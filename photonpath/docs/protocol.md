# Wire format

Two UDP message types on the same port (9100 by default). Everything is
big-endian (`!` in Python's `struct`), because these are network packets
and I did not want to think about the endianness of whatever node they
land on.

You only need this page if you are writing your own client or server, or
parsing a capture. Using the tool does not require it.

## Pose (client to server)

`struct.Struct("!4sIQQI")`, 28 bytes of header, padded to `--pose-bytes`
(128 by default).

| Field | Type | Meaning |
| --- | --- | --- |
| magic | 4s | `PPOS` |
| session | I | session id, random per client run |
| seq | Q | pose sequence number, starts at 1 |
| t_client_ns | Q | `CLOCK_REALTIME` when the pose was sampled |
| flags | I | bit 0 (`FLAG_BYE`) ends the session |

The padding after the header is zeros and carries no meaning. A real
application would put orientation and controller state there; the size is
what matters for the traffic shape.

`t_client_ns` is the client's own clock and is only ever read again by that
same client, when the frame brings it back. Nothing compares it against the
server's clock, which is what removes the need for clock synchronisation.

## Frame (server to client)

`struct.Struct("!4sIQHHQQIII")`, 48 bytes of header, followed by payload up
to `--mtu` bytes total.

| Field | Type | Meaning |
| --- | --- | --- |
| magic | 4s | `PPFR` |
| session | I | echoed from the pose |
| frame_seq | Q | frame counter for this session, starts at 1 |
| pkt_idx | H | index of this packet within the frame, from 0 |
| pkt_cnt | H | how many packets this frame has |
| pose_seq | Q | sequence of the pose this frame answers |
| t_pose_ns | Q | echoed `t_client_ns` of that pose |
| hold_us | I | how long that pose has been inside the server, **as of this packet** |
| render_us | I | render cost charged to this frame |
| ai_us | I | last response time of the external inference hook, 0 when off |

`hold_us` changes from packet to packet within the same frame. That is
deliberate: with pacing enabled the packets leave at different instants,
and the client measures against the arrival of the last one, so it needs
the hold that belongs to that specific packet.

The payload is a fixed non-zero byte pattern (`i & 0xFF`). Zeros are too
friendly to compression and to offload paths, and I would rather the bytes
on the wire be the bytes I asked for.

## Reassembly

A frame is complete when every `pkt_idx` from 0 to `pkt_cnt - 1` has
arrived. The client tracks them in a **bitmask**, not a counter: with a
counter, a duplicated packet would complete a frame that is still missing a
piece, and that frame's motion-to-photon would be recorded at the wrong
instant, too early.

Frames are held in a dictionary keyed by `frame_seq`. When more than 64 are
pending, the oldest are dropped and counted as incomplete. Anything still
pending when the run ends is counted too, which sounds obvious but was a
bug for a while: the summary would report zero incomplete frames on a test
that had been losing packets from start to finish.

## Sessions

The server keys sessions by source address. A pose with a different
`session` from the same address starts a new session, and a session with no
pose for `--session-timeout` seconds (2 s by default) is dropped.

`FLAG_BYE` is a courtesy, not a requirement: it makes the server stop
sending immediately instead of waiting for the timeout. A client that is
killed mid-run just leaves a session to expire.

## Changing any of this

If you modify a header, change the magic too. Two versions that share magic
bytes and disagree about the layout will not fail loudly, they will produce
plausible garbage, and you will spend an afternoon on it.
