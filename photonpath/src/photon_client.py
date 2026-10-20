#!/usr/bin/env python3
"""
photon_client: the UE side of PhotonPath.

Runs on the device (in my testbed, inside the RAN simulator pod, bound to
the IP of the PDU session tunnel). It sends the pose stream, consumes the
frame stream, and measures per frame:

    m2p_us   motion-to-photon: from the pose sample until the complete
             frame has arrived
    hold_us  how long that pose sat inside the server: the wait for the
             next render instant (cadence plus pacing) plus the render
             itself (render_us)
    net_us   m2p minus hold: the part that depends on where the traffic
             leaves the user plane

Everything is measured with this client's own clock, out and back, so
there is nothing to synchronise between the UE and the server.

Kernel timestamps. Every packet's arrival is dated by the KERNEL
(SO_TIMESTAMPNS), not by Python. Without that, the time this process
spends waiting for a CPU lands inside m2p and, since it is not in hold,
comes out reported as "network delay". The error grows with load, which is
exactly where the measurement needs to be trustworthy. See
photon_clock.py. The price is that the measurement clock becomes
CLOCK_REALTIME, so clock_drift_us in the summary is there to expose an NTP
step in the middle of a run.

Where the loss happened. dl_sock_drops and dl_sock_overflow_pkts are
packets that arrived and the KERNEL threw away because this socket's queue
was full. That is loss inside this client, not loss on the path. If they
are not zero, the point does not measure the network, it measures Python.
udp_rcvbuf_errors does the same for the whole network namespace, which in
a RAN simulator pod also covers the gNB and UE sockets: an overflow there
means the loss happened inside the simulator.

One window. Latency, loss and throughput are all computed over the same
post-warmup window. Mixing them (loss over the whole run, latency only
after warmup) used to contaminate loss with the PDU session setup.

Output: one CSV row per frame (for plots) plus a JSON summary on stdout
(for a campaign script to aggregate).

    python3 photon_client.py --server edge-app:9100 --bind 10.60.0.1 \
        --duration 60 --csv /tmp/edge.csv --label edge-run
"""
import argparse
import json
import os
import random
import socket
import struct
import sys
import threading
import time

import photon_clock

POSE_MAGIC = b"PPOS"
FRAME_MAGIC = b"PPFR"
POSE_HDR = struct.Struct("!4sIQQI")
FRAME_HDR = struct.Struct("!4sIQHHQQIII")
FLAG_BYE = 1


def pct(values, q):
    if not values:
        return None
    k = (len(values) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


class Window:
    """Counters for one stretch of the run. There are two of them: the
    whole run, and the measurement window (post-warmup). The summary
    reports the second one, because that is the same slice the latency
    percentiles come from."""

    __slots__ = ("rx_packets", "rx_bytes", "first_seq", "last_seq",
                 "t_first_ns", "t_last_ns", "ovfl_start", "ovfl_end")

    def __init__(self):
        self.rx_packets = 0
        self.rx_bytes = 0
        self.first_seq = None
        self.last_seq = 0
        self.t_first_ns = None
        self.t_last_ns = None
        self.ovfl_start = None
        self.ovfl_end = None

    def add(self, fseq, nbytes, t_ns, ovfl):
        self.rx_packets += 1
        self.rx_bytes += nbytes
        if self.first_seq is None:
            self.first_seq = fseq
            self.t_first_ns = t_ns
            self.ovfl_start = ovfl
        self.last_seq = max(self.last_seq, fseq)
        self.t_last_ns = t_ns
        if ovfl is not None:
            self.ovfl_end = ovfl

    @property
    def span_s(self):
        if self.t_first_ns is None or self.t_last_ns is None:
            return 0.0
        return (self.t_last_ns - self.t_first_ns) / 1e9

    @property
    def frames_expected(self):
        if self.first_seq is None:
            return 0
        return self.last_seq - self.first_seq + 1

    @property
    def overflow(self):
        if self.ovfl_start is None or self.ovfl_end is None:
            return None
        return max(0, self.ovfl_end - self.ovfl_start)


class Client:
    def __init__(self, args):
        self.args = args
        self.sid = args.session or random.getrandbits(31)
        self.running = True
        self.samples = []            # one dict per complete frame
        self.frames = {}             # frame_seq -> partial state
        self.lock = threading.Lock()
        self.whole_run = Window()
        self.window = Window()
        self.tx_packets = 0
        self.tx_bytes = 0
        self.frames_partial = 0
        self.pkt_cnt = 0
        self.t0_ns = None            # arrival of the first downlink packet
        self.t_window_ns = None      # start of the post-warmup window
        self.cutoff_seq = -1         # highest frame_seq seen before the cut
        self.in_window = False
        self.tx_span = 0.0           # how long the uplink actually ran
        self.lport_real = 0

        if args.pose_hz <= 0:
            sys.exit("--pose-hz must be greater than zero")
        host, _, port = args.server.rpartition(":")
        # Resolve once. sendto() with a hostname would do a DNS lookup per
        # packet. In Kubernetes the name comes from CoreDNS, so this is the
        # ClusterIP the cluster allocated and there is no literal IP
        # hardcoded anywhere.
        try:
            ip = socket.gethostbyname(host)
        except socket.gaierror as e:
            sys.exit(f"could not resolve '{host}': {e}")
        self.dst = (ip, int(port))
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.rcvbuf, _ = photon_clock.buffers(self.sock, 8 << 20)
        self.ts_kernel, self.has_ovfl = photon_clock.enable(self.sock)
        self.sock.bind((args.bind, args.lport))
        # Kernel timeout rather than sock.settimeout(), see photon_clock.
        photon_clock.recv_timeout(self.sock, 0.2)
        self.ancbuf = photon_clock.ancbufsize()
        self.rxbuf = bytearray(2048)

    # ------------------------------------------------------------------ TX
    def tx_loop(self):
        a = self.args
        period = 1.0 / a.pose_hz
        pad = bytes(max(0, a.pose_bytes - POSE_HDR.size))
        seq = 0
        next_t = start = time.perf_counter()
        end = next_t + a.duration
        while self.running and time.perf_counter() < end:
            next_t += period
            seq += 1
            pkt = POSE_HDR.pack(POSE_MAGIC, self.sid, seq,
                                photon_clock.now_ns(), 0) + pad
            try:
                self.sock.sendto(pkt, self.dst)
                self.tx_packets += 1
                self.tx_bytes += len(pkt)
            except OSError as exc:
                print(f"send error: {exc}", file=sys.stderr)
            sleep = next_t - time.perf_counter()
            if sleep > 0:
                time.sleep(sleep)
            else:
                next_t = time.perf_counter()
        self.tx_span = time.perf_counter() - start
        # Close the session so the server stops sending frames.
        try:
            self.sock.sendto(POSE_HDR.pack(POSE_MAGIC, self.sid, seq + 1,
                                           photon_clock.now_ns(), FLAG_BYE),
                             self.dst)
        except OSError:
            pass
        self.running = False

    # ------------------------------------------------------------------ RX
    def rx_loop(self):
        recv_into = self.sock.recvmsg_into
        view = [memoryview(self.rxbuf)]
        buf = self.rxbuf
        warmup_ns = int(self.args.warmup * 1e9)
        while self.running:
            try:
                n, ancdata, _, _ = recv_into(view, self.ancbuf)
            except (BlockingIOError, socket.timeout):
                continue
            except OSError:
                return
            if n < FRAME_HDR.size:
                continue
            (magic, sid, fseq, idx, cnt, pose_seq, t_pose_ns,
             hold_us, render_us, ai_us) = FRAME_HDR.unpack_from(buf)
            if magic != FRAME_MAGIC or sid != self.sid:
                continue
            if cnt < 1:
                continue

            k_ns, ovfl = (photon_clock.read_ancdata(ancdata) if ancdata
                          else (None, None))
            now_ns = k_ns if k_ns is not None else photon_clock.now_ns()
            if self.t0_ns is None:
                self.t0_ns = now_ns
                self.t_window_ns = now_ns + warmup_ns
            self.pkt_cnt = cnt
            self.whole_run.add(fseq, n, now_ns, ovfl)
            # The measurement window starts at the first frame that is
            # entirely inside it, not at the first packet after the cut. A
            # frame straddling the cut would have some of its packets
            # counted outside and all of its expected packets counted
            # inside, and the difference would come out as loss. On a 4 s
            # test with 44 packets per frame that alone invented 0.08% of
            # loss that did not exist.
            if not self.in_window:
                if now_ns < self.t_window_ns:
                    self.cutoff_seq = max(self.cutoff_seq, fseq)
                elif fseq > self.cutoff_seq:
                    self.in_window = True
            if self.in_window:
                self.window.add(fseq, n, now_ns, ovfl)

            st = self.frames.get(fseq)
            if st is None:
                # st[0] is a BITMASK of the idx values seen so far, not a
                # count. With a count, a duplicated packet would complete a
                # frame that is still missing a piece, and that frame's m2p
                # would be measured too early.
                st = self.frames[fseq] = [0, cnt, t_pose_ns, hold_us,
                                          render_us, ai_us, pose_seq]
            if idx < st[1]:
                st[0] |= 1 << idx
            if st[0] == (1 << st[1]) - 1:
                del self.frames[fseq]
                # hold of the packet that COMPLETED the frame. Its arrival
                # is what defines m2p, so its time-in-server is the one that
                # has to come out of net_us. With pacing on, the packets of
                # a frame leave at different instants and this is the only
                # hold that matches the instant we measured.
                st[3] = hold_us
                self.record(fseq, now_ns, st)
            # Old frames that never completed count as loss.
            if len(self.frames) > 64:
                for old in sorted(self.frames)[:-32]:
                    self.frames.pop(old, None)
                    self.frames_partial += 1

    def record(self, fseq, now_ns, st):
        _, cnt, t_pose_ns, hold_us, render_us, ai_us, pose_seq = st
        m2p_us = (now_ns - t_pose_ns) / 1000.0
        # hold already includes the render, measured at send time.
        net_us = m2p_us - hold_us
        t_rel = (now_ns - self.t0_ns) / 1e9
        with self.lock:
            self.samples.append({
                "t_s": round(t_rel, 6),
                "frame_seq": fseq,
                "pose_seq": pose_seq,
                "m2p_us": round(m2p_us, 1),
                "net_us": round(net_us, 1),
                "hold_us": hold_us,
                "render_us": render_us,
                "ai_us": ai_us,
                "pkts": cnt,
            })

    # ------------------------------------------------------------- summary
    def summary(self, wall_s, health):
        a = self.args
        # Same slice the loss and throughput counters use: the window opens
        # at the first whole frame after warmup. Filtering latency by time
        # and the counters by frame would leave the boundary frame inside
        # one and outside the other. It is a small difference, but the text
        # presents both numbers as coming from the same slice.
        cut = self.window.first_seq
        with self.lock:
            kept = ([] if cut is None
                    else [s for s in self.samples if s["frame_seq"] >= cut])
        m2p = sorted(s["m2p_us"] for s in kept)
        net = sorted(s["net_us"] for s in kept)
        hold = sorted(float(s["hold_us"]) for s in kept)

        # RFC 3550 style jitter, over the time series rather than the
        # sorted values. Computed on both metrics: the m2p one includes the
        # render cadence (it moves with fps), the net one is what the
        # network actually did.
        def rfc3550(key):
            j, prev = 0.0, None
            for smp in kept:
                if prev is not None:
                    j += (abs(smp[key] - prev) - j) / 16.0
                prev = smp[key]
            return round(j, 1)

        # Loss and throughput over the SAME window as the latency
        # percentiles. Both are still client-side estimates: whole frames
        # lost at the edges of the window shrink the denominator, so this
        # loss is a floor. The exact figure needs the server's
        # frame_packets_total, which is what a campaign script should pair
        # this with.
        w = self.window
        frames_expected = w.frames_expected
        pkts_expected = frames_expected * (self.pkt_cnt or 1)
        loss = 0.0
        if pkts_expected:
            loss = max(0.0, 1 - w.rx_packets / pkts_expected) * 100

        # Throughput over the window we ASKED for (the time the session was
        # alive and the downlink should have been arriving), not over the
        # interval in which it actually arrived.
        #
        # Dividing by the observed interval hides the worst failure this
        # test can have: a flow that DIES mid-run comes out with nominal
        # throughput and zero loss, because the denominator shrinks along
        # with the numerator. That is exactly what happened on my first run
        # on the testbed. The downlink on the remote path stopped at 33 s
        # and the summary said "6.34 Mbps, loss 0.000%".
        #
        # dl_continuity_pct is the other half of that: how much of the
        # requested window had traffic in it. Below 100% means the flow was
        # interrupted, which is a different thing from packet loss and
        # needs its own number.
        nominal_window = max(0.0, (self.tx_span or wall_s) - a.warmup)
        dl_eff = max(1e-9, nominal_window or w.span_s or wall_s)
        ul_eff = max(1e-9, self.tx_span or wall_s)
        complete = len(self.samples)
        total_frames = complete + self.frames_partial
        return {
            "label": a.label,
            "server": a.server,
            "bind": a.bind,
            "duration_s": round(wall_s, 3),
            "ul_window_s": round(self.tx_span, 3),
            "warmup_s": a.warmup,
            "pose_hz": a.pose_hz,
            # Totals for the WHOLE run, warmup included.
            "frames_complete": complete,
            "frames_partial": self.frames_partial,
            # Whole-run total, to pair with the server's
            # frame_packets_total. That pair is where the exact loss comes
            # from; the client's own figure is a floor, because it cannot
            # see a frame that was lost entirely.
            "dl_packets_rx_run": self.whole_run.rx_packets,
            "frames_incomplete_pct": (
                round(self.frames_partial / total_frames * 100, 3)
                if total_frames else 0.0),
            # KPIs, all from the measurement window.
            "frames_in_stats": len(kept),
            "dl_packet_loss_pct": round(loss, 4),
            "dl_throughput_mbps": round(w.rx_bytes * 8 / dl_eff / 1e6, 3),
            # Fraction of the requested window that had downlink traffic.
            # Below 100% means the flow was interrupted. Packet loss does
            # not see this, because a server that STOPS sending leaves no
            # hole in the sequence.
            "dl_continuity_pct": round(min(100.0, w.span_s / dl_eff * 100), 2),
            "ul_packets_tx": self.tx_packets,
            "ul_pose_hz_real": round(self.tx_packets / ul_eff, 1),
            "ul_throughput_mbps": round(self.tx_bytes * 8 / ul_eff / 1e6, 3),
            # The raw counters the loss and throughput KPIs come from. They
            # sit in their own block so it is never ambiguous what is
            # window and what is whole run. Mixing the two at the same
            # level is what used to put "frames_expected" and
            # "frames_complete" side by side with different slices.
            "window": {
                "start_s": a.warmup,
                "nominal_s": round(nominal_window, 3),
                "span_s": round(w.span_s, 3),
                "frames_expected": frames_expected,
                "dl_packets_rx": w.rx_packets,
                "dl_packets_expected": pkts_expected,
            },
            "m2p_us": {
                "min": pct(m2p, 0), "p50": pct(m2p, 0.50),
                "p95": pct(m2p, 0.95), "p99": pct(m2p, 0.99),
                "max": pct(m2p, 1.0),
                "mean": round(sum(m2p) / len(m2p), 1) if m2p else None,
            },
            "net_rtt_us": {
                "min": pct(net, 0), "p50": pct(net, 0.50),
                "p95": pct(net, 0.95), "p99": pct(net, 0.99),
                "max": pct(net, 1.0),
                "mean": round(sum(net) / len(net), 1) if net else None,
            },
            # hold = time inside the server (wait for the next render
            # instant, plus the pacing window, plus the render). Reported
            # because it is the only way for a reader to check the
            # decomposition m2p = net + hold, and to see how much of m2p is
            # a choice made by the generator rather than a property of the
            # path.
            "hold_us": {
                "min": pct(hold, 0), "p50": pct(hold, 0.50),
                "p95": pct(hold, 0.95), "max": pct(hold, 1.0),
                "mean": round(sum(hold) / len(hold), 1) if hold else None,
            },
            "jitter_us": rfc3550("m2p_us"),
            "jitter_net_us": rfc3550("net_us"),
            # Everything below answers "is this run usable?". None of it
            # describes the network. It describes whether the instrument
            # held up.
            "health": health,
        }

    def health(self, snmp0, ctx0, drift0, drops0):
        """Evidence that the run measured the network and not this client."""
        snmp1 = photon_clock.udp_errors()
        ctx1 = photon_clock.ctxt_switches()
        rt1, mono1 = photon_clock.clock_drift_us()
        rt0, mono0 = drift0
        drops1 = photon_clock.udp_drops(self.lport_real)

        def d(k):
            if not snmp0 or not snmp1 or k not in snmp0 or k not in snmp1:
                return None
            return snmp1[k] - snmp0[k]

        return {
            "rx_timestamp_kernel": self.ts_kernel,
            "sock_rcvbuf_bytes": self.rcvbuf,
            # Packets that arrived and the kernel dropped because THIS
            # socket's queue was full: our loss, not the path's. /proc is
            # the reliable source (dl_sock_drops); the SO_RXQ_OVFL cmsg is
            # kept as confirmation, since it does not always show up.
            "dl_sock_drops": (None if drops0 is None or drops1 is None
                              else drops1 - drops0),
            "dl_sock_overflow_pkts": (self.window.overflow if self.has_ovfl
                                      else None),
            "dl_sock_overflow_run": (self.whole_run.overflow if self.has_ovfl
                                     else None),
            # Whole network namespace. In a RAN simulator pod that includes
            # the gNB and UE sockets.
            "udp_rcvbuf_errors": d("RcvbufErrors"),
            "udp_in_errors": d("InErrors"),
            # Times the scheduler took this process off the CPU without it
            # asking.
            "ctxt_switches_involuntary": (
                None if ctx0 is None or ctx1 is None else ctx1 - ctx0),
            # How far CLOCK_REALTIME moved beyond the monotonic clock during
            # the run. A few ms means NTP stepped and the m2p samples of
            # that stretch are shifted.
            "clock_drift_us": round(((rt1 - rt0) - (mono1 - mono0)) / 1000.0,
                                    1),
        }

    def flush_partials(self):
        """Frames still incomplete when the run ends never went through the
        64-frame pruning, and without this they vanished from the books:
        the summary would say "frames_partial: 0" on a test that lost
        packets from start to finish."""
        self.frames_partial += len(self.frames)
        self.frames.clear()

    def write_csv(self, path):
        with self.lock:
            rows = list(self.samples)
        cols = ["t_s", "frame_seq", "pose_seq", "m2p_us", "net_us", "hold_us",
                "render_us", "ai_us", "pkts"]
        with open(path, "w") as fh:
            fh.write("label," + ",".join(cols) + "\n")
            for r in rows:
                fh.write(self.args.label + "," +
                         ",".join(str(r[c]) for c in cols) + "\n")

    def run(self):
        a = self.args
        print(f"photon-client sid={self.sid} {a.bind}:{a.lport} -> {a.server} "
              f"pose={a.pose_hz} Hz for {a.duration}s "
              f"rcvbuf={self.rcvbuf // 1024} kB "
              f"kernel_stamp={'yes' if self.ts_kernel else 'NO'}",
              file=sys.stderr)
        if self.rcvbuf < 4 << 20:
            print("WARNING: the kernel clamped the rcvbuf we asked for (8 MB) "
                  f"down to {self.rcvbuf // 1024} kB. Raise net.core.rmem_max "
                  "ON THE NODE. It is a global sysctl, there is no per "
                  "namespace version, and you cannot set it from inside the "
                  "pod.", file=sys.stderr)
        self.lport_real = self.sock.getsockname()[1]
        snmp0 = photon_clock.udp_errors()
        ctx0 = photon_clock.ctxt_switches()
        drift0 = photon_clock.clock_drift_us()
        drops0 = photon_clock.udp_drops(self.lport_real)

        rx = threading.Thread(target=self.rx_loop, daemon=True)
        rx.start()
        t0 = time.perf_counter()
        try:
            self.tx_loop()
        except KeyboardInterrupt:
            self.running = False
        time.sleep(0.5)             # let the last frames in flight land
        self.running = False
        rx.join(timeout=1.0)
        wall = time.perf_counter() - t0
        self.flush_partials()

        if a.csv:
            self.write_csv(a.csv)
            print(f"csv: {a.csv}", file=sys.stderr)
        s = self.summary(wall, self.health(snmp0, ctx0, drift0, drops0))
        out = json.dumps(s, indent=2)
        if a.json:
            with open(a.json, "w") as fh:
                fh.write(out + "\n")
        print(out)
        if not s["frames_complete"]:
            print("NO complete frame received. Check the tunnel route and "
                  "whether the server is reachable from the UE address.",
                  file=sys.stderr)
            return 1
        ov = s["health"]["dl_sock_drops"] or s["health"]["dl_sock_overflow_pkts"]
        if ov:
            print(f"WARNING: the kernel dropped {ov} packets because THIS "
                  "socket's queue was full. The loss in this run belongs to "
                  "the client, not to the path under test, so the point does "
                  "not measure the network.", file=sys.stderr)
        return 0


def envd(name, default, cast=str):
    v = os.environ.get(name)
    return cast(v) if v not in (None, "") else default


def parse_args(argv):
    p = argparse.ArgumentParser(description="PhotonPath client (UE side)")
    p.add_argument("--server", default=envd("PP_SERVER", "edge-app:9100"),
                   help="host:port of the photon server (a Kubernetes "
                        "Service name or an IP); resolved once at startup")
    p.add_argument("--bind", default=envd("PP_BIND", "0.0.0.0"),
                   help="UE address (the tunnel interface), so the traffic "
                        "goes out through the PDU session")
    p.add_argument("--lport", type=int, default=envd("PP_LPORT", 0, int))
    p.add_argument("--duration", type=float,
                   default=envd("PP_DURATION", 60.0, float))
    p.add_argument("--warmup", type=float,
                   default=envd("PP_WARMUP", 5.0, float),
                   help="initial seconds dropped from the summary (from ALL "
                        "the metrics: latency, loss and throughput)")
    p.add_argument("--pose-hz", type=float,
                   default=envd("PP_POSE_HZ", 250.0, float))
    p.add_argument("--pose-bytes", type=int,
                   default=envd("PP_POSE_BYTES", 128, int))
    p.add_argument("--session", type=int, default=envd("PP_SESSION", 0, int))
    p.add_argument("--csv", default=envd("PP_CSV", ""))
    p.add_argument("--json", default=envd("PP_JSON", ""))
    p.add_argument("--label", default=envd("PP_LABEL", "run"))
    return p.parse_args(argv)


if __name__ == "__main__":
    sys.exit(Client(parse_args(sys.argv[1:])).run())
