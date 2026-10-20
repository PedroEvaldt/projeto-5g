# Reading the numbers

This is the page I wish I had when I started. Everything here is something
that went wrong at least once on my testbed before I understood it.

## The decomposition

```
motion-to-photon  =  uplink  +  hold  +  downlink
                     \___________________________/
                              m2p_us

network RTT       =  m2p_us  -  hold_us   =   net_us
```

`hold_us` is time spent inside the server: waiting for the next frame
instant, the pacing window, and the render if you enabled one. It is
reported by the server inside every frame, so the client can subtract it
without knowing anything about the server's schedule.

Two rules follow from this:

1. **`m2p` depends on your generator settings.** Change `--fps` and it
   moves. Change `--pace-duty` and it moves. That is fine, as long as both
   sides of your comparison use the same settings and you say so.
2. **`net` is the one you compare across paths.** It has the generator's
   own timing subtracted out, so it is the number that should change when
   the traffic takes a different route and not otherwise.

If you report a big `m2p` improvement and a flat `net`, you did not measure
the network: you measured two different application configurations.

## Why the hold has to be per packet

The frame is many packets. With pacing on, they leave at different
instants, and the client closes the measurement when the **last** one
arrives. So each packet carries the hold *it* accumulated, and the client
uses the hold of the packet that completed the frame.

A single hold per frame would have pushed the server's own pacing into
`net_us`, where it would look exactly like network delay. This is the kind
of error that does not show up as an obvious bug: it just makes your
numbers a bit worse in a way that scales with load.

## Why kernel timestamps

A timestamp taken in Python, after `recvmsg` returns, includes the time the
process spent waiting for a CPU. On a node that is also running the 5G
core, both UPFs and the RAN simulator, that wait is not negligible, and it
**grows with load**. Which is exactly where you need the measurement to
hold up.

The failure mode is nasty because it is plausible: latency gets worse as
you raise the bitrate, you write that the edge path degrades more
gracefully than the remote one, and what you actually plotted is the
scheduler. `SO_TIMESTAMPNS` moves the stamp into the kernel, at packet
arrival, and everything after that stops counting.

The price: the measurement clock becomes `CLOCK_REALTIME`, because that is
what the kernel uses for software timestamps. Both ends of each measurement
are in the same process, so nothing has to be synchronised between
machines, but an NTP step during a run would shift the samples. That is
what `clock_drift_us` in the health block is for. If it is more than a
couple hundred microseconds, throw the run away.

On a platform without support, both programs print a warning at startup and
`health.rx_timestamp_kernel` comes back `false`. Do not publish those runs.

## The health block, field by field

```jsonc
"health": {
  "rx_timestamp_kernel": true,       // false = the run measures Python too
  "sock_rcvbuf_bytes": 8388608,      // what the kernel granted, not what we asked
  "dl_sock_drops": 0,                // OUR socket's queue overflowed (from /proc)
  "dl_sock_overflow_pkts": 0,        // same thing via SO_RXQ_OVFL, when available
  "dl_sock_overflow_run": 0,         // ditto, whole run instead of the window
  "udp_rcvbuf_errors": 0,            // whole network namespace
  "udp_in_errors": 0,                // ditto
  "ctxt_switches_involuntary": 118,  // times the scheduler preempted us
  "clock_drift_us": -3.1             // realtime vs monotonic over the run
}
```

`dl_sock_drops` and `udp_rcvbuf_errors` are the two that decide whether a
run is about the network at all. They are different scopes on purpose:

- `dl_sock_drops` is **this socket**. Non-zero means the client could not
  keep up and the kernel threw packets away. The loss you are about to
  report is yours.
- `udp_rcvbuf_errors` is the **whole network namespace**. Inside a UERANSIM
  pod, that also covers the gNB and UE sockets. Non-zero here with zero on
  our own socket means the loss happened inside the simulator, which is
  still not the path you meant to measure.

`ctxt_switches_involuntary` will never be zero, and that is fine. Watch it
across a campaign instead: if it jumps by an order of magnitude on the runs
with the worst latency, the machine was contended and you are plotting CPU
contention.

Two counters exist twice (`dl_sock_overflow_pkts` and `..._run`) because
the `SO_RXQ_OVFL` control message did not show up on some of my runs even
though the kernel accepted the socket option. `/proc/net/udp` always
answers, so `dl_sock_drops` is the one to trust and the cmsg is a
confirmation.

## Loss is not the same as continuity

`dl_packet_loss_pct` counts holes in the packet sequence. A server that
**stops sending** leaves no holes. So a flow that dies halfway through a
run comes out with nominal throughput and zero loss, because both the
numerator and the denominator shrink together.

That is why throughput is computed over the window you *asked for*, not
over the interval where traffic actually arrived, and why there is a
separate `dl_continuity_pct`. On my first testbed run the remote downlink
stopped at 33 s of a 60 s run, and an earlier version of this summary
reported "6.34 Mbps, loss 0.000%". It looked like a clean data point.

Check `dl_continuity_pct == 100` before anything else.

Also note the client-side loss figure is a **floor**. The client cannot see
a frame that was lost in its entirety, because it never learns that the
frame existed. For the exact figure, pair `dl_packets_rx_run` with the
server's `photonpath_server_frame_packets_total` over the same interval.
And subtract `photonpath_server_send_dropped_total`: a packet the server
never handed to the kernel is not network loss, it is generator loss, and
it deserves a different sentence in your write-up.

## Checking the instrument

You can make the tool verify its own arithmetic. Run the server with

```bash
python3 src/photon_server.py --render-ms 5 --render-sleep
```

and compare against `--render-ms 0`. The injected 5 ms should land, almost
exactly, in `hold_us`, and `net_us` should not move. If `net_us` moves,
the decomposition is broken somewhere and nothing downstream is
trustworthy.

Use `--render-sleep` for this and not the default CPU burn. While burning
CPU, the server holds the interpreter and its receive thread falls behind,
so at frame time it is still holding an older pose than the one that
already arrived, and `hold` grows by *more* than what you injected. I
measured 1.32x instead of 1.00x doing it that way, and spent a while
thinking the tool was broken before realising the burn was the cause.

The CPU burn is still the right choice when what you want is load on the
edge node. A sleep occupies no core at all.

Do the same sweep with `--fps`: going from 90 to 60 fps should raise the
median `hold` and leave `net` where it was. The dominant term in `hold` is
the pacing window, `pace_duty x period`, so expect roughly
`0.8 x (16.7 - 11.1) = 4.4 ms`. The other term, how stale the pose already
was when the frame instant came, is bounded by the pose interval (4 ms at
250 Hz) and barely moves.

## Designing a comparison

- **Change one thing.** Same bitrate, same fps, same MTU, same duration,
  same warmup on both paths. The only difference should be the destination.
- **Interleave the runs.** Do not run all the edge points first and then
  all the remote points. Anything that drifts during the session (thermal,
  another tenant on the cluster, your own background load) would land
  entirely on one arm of the comparison. Alternate, and treat a
  round of both as the unit.
- **Repeat.** A single 60 s run gives you a percentile, not a result. I use
  repeated rounds and compare distributions, because the tail is usually
  where the interesting difference is. The medians of two paths can sit a
  few hundred microseconds apart and look boring while p99 tells a
  completely different story.
- **Watch the health block on every single run**, not just the weird ones.
  A run that fails the checks has to be discarded before you look at its
  latency, otherwise you are choosing which points to keep based on their
  results.

## Known limits

- The loss figure from the client alone is a floor (see above).
- Percentiles come from linear interpolation between order statistics, no
  fancy estimator. At 90 fps over 60 s that is about 5000 samples, which is
  fine for p95 and adequate for p99.
- `jitter_us` follows the RFC 3550 smoothing, which is a filter and not a
  standard deviation. It is there because it is what network people expect
  to see; if you want dispersion, take it from the CSV.
- Everything is single-threaded Python. That is enough for the rates in the
  table below on a modern core, but check `frames_late_total` and
  `pacing_error_seconds` if you push beyond it.

| Profile | Bitrate | fps | Packets per frame |
| --- | --- | --- | --- |
| light | 10 Mbps | 60 | 18 |
| medium | 25 Mbps | 90 | 29 |
| heavy | 50 Mbps | 90 | 58 |
| extreme | 100 Mbps | 90 | 116 |

The extreme profile is where I started hitting the generator's own ceiling
on the hardware I had. Your mileage will differ, which is exactly why the
generator reports its own health.
