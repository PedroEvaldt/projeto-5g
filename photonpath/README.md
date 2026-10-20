# PhotonPath

A closed-loop traffic generator that measures **motion-to-photon latency**
across a mobile network, and splits it into the part the network is
responsible for and the part the application is responsible for.

I wrote it for my undergraduate thesis, where I needed to show what a 5G
local breakout (traffic leaving the user plane at an edge UPF instead of
going all the way to the central anchor) actually does to an interactive
VR workload. Off-the-shelf tools did not answer that question: iperf tells
you how much fits through the pipe, and ping tells you how long an empty
packet takes. Neither of them tells you how long a user waits between
moving their head and seeing the frame that reflects it, which is the
number VR people actually care about.

So this is a synthetic VR application. It is not a renderer and it does not
draw anything. It reproduces the traffic shape of one, and it is honest
about how much of the delay it caused itself.

```
   UE / client                                      server (edge or remote)
   -----------                                      -----------------------
   pose, 128 B @ 250 Hz    ------------------->     keeps the latest pose
                                                    waits for the next
                                                    frame instant
   frame, ~12 kB @ 90 fps  <-------------------     sends it back with the
   (paced, many packets)                            pose timestamp inside
```

Every frame carries the timestamp of the last pose the server had received.
The client measures the round trip with its own clock, so **there is no
clock synchronisation between the UE and the server**. That matters in a
testbed where you cannot trust NTP between the nodes.

## The three numbers

| Number | What it is |
| --- | --- |
| `m2p_us` | motion-to-photon: from the pose sample until the last packet of the frame arrives. This is the application KPI. |
| `hold_us` | how long that pose sat inside the server: waiting for the next frame instant, plus the pacing window, plus the render. |
| `net_us` | `m2p - hold`. The part that depends on where the traffic leaves the user plane. |

Keeping these apart is the whole point. If you only report m2p, most of
what you are showing is the application's own timing (at 90 fps with
pacing on, a frame spends around 10 ms inside the server before its last
packet is out), and that drowns out the difference between two network
paths. If you only report net RTT, you are
not reporting what the user feels. You need both, plus the decomposition,
so a reader can check that `m2p = net + hold` and see how much of your
result is a choice you made in the generator.

## Requirements

- **Linux** for a real measurement. The client and the server use
  `SO_TIMESTAMPNS` so packets are timestamped by the kernel and not by
  Python. See [docs/measuring.md](docs/measuring.md) for why that is not
  optional.
- **Python 3.9 or newer**. No dependencies, no pip install, no build. I run
  it in a stock `python:3.12-slim` container.
- macOS works for a smoke test, but it has no `SO_TIMESTAMPNS` and a coarse
  `time.sleep()`, so the numbers it gives you are not measurements. Both
  programs print a warning when the kernel timestamp is missing.
- Windows is not supported (`recvmsg_into` does not exist there).

## Quick start

Two terminals on the same machine, just to see it work:

```bash
# terminal 1
python3 src/photon_server.py --bitrate-mbps 8 --fps 60

# terminal 2
python3 src/photon_client.py --server 127.0.0.1:9100 --bind 127.0.0.1 \
    --duration 15 --warmup 3 --label demo --csv /tmp/demo.csv
```

Or just run the script that does both:

```bash
./examples/local-demo.sh 15
```

The server prints what it is going to generate and then stays quiet:

```
photon-server role=edge profile=heavy udp=0.0.0.0:9100 metrics=9101
  downlink: 14 pkt/frame x 1200 B x 60 fps = 8.1 Mbps per session (target 8.0 Mbps)
  pacing: 7 bursts of 2 pkt every 1.90 ms (13.3 ms out of 16.7 ms)
  buffers: rcvbuf=8192 kB sndbuf=8192 kB kernel_stamp=yes
  render=0.0 ms/frame/session [CPU burn] ai=off
```

The client prints a JSON summary on stdout and, optionally, one CSV row per
frame. Read [docs/measuring.md](docs/measuring.md) before you believe any
of it.

## Running it across a mobile network

This is what the tool is for. The layout I use:

- **`photon_server.py` on the edge**, on the same node as the UPF that
  performs the breakout. If the application sits on a different node than
  the UPF, the packet leaves the user plane and still crosses a wire to
  reach it, which is a different topology from the one you are claiming to
  measure.
- **A second `photon_server.py` far away**, behind the transport network,
  with *exactly the same configuration*. This is your control. Running both
  is what lets you attribute a difference to the path instead of to the
  workload, and it also proves your breakout rule is classifying traffic
  rather than diverting everything.
- **`photon_client.py` on the UE side**, bound to the IP of the PDU session
  tunnel (`--bind 10.60.0.1` or whatever your UE got). Binding matters: if
  you leave it on `0.0.0.0` the traffic may leave through the host
  interface and never enter the 5G data path at all, and everything will
  look suspiciously fast.
- **Keep the RAN simulator off the edge node.** A subscriber does not live
  inside the edge host, and on a small machine the simulator will fight the
  generator for CPU. On a 100 Mbps profile here the server sustained only
  53 of the 90 fps when it shared a node with the gNB and the UE.

Then you run the client twice, once against each server, and compare. For
Kubernetes manifests and a ConfigMap-based deployment (no registry, no
image build), see [docs/kubernetes.md](docs/kubernetes.md).

## Server options

Every option also reads an environment variable, so you can change profiles
with `kubectl set env` without touching the manifests.

| Option | Env | Default | What it does |
| --- | --- | --- | --- |
| `--bind` | `PP_BIND` | `0.0.0.0` | UDP listen address |
| `--port` | `PP_PORT` | `9100` | UDP port for pose and frames |
| `--metrics-port` | `PP_METRICS_PORT` | `9101` | HTTP port for `/metrics` and `/healthz` |
| `--bitrate-mbps` | `PP_BITRATE_MBPS` | `50` | downlink rate **per session** |
| `--fps` | `PP_FPS` | `90` | frames per second |
| `--mtu` | `PP_MTU` | `1200` | UDP payload bytes per packet |
| `--render-ms` | `PP_RENDER_MS` | `0` | CPU burned per frame per session, standing in for the render |
| `--render-sleep` | `PP_RENDER_SLEEP` | off | inject the render as a sleep instead of a burn (instrument check only) |
| `--spread` / `--no-spread` | `PP_SPREAD` | on | pace the frame's packets across the period |
| `--pace-duty` | `PP_PACE_DUTY` | `0.8` | fraction of the period used for pacing |
| `--pace-min-gap-ms` | `PP_PACE_MIN_GAP_MS` | `1.0` | minimum gap between bursts |
| `--session-timeout` | `PP_SESSION_TIMEOUT` | `2.0` | seconds without a pose before a session is dropped |
| `--role` | `PP_ROLE` | `edge` | label on the Prometheus metrics |
| `--profile` | `PP_PROFILE` | `heavy` | second label, for your own bookkeeping |
| `--ai-url` | `PP_AI_URL` | empty | optional external inference endpoint (see below) |

`--mtu 1200` is not arbitrary. It keeps the IP packet under the GTP-U
tunnel MTU so nothing gets fragmented inside the tunnel, which would change
what you are measuring. If your tunnel is smaller, lower it.

**Pacing.** A 50 Mbps frame at 60 fps is 87 packets. Sent as one burst they
leave at interface speed, several Gbps instantaneous, and nothing on the
path absorbs that: whatever overflows gets reported as loss when it was
really burstiness. Paced, the same frame asks for a queue about ten times
smaller. The cost is that the pacing window lands inside `hold`, so `m2p`
goes up. It goes up by the same amount on every path, and `net_us`
subtracts it back out. `--no-spread` turns it off if you want to see the
difference for yourself.

**The AI hook** (`--ai-url`) fires fixed-rate HTTP requests at an external
inference service and reports the response time in every frame as `ai_us`.
I use it to add an AI-on-RAN workload next to the VR one. It is off by
default and nothing depends on it.

## Client options

| Option | Env | Default | What it does |
| --- | --- | --- | --- |
| `--server` | `PP_SERVER` | `edge-app:9100` | `host:port` of the server; resolved once at startup |
| `--bind` | `PP_BIND` | `0.0.0.0` | **the UE address.** Set this |
| `--lport` | `PP_LPORT` | `0` | local UDP port, 0 lets the kernel pick |
| `--duration` | `PP_DURATION` | `60` | seconds of traffic |
| `--warmup` | `PP_WARMUP` | `5` | initial seconds dropped from **all** metrics |
| `--pose-hz` | `PP_POSE_HZ` | `250` | uplink pose rate |
| `--pose-bytes` | `PP_POSE_BYTES` | `128` | size of each pose packet |
| `--session` | `PP_SESSION` | random | session id, if you want it fixed |
| `--csv` | `PP_CSV` | none | write one row per frame here |
| `--json` | `PP_JSON` | none | also write the summary to this file |
| `--label` | `PP_LABEL` | `run` | tag that goes in the CSV and the summary |

The warmup is applied to latency, loss and throughput alike. That is
deliberate: measuring loss over the whole run while measuring latency only
after the warmup contaminates the loss figure with the PDU session coming
up.

## Output

**Per-frame CSV** (`--csv`), one row per completed frame:

```
label,t_s,frame_seq,pose_seq,m2p_us,net_us,hold_us,render_us,ai_us,pkts
demo,0.011751,1,1,15536.0,157.0,15379,0,0,14
```

Note that the CSV holds the whole run, warmup included, so filter by `t_s`
if you are plotting the same window the summary reports.

**JSON summary** on stdout, which is what a campaign script should consume.
Abridged:

```jsonc
{
  "label": "demo",
  "frames_complete": 360,        // whole run
  "frames_in_stats": 299,        // measurement window only
  "dl_packet_loss_pct": 0.2619,
  "dl_throughput_mbps": 8.039,
  "dl_continuity_pct": 99.67,    // < 100 means the flow was interrupted
  "window":   { ... },           // the raw counters the KPIs come from
  "m2p_us":   { "min": ..., "p50": ..., "p95": ..., "p99": ..., "max": ... },
  "net_rtt_us": { ... },
  "hold_us":  { ... },
  "jitter_us": 1068.1,           // RFC 3550 style, over m2p
  "jitter_net_us": 75.8,         // same, over net RTT
  "health":   { ... }            // is this run usable at all
}
```

(Those values came from the loopback demo on my laptop. They are there to
show the shape of the output, not as results.)

**Prometheus metrics** on `:9101/metrics`, prefixed `photonpath_server_`.
The interesting ones are not the traffic counters but the self-diagnostics:
`pacing_error_seconds`, `send_dropped_total`, `frames_late_total`,
`sock_sndbuf_bytes` and `kernel_rx_timestamp`. If any of those move, the
bottleneck was the generator and that data point should be thrown away.

## Before you trust a run

The summary carries a `health` block for exactly this. The short version:

- `rx_timestamp_kernel` must be `true`.
- `dl_sock_drops` and `udp_rcvbuf_errors` must be `0`. If they are not, the
  packets were dropped by the client's own kernel, so you measured Python
  and not the network.
- `dl_continuity_pct` must be `100`. Below that, the flow was interrupted,
  which packet loss does not detect: a server that stops sending leaves no
  hole in the sequence numbers. This bit me on my very first testbed run,
  where the downlink died at 33 s and the summary cheerfully reported
  "6.34 Mbps, loss 0.000%".
- `clock_drift_us` must be near zero, otherwise NTP stepped mid-run.

The long version, including how to check that the tool measures what it
claims to, is in [docs/measuring.md](docs/measuring.md).

## Raising the bitrate

The single most common reason a high-bitrate run looks terrible is socket
buffers. The generator asks for 8 MB and the kernel silently clamps it to
`net.core.rmem_max` (208 kB by default). Those are **global** sysctls, not
per network namespace, so you have to set them on the node and cannot do it
from inside a pod:

```bash
sudo sysctl -w net.core.rmem_max=16777216 net.core.wmem_max=16777216
```

Both programs print what the kernel actually granted, and warn when it was
cut. Check that line before blaming the network.

## What this is not

- Not a VR renderer, and not a claim about any real headset's pipeline. It
  reproduces a traffic shape and a timing structure, nothing else.
- Not a substitute for a throughput test. It answers "how long does the
  loop take", not "how much fits". Run iperf separately for the ceiling.
- Not calibrated against a hardware reference. The floor of what it can
  resolve is the machine it runs on, which is why it reports its own health
  instead of pretending otherwise.

## Where this comes from

It was built for my final-year project (TG2) at UFRGS, on a testbed with
free5GC and UERANSIM, comparing a session with an edge breakout against one
anchored in the core. The traffic model follows the VR-inspired
bidirectional profile I described in the first half of that work: heavy
downlink, light and frequent uplink, closed loop.

If it is useful in something you publish, a mention is enough, and I would
genuinely like to hear what you measured with it.

## Documentation

- [docs/measuring.md](docs/measuring.md): what each number means, how the
  measurement can lie to you, and how to check the instrument.
- [docs/kubernetes.md](docs/kubernetes.md): deploying both servers and
  running the client from inside a UE pod.
- [docs/protocol.md](docs/protocol.md): the wire format, if you want to
  write your own client or parse a capture.

## License

MIT, see [LICENSE](LICENSE).
