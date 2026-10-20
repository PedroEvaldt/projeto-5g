#!/usr/bin/env python3
"""
photon_clock: kernel timestamps and health counters.

Shared by the server and the client. Nothing here generates traffic. It
only exists so the numbers the two programs print describe the network,
and not the machine that did the measuring.

Why kernel timestamps. The latencies we are after are milliseconds, and a
timestamp taken in Python (after recvmsg returns) also carries the time
the process spent waiting for a CPU. On the box I ran this on, which hosts
the 5G core, both UPFs, the RAN simulator and the generator itself, that
wait is not noise, and it grows with load. Which is exactly where the
measurement has to hold up. Without kernel timestamps you get a "network
latency" that gets worse whenever the CPU gets busy, and the report ends
up blaming the UPF for something the scheduler did.

SO_TIMESTAMPNS fixes that: the kernel stamps the packet when it arrives
and gives the stamp back as ancillary data on recvmsg. Whatever happens
between that stamp and our Python code stops counting.

The catch: the measurement clock becomes CLOCK_REALTIME, because that is
what the kernel uses for software timestamps, instead of perf_counter.
Both ends of every measurement live in the same process, so there is no
clock sync between machines involved. But an NTP step in the middle of a
run would shift the samples, and that is what clock_drift_us() is for.

On a platform without support (macOS, older kernels) everything falls back
to a Python timestamp. It still runs, it just measures worse, and both
programs say so in the banner they print at startup.
"""
import socket
import struct
import time

# Numbers from asm-generic/socket.h, which is what x86_64, arm64 and riscv
# use. mips, parisc, sparc and alpha have their own values, so ask the
# socket module first and only fall back to the literal.
SO_TIMESTAMPNS = getattr(socket, "SO_TIMESTAMPNS", 35)
SCM_TIMESTAMPNS = SO_TIMESTAMPNS
SO_RXQ_OVFL = getattr(socket, "SO_RXQ_OVFL", 40)
SCM_RXQ_OVFL = SO_RXQ_OVFL

CLOCK_REALTIME = getattr(time, "CLOCK_REALTIME", 0)


def now_ns():
    """The measurement clock. Same one the kernel uses for SO_TIMESTAMPNS."""
    return time.clock_gettime_ns(CLOCK_REALTIME)


def clock_drift_us():
    """Returns (realtime_ns, monotonic_ns).

    Call it at the start and at the end of a run. The difference between
    the two deltas is how much CLOCK_REALTIME moved beyond the monotonic
    clock. Zero means no NTP adjustment happened. A few milliseconds means
    the samples of that run are contaminated and the point is not usable.
    """
    return now_ns(), time.monotonic_ns()


def enable(sock, rxq_ovfl=True):
    """Turn on the kernel timestamp, and optionally the receive-queue
    overflow counter. Returns (has_timestamp, has_overflow)."""
    ts = ovfl = False
    try:
        sock.setsockopt(socket.SOL_SOCKET, SO_TIMESTAMPNS, 1)
        ts = True
    except OSError:
        pass
    if rxq_ovfl:
        try:
            sock.setsockopt(socket.SOL_SOCKET, SO_RXQ_OVFL, 1)
            ovfl = True
        except OSError:
            pass
    return ts, ovfl


def ancbufsize():
    """Room for both cmsgs: a 16 B timespec and a 4 B counter."""
    try:
        return socket.CMSG_SPACE(16) + socket.CMSG_SPACE(4)
    except AttributeError:
        return 128


def read_ancdata(ancdata):
    """Pull (kernel_ns, overflow_count) out of the recvmsg ancillary data.
    Either field comes back as None when it was not there."""
    t_ns = ovfl = None
    for level, ctype, data in ancdata:
        if level != socket.SOL_SOCKET:
            continue
        if ctype == SCM_TIMESTAMPNS:
            # struct timespec is two native words: 16 B on 64-bit, 8 B on
            # 32-bit. The size of the cmsg tells us which one we got.
            if len(data) >= 16:
                s, ns = struct.unpack_from("@qq", data)
            elif len(data) >= 8:
                s, ns = struct.unpack_from("@ii", data)
            else:
                continue
            t_ns = s * 1_000_000_000 + ns
        elif ctype == SCM_RXQ_OVFL and len(data) >= 4:
            ovfl = struct.unpack_from("@I", data)[0]
    return t_ns, ovfl


def recv_timeout(sock, seconds):
    """Receive timeout handled by the kernel instead of sock.settimeout().

    settimeout() switches the socket into CPython's "timeout" mode, which
    calls poll() before every recvmsg. That is one extra syscall per packet
    in the hottest loop we have. With SO_RCVTIMEO the socket stays blocking
    as far as CPython is concerned and the kernel returns EAGAIN on its
    own, which reaches us as BlockingIOError.
    """
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVTIMEO,
                        struct.pack("@qq", int(seconds),
                                    int((seconds % 1) * 1e6)))
        return True
    except OSError:
        sock.settimeout(seconds)
        return False


def buffers(sock, wanted):
    """Ask for SO_RCVBUF/SO_SNDBUF and return what the kernel actually gave.

    The kernel clamps the request to net.core.rmem_max / wmem_max (208 kB
    by default) and says nothing about it, so asking for 8 MB and getting
    416 kB is completely silent. Those two sysctls are global, they are not
    per network namespace, so raising them is the node's job and cannot be
    done from inside a pod. Returning the effective value is what lets the
    run banner show the clamp instead of hiding it.
    """
    for opt in (socket.SO_RCVBUF, socket.SO_SNDBUF):
        try:
            sock.setsockopt(socket.SOL_SOCKET, opt, wanted)
        except OSError:
            pass
    return (sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF),
            sock.getsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF))


def udp_errors():
    """UDP RcvbufErrors/InErrors from /proc/net/snmp.

    These are for the whole network namespace, not for one socket. Inside a
    RAN simulator pod that also covers the gNB and UE sockets, which share
    the netns: if the overflow shows up here but not on our own socket, the
    loss happened inside the simulator and not on the path under test.
    """
    try:
        with open("/proc/net/snmp") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return {}
    header = None
    for line in lines:
        if line.startswith("Udp:"):
            fields = line.split()[1:]
            if header is None:
                header = fields
            else:
                return {k: int(v) for k, v in zip(header, fields)}
    return {}


def udp_drops(port):
    """Queue-full drops for one socket, from the last column of
    /proc/net/udp (the one the kernel calls "drops").

    This duplicates SO_RXQ_OVFL on purpose. The cmsg never showed up on my
    first runs even though the kernel accepted the sockopt, and a counter
    that only sometimes appears is no good for deciding whether a
    measurement is valid. /proc is always there. The port is whatever
    bind() picked, so read it back with sock.getsockname()[1].

    Returns None when the line is not found. The caller takes the delta
    between two reads.
    """
    target = f":{port:04X}"
    try:
        with open("/proc/net/udp") as fh:
            next(fh, None)          # header line
            for line in fh:
                fields = line.split()
                if len(fields) >= 13 and fields[1].endswith(target):
                    return int(fields[-1])
    except (OSError, ValueError):
        pass
    return None


def ctxt_switches():
    """Involuntary context switches of this process: how many times the
    scheduler took it off the CPU without it asking. If this number blows
    up during a run, the machine was contended and the latency you measured
    has CPU time baked into it."""
    try:
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith("nonvoluntary_ctxt_switches:"):
                    return int(line.split()[1])
    except OSError:
        pass
    return None
