#!/usr/bin/env python3
"""
photon_server: the application side of PhotonPath.

Runs wherever you want to measure to. In my setup that is either an edge
host next to the local breakout, or a server in the central data network,
using the exact same configuration for both so the only difference between
the two paths is where the traffic leaves the user plane.

It generates a VR-like bidirectional load: a heavy downlink of rendered
"frames", closed in a loop with a light and frequent uplink of pose and
control packets.

    UE  --- pose  (128 B at 250 Hz, uplink)   --->  server
    UE  <-- frame (~12 kB at 60-90 fps, DL)   ----  server

Every frame carries back the timestamp of the last pose we received, so
the client measures motion-to-photon with its own clock. No clock sync
between the UE and the server is needed, which matters because there is no
trustworthy NTP between the nodes of my testbed, and there probably is not
between yours either.

Each frame also says how long that pose sat inside this process: hold_us,
which is the wait for the next render instant plus the render itself
(render_us goes out separately). With that the client can split two things
that usually get lumped together:

    motion-to-photon = uplink + hold + downlink
    network RTT      = motion-to-photon - hold

The first is the application KPI. The second is the one that moves when
the user plane anchor moves. Measuring both is what lets you say how much
of the QoE gain actually came from the path, and how much was the
application's own frame cadence.

Two design decisions worth knowing about before you compare these numbers
with another generator:

1. Frames are PACED inside the period, in small bursts, rather than being
   dumped as one big burst. A 50 Mbps frame at 60 fps is 87 packets
   (~104 kB), and as a single burst those leave at veth speed, a few Gbps
   instantaneous. No queue on the path absorbs that (the client's receive
   queue, the RAN simulator's sockets), and whatever overflows gets
   reported as "loss" when it was really just burstiness. Paced, the same
   frame asks for a queue roughly 10x smaller, which is what let me push
   the bitrate up at all. The cost is that the last packet's hold now
   includes the pacing window, so m2p goes up. It goes up by the same
   amount on every path, and net RTT (which subtracts hold) does not move.
   --no-spread turns it off.

2. The pose receive timestamp comes from the KERNEL (SO_TIMESTAMPNS), not
   from Python. Without it, the time this process spends off the CPU lands
   in the wrong hold and leaks into the client's net RTT as if it were
   network time. That error grows with load, which is precisely where you
   need the measurement to be right. See photon_clock.py.

Prometheus metrics on /metrics (port 9101 by default). They include the
generator's own pacing error, the packets it failed to hand to the socket
and the effective buffer sizes. If any of those move, the bottleneck is
this process and not the network, and that data point should be dropped.

Typical use:
    python3 photon_server.py --bitrate-mbps 50 --fps 90

Every parameter also reads an environment variable (PP_BITRATE_MBPS,
PP_FPS, ...), so you can change profiles with `kubectl set env` without
rebuilding anything.
"""
import argparse
import math
import os
import socket
import struct
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import photon_clock

POSE_MAGIC = b"PPOS"
FRAME_MAGIC = b"PPFR"

# magic, session, seq, t_client_ns, flags
POSE_HDR = struct.Struct("!4sIQQI")
# magic, session, frame_seq, pkt_idx, pkt_cnt, pose_seq, t_pose_ns,
# hold_us, render_us, ai_us
FRAME_HDR = struct.Struct("!4sIQHHQQIII")

FLAG_BYE = 1
MSG_DONTWAIT = getattr(socket, "MSG_DONTWAIT", 0x40)


class Metrics:
    """Plain counters. The Prometheus text format is simple enough that
    pulling a client library into the image would not pay for itself."""

    def __init__(self):
        self.lock = threading.Lock()
        self.c = {
            "pose_packets_total": 0,
            "pose_bytes_total": 0,
            "pose_lost_total": 0,
            "pose_reordered_total": 0,
            "frames_sent_total": 0,
            "frame_packets_total": 0,
            "frame_bytes_total": 0,
            "frames_late_total": 0,
            "frames_truncated_total": 0,
            "frames_reanchored_total": 0,
            "send_dropped_total": 0,
            "send_errors_total": 0,
            "sessions_started_total": 0,
            "sessions_expired_total": 0,
            "ai_requests_total": 0,
            "ai_failures_total": 0,
        }
        self.g = {
            "sessions_active": 0,
            "pacing_error_seconds": 0.0,
            "render_seconds": 0.0,
            "ai_response_seconds": 0.0,
            "sock_rcvbuf_bytes": 0,
            "sock_sndbuf_bytes": 0,
            "kernel_rx_timestamp": 0,
            "pace_chunk_packets": 0,
            "pace_window_seconds": 0.0,
        }

    def add(self, key, value=1):
        with self.lock:
            self.c[key] += value

    def set(self, key, value):
        with self.lock:
            self.g[key] = value

    def render(self, labels):
        lbl = ",".join(f'{k}="{v}"' for k, v in labels.items())
        lbl = "{" + lbl + "}" if lbl else ""
        out = []
        with self.lock:
            for k, v in self.c.items():
                out.append(f"# TYPE photonpath_server_{k} counter")
                out.append(f"photonpath_server_{k}{lbl} {v}")
            for k, v in self.g.items():
                out.append(f"# TYPE photonpath_server_{k} gauge")
                out.append(f"photonpath_server_{k}{lbl} {v}")
        return "\n".join(out) + "\n"


class Session:
    __slots__ = ("sid", "addr", "seq", "t_pose_ns", "recv_ns", "frame_seq",
                 "started_ns")

    def __init__(self, sid, addr, now_ns):
        self.sid = sid
        self.addr = addr
        self.seq = 0
        self.t_pose_ns = 0
        self.recv_ns = now_ns
        self.frame_seq = 0
        self.started_ns = now_ns


class Server:
    def __init__(self, args):
        self.args = args
        self.metrics = Metrics()
        self.sessions = {}          # addr -> Session
        self.lock = threading.Lock()
        self.running = True
        self.ai_us = 0

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.rcvbuf, self.sndbuf = photon_clock.buffers(self.sock, 8 << 20)
        self.ts_kernel, _ = photon_clock.enable(self.sock, rxq_ovfl=False)
        self.sock.bind((args.bind, args.port))
        photon_clock.recv_timeout(self.sock, 0.2)
        self.ancbuf = photon_clock.ancbufsize()
        self.rxbuf = bytearray(2048)
        self.metrics.set("sock_rcvbuf_bytes", self.rcvbuf)
        self.metrics.set("sock_sndbuf_bytes", self.sndbuf)
        self.metrics.set("kernel_rx_timestamp", 1 if self.ts_kernel else 0)

        self.payload_size = args.mtu - FRAME_HDR.size
        if self.payload_size < 1:
            sys.exit(f"--mtu {args.mtu} is smaller than the header "
                     f"({FRAME_HDR.size} B)")
        if args.fps <= 0:
            sys.exit("--fps must be greater than zero")
        if args.bitrate_mbps <= 0:
            sys.exit("--bitrate-mbps must be greater than zero")
        frame_bytes = args.bitrate_mbps * 1e6 / 8.0 / args.fps
        self.pkt_cnt = max(1, math.ceil(frame_bytes / args.mtu))
        if self.pkt_cnt > 0xFFFF:
            sys.exit("frame too big: more than 65535 packets per frame")

        # Pacing. The frame leaves as bursts of `chunk` packets, one burst
        # every `gap` seconds, covering pace_duty of the period.
        #
        # chunk is deliberately not 1. With 174 packets inside a 16.7 ms
        # period the gap between packets would be 77 us, below what
        # time.sleep() on Linux actually delivers, and the generator would
        # start overshooting the period. That just trades a burst problem
        # for a cadence problem. Grouping packets keeps the number of
        # sleeps bounded and each sleep above the floor.
        period = 1.0 / args.fps
        self.budget = period * args.pace_duty
        gap_min = max(1e-4, args.pace_min_gap_ms / 1000.0)
        slots = max(1, int(self.budget / gap_min))
        self.chunk = max(1, math.ceil(self.pkt_cnt / slots))
        self.n_chunks = math.ceil(self.pkt_cnt / self.chunk)
        self.gap = self.budget / self.n_chunks if self.n_chunks else 0.0
        paced = args.spread and self.n_chunks > 1
        self.metrics.set("pace_chunk_packets", self.chunk if paced else 0)
        # Exported because it ends up inside hold, and therefore inside
        # m2p. Record it next to your results, otherwise the inflated m2p
        # has no explanation when you go back to the numbers.
        self.metrics.set("pace_window_seconds",
                         round(self.gap * (self.n_chunks - 1), 6)
                         if paced else 0.0)

        # One buffer, reused. Allocating ~70 kB per frame at 90 fps is not
        # free.
        self.buf = bytearray(args.mtu)
        self.view = memoryview(self.buf)
        # Non-zero payload pattern, since compression and offloads love
        # runs of zeros a little too much.
        for i in range(FRAME_HDR.size, args.mtu):
            self.buf[i] = i & 0xFF

    # ------------------------------------------------------------------ RX
    def rx_loop(self):
        recv_into = self.sock.recvmsg_into
        view = [memoryview(self.rxbuf)]
        while self.running:
            try:
                n, ancdata, _, addr = recv_into(view, self.ancbuf)
            except (BlockingIOError, socket.timeout):
                continue        # SO_RCVTIMEO fired, just re-read self.running
            except OSError:
                if self.running:
                    continue
                return
            if n < POSE_HDR.size:
                continue
            magic, sid, seq, t_ns, flags = POSE_HDR.unpack_from(self.rxbuf)
            if magic != POSE_MAGIC:
                continue

            # Kernel stamp. Whatever this process takes between the packet
            # arriving and this line must not go into hold, otherwise it
            # leaves m2p and reappears as network time in the client's
            # net RTT.
            k_ns, _ = (photon_clock.read_ancdata(ancdata) if ancdata
                       else (None, None))
            now_ns = k_ns if k_ns is not None else photon_clock.now_ns()
            self.metrics.add("pose_packets_total")
            self.metrics.add("pose_bytes_total", n)

            with self.lock:
                s = self.sessions.get(addr)
                if s is None or s.sid != sid:
                    s = Session(sid, addr, now_ns)
                    self.sessions[addr] = s
                    self.metrics.add("sessions_started_total")
                    self.metrics.set("sessions_active", len(self.sessions))
                if flags & FLAG_BYE:
                    self.sessions.pop(addr, None)
                    self.metrics.set("sessions_active", len(self.sessions))
                    continue
                # s.seq == 0 is the first pose of the session, so there is
                # no gap to measure yet.
                if seq > s.seq + 1 and s.seq:
                    self.metrics.add("pose_lost_total", seq - s.seq - 1)
                elif seq <= s.seq and s.seq:
                    self.metrics.add("pose_reordered_total")
                if seq >= s.seq:
                    s.seq = seq
                    s.t_pose_ns = t_ns
                    s.recv_ns = now_ns

    # ------------------------------------------------------------------ TX
    def tx_loop(self):
        a = self.args
        period = 1.0 / a.fps
        timeout_ns = int(a.session_timeout * 1e9)
        next_t = time.perf_counter()
        while self.running:
            next_t += period
            now_ns = photon_clock.now_ns()

            with self.lock:
                dead = [k for k, s in self.sessions.items()
                        if now_ns - s.recv_ns > timeout_ns]
                for k in dead:
                    self.sessions.pop(k, None)
                if dead:
                    self.metrics.add("sessions_expired_total", len(dead))
                    self.metrics.set("sessions_active", len(self.sessions))
                # Snapshot of each session, taken under the lock. Reading
                # s.seq / s.t_pose_ns / s.recv_ns inside send_frame would
                # race with rx_loop: hold_us could come from one pose and
                # t_pose_ns from another, and the client's net_us would be
                # off by up to one pose interval. Same order of magnitude
                # as the number we are trying to measure.
                active = [(s, s.seq, s.t_pose_ns, s.recv_ns)
                          for s in self.sessions.values()]

            render_us = self.render(len(active)) if active else 0

            # With N sessions the pacing budget gets split among them.
            # There is only one period and everybody has to fit in it.
            share = self.budget / max(1, len(active))
            for s, seq, t_pose_ns, recv_ns in active:
                self.send_frame(s, seq, t_pose_ns, recv_ns, render_us, share)

            now = time.perf_counter()
            drift = next_t - now
            self.metrics.set("pacing_error_seconds", round(-drift, 6))
            if drift > 0:
                time.sleep(drift)
            else:
                self.metrics.add("frames_late_total")
                if drift < -5 * period:
                    # Way behind. Re-anchor the clock instead of trying to
                    # catch up on frames that are already meaningless.
                    self.metrics.add("frames_reanchored_total")
                    next_t = now

    def send_frame(self, s, seq, t_pose_ns, recv_ns, render_us, budget):
        """seq, t_pose_ns and recv_ns come from the tx_loop snapshot. The
        three of them have to describe the same pose, or the client's
        net_us comes out wrong."""
        s.frame_seq += 1
        paced = self.args.spread and self.n_chunks > 1
        gap = budget / self.n_chunks if paced else 0.0
        t_ref = time.perf_counter()
        sent = 0
        for idx in range(self.pkt_cnt):
            if paced and idx % self.chunk == 0 and idx:
                # Absolute deadline from t_ref, so sleep overshoot does not
                # pile up along the frame. It only shortens the next wait.
                target = t_ref + (idx // self.chunk) * gap
                wait = target - time.perf_counter()
                if wait > 0:
                    time.sleep(wait)
            # hold is recomputed for every packet. Packets of one frame
            # leave at different instants and the client closes m2p when
            # the LAST one arrives, so each packet has to carry the time it
            # personally spent in here. A single hold per frame would push
            # our own pacing into the client's net_us as if it were the
            # network.
            hold_us = max(0, (photon_clock.now_ns() - recv_ns) // 1000)
            FRAME_HDR.pack_into(
                self.buf, 0, FRAME_MAGIC, s.sid, s.frame_seq, idx,
                self.pkt_cnt, seq, t_pose_ns, int(hold_us),
                int(render_us), int(self.ai_us),
            )
            try:
                # MSG_DONTWAIT per call instead of switching the socket to
                # non-blocking, because rx_loop shares this socket and
                # wants to block. On a blocking socket a full sndbuf would
                # stall the generator until it drained, and the symptom
                # would surface as a late frame without saying where it
                # came from. Like this the overflow becomes a counter. And
                # a packet this process never handed to the kernel should
                # not be counted as network loss.
                self.sock.sendto(self.view, MSG_DONTWAIT, s.addr)
            except BlockingIOError:
                self.metrics.add("send_dropped_total", self.pkt_cnt - idx)
                break
            except OSError:
                self.metrics.add("send_errors_total")
                self.metrics.add("send_dropped_total", self.pkt_cnt - idx)
                break
            sent += 1
        self.metrics.add("frames_sent_total")
        self.metrics.add("frame_packets_total", sent)
        self.metrics.add("frame_bytes_total", sent * self.args.mtu)
        if sent < self.pkt_cnt:
            self.metrics.add("frames_truncated_total")

    def render(self, n_sessions):
        """Stand-in for the cost of rendering a frame. With --render-ms 0
        it costs nothing. With a value above zero it burns CPU on purpose,
        so the edge node carries a compute load comparable to a real
        render.

        --render-sleep swaps the burn for a sleep. That mode is for
        checking the instrument, not for running a campaign. While burning
        CPU this process holds the interpreter and the rx_loop thread falls
        behind, so at frame time the server is still holding an older pose
        than the one that already arrived, and hold (measured against the
        arrival of that pose) grows by MORE than the render you injected.
        I measured 1.32x instead of 1.00x on a heavy profile here.
        Sleeping releases the interpreter, rx_loop keeps up, and hold rises
        1:1 with the injected render, which is the property you want when
        you are validating the tool itself.

        If what you want is CPU load on the edge, stay with the burn (the
        default). A sleep occupies no core at all.
        """
        ms = self.args.render_ms
        if ms <= 0:
            self.metrics.set("render_seconds", 0.0)
            return 0
        budget = ms * n_sessions / 1000.0
        t0 = time.perf_counter()
        if self.args.render_sleep:
            time.sleep(budget)
        else:
            deadline = t0 + budget
            x = 0.0
            while time.perf_counter() < deadline:
                for _ in range(200):
                    x += 1.000001
        spent = time.perf_counter() - t0
        self.metrics.set("render_seconds", round(spent, 6))
        return spent * 1e6 / max(1, n_sessions)

    # ------------------------------------------------------------- AI hook
    def ai_loop(self):
        """Hook for an external inference service (in my testbed a Jetson
        running an AI-on-RAN workload). Fires requests at a fixed rate and
        measures the response time, which rides along in every frame as
        ai_us. Off by default."""
        import urllib.request

        a = self.args
        period = 1.0 / a.ai_rate
        payload = os.urandom(a.ai_bytes)
        next_t = time.perf_counter()
        while self.running:
            next_t += period
            t0 = time.perf_counter()
            try:
                req = urllib.request.Request(
                    a.ai_url, data=payload,
                    headers={"Content-Type": "application/octet-stream"})
                with urllib.request.urlopen(req, timeout=a.ai_timeout) as r:
                    r.read()
                dt = time.perf_counter() - t0
                self.ai_us = int(dt * 1e6)
                self.metrics.set("ai_response_seconds", round(dt, 6))
                self.metrics.add("ai_requests_total")
            except Exception:
                self.metrics.add("ai_requests_total")
                self.metrics.add("ai_failures_total")
            sleep = next_t - time.perf_counter()
            if sleep > 0:
                time.sleep(sleep)
            else:
                next_t = time.perf_counter()

    # ------------------------------------------------------------- metrics
    def serve_metrics(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path.startswith("/metrics"):
                    body = server.metrics.render({
                        "role": server.args.role,
                        "profile": server.args.profile,
                    }).encode()
                    self.send_response(200)
                    self.send_header("Content-Type",
                                     "text/plain; version=0.0.4")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif self.path.startswith("/healthz"):
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"ok\n")
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, *_):
                pass

        ThreadingHTTPServer((self.args.bind, self.args.metrics_port),
                            Handler).serve_forever()

    def run(self):
        a = self.args
        mbps = self.pkt_cnt * a.mtu * 8 * a.fps / 1e6
        print(f"photon-server role={a.role} profile={a.profile} "
              f"udp={a.bind}:{a.port} metrics={a.metrics_port}", flush=True)
        print(f"  downlink: {self.pkt_cnt} pkt/frame x {a.mtu} B x {a.fps} fps"
              f" = {mbps:.1f} Mbps per session (target {a.bitrate_mbps} Mbps)",
              flush=True)
        if a.spread and self.n_chunks > 1:
            print(f"  pacing: {self.n_chunks} bursts of {self.chunk} pkt every"
                  f" {self.gap * 1e3:.2f} ms ({self.budget * 1e3:.1f} ms out "
                  f"of {1e3 / a.fps:.1f} ms)", flush=True)
        else:
            print("  pacing: OFF, the whole frame goes out as one burst "
                  f"({self.pkt_cnt * a.mtu / 1e3:.1f} kB)", flush=True)
        print(f"  buffers: rcvbuf={self.rcvbuf // 1024} kB "
              f"sndbuf={self.sndbuf // 1024} kB "
              f"kernel_stamp={'yes' if self.ts_kernel else 'NO'}", flush=True)
        if self.sndbuf < 4 << 20:
            print("  WARNING: the kernel clamped the sndbuf we asked for "
                  "(8 MB). Raise net.core.wmem_max/rmem_max ON THE NODE. "
                  "They are global sysctls, not per namespace, so you cannot "
                  "do it from inside the pod.", flush=True)
        if not self.ts_kernel:
            print("  WARNING: no SO_TIMESTAMPNS here. hold is stamped in "
                  "Python and carries this process's time off the CPU with "
                  "it.", flush=True)
        print(f"  render={a.render_ms} ms/frame/session "
              f"[{'sleep' if a.render_sleep else 'CPU burn'}] "
              f"ai={'on' if a.ai_url else 'off'}", flush=True)

        threading.Thread(target=self.serve_metrics, daemon=True).start()
        threading.Thread(target=self.rx_loop, daemon=True).start()
        if a.ai_url:
            threading.Thread(target=self.ai_loop, daemon=True).start()
        try:
            self.tx_loop()
        except KeyboardInterrupt:
            self.running = False


def envd(name, default, cast=str):
    v = os.environ.get(name)
    return cast(v) if v not in (None, "") else default


def parse_args(argv):
    p = argparse.ArgumentParser(
        description="PhotonPath server: heavy downlink closed in a loop "
                    "with a pose uplink")
    p.add_argument("--bind", default=envd("PP_BIND", "0.0.0.0"))
    p.add_argument("--port", type=int, default=envd("PP_PORT", 9100, int))
    p.add_argument("--metrics-port", type=int,
                   default=envd("PP_METRICS_PORT", 9101, int))
    p.add_argument("--bitrate-mbps", type=float,
                   default=envd("PP_BITRATE_MBPS", 50.0, float),
                   help="downlink rate PER SESSION (default 50)")
    p.add_argument("--fps", type=int, default=envd("PP_FPS", 90, int))
    p.add_argument("--mtu", type=int, default=envd("PP_MTU", 1200, int),
                   help="UDP payload bytes per packet (default 1200, which "
                        "stays under the GTP-U tunnel MTU)")
    p.add_argument("--render-ms", type=float,
                   default=envd("PP_RENDER_MS", 0.0, float),
                   help="CPU burned per frame per session, standing in for "
                        "the render")
    p.add_argument("--render-sleep", action=argparse.BooleanOptionalAction,
                   default=envd("PP_RENDER_SLEEP", "0") not in
                   ("0", "false", ""),
                   help="inject --render-ms as a SLEEP instead of a CPU "
                        "burn. This is the instrument-check mode: while "
                        "sleeping the interpreter is free, rx_loop keeps up "
                        "and hold rises 1:1 with the injected render. "
                        "Campaigns use the burn (default), which is what "
                        "actually loads the edge node")
    p.add_argument("--spread", action=argparse.BooleanOptionalAction,
                   default=envd("PP_SPREAD", "1") not in ("0", "false", ""),
                   help="ON by default: spreads the frame's packets over the "
                        "period, in bursts of --pace-chunk packets, instead "
                        "of sending the whole frame at once. --no-spread "
                        "goes back to the single burst (and back to losing "
                        "frames at high load)")
    p.add_argument("--pace-duty", type=float,
                   default=envd("PP_PACE_DUTY", 0.8, float),
                   help="fraction of the frame period used to spread the "
                        "packets (default 0.8; the rest is slack so the "
                        "generator does not overshoot its own cadence)")
    p.add_argument("--pace-min-gap-ms", type=float,
                   default=envd("PP_PACE_MIN_GAP_MS", 1.0, float),
                   help="minimum gap between bursts. Below this, Linux's "
                        "time.sleep() misses by more than the gap itself; "
                        "the burst size is derived from this floor")
    p.add_argument("--session-timeout", type=float,
                   default=envd("PP_SESSION_TIMEOUT", 2.0, float))
    p.add_argument("--role", default=envd("PP_ROLE", "edge"),
                   help="metrics label, e.g. edge or remote")
    p.add_argument("--profile", default=envd("PP_PROFILE", "heavy"))
    p.add_argument("--ai-url", default=envd("PP_AI_URL", ""),
                   help="endpoint of an external inference service; empty "
                        "disables the hook")
    p.add_argument("--ai-rate", type=float,
                   default=envd("PP_AI_RATE", 10.0, float))
    p.add_argument("--ai-bytes", type=int,
                   default=envd("PP_AI_BYTES", 4096, int))
    p.add_argument("--ai-timeout", type=float,
                   default=envd("PP_AI_TIMEOUT", 2.0, float))
    return p.parse_args(argv)


if __name__ == "__main__":
    Server(parse_args(sys.argv[1:])).run()
