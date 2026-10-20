# Running it on a Kubernetes 5G testbed

This is how I deploy it on top of free5GC plus UERANSIM. Nothing here is
specific to free5GC, but the names of things will be.

## Why a ConfigMap and not an image

The three scripts are mounted from a ConfigMap. No registry, no image
build, and changing the code is `kubectl apply` plus a pod restart. The
container image stays a stock `python:3.12-slim` because there is nothing
to install.

The three files have to land in the **same directory** inside the pod, so
that `import photon_clock` resolves: Python puts the script's own directory
on `sys.path`, and that is the whole mechanism.

The ConfigMap is generated from the sources, so regenerate it whenever you
touch a `.py`:

```bash
python3 tools/gen-configmap.py --namespace 5gcore --out deploy/configmap.yaml
```

A ConfigMap has a 1 MiB limit. These three files are nowhere near it.

## 1. Before anything, the node sysctls

The generator asks for 8 MB of socket buffer and the kernel silently
clamps it to `net.core.rmem_max`, which is 208 kB on most distributions.
These are global sysctls: they are not namespaced, so you cannot set them
from inside a pod. On **every node that will run a server or the client**:

```bash
sudo sysctl -w net.core.rmem_max=16777216 net.core.wmem_max=16777216
```

If you skip this, the loss you measure at higher bitrates is the buffer,
not the network. And if one node has it and the other does not, your two
paths are not comparable at all.

Both programs print what they actually got, so check for the clamp warning
in the logs before you start a campaign.

## 2. Deploy the two servers

```bash
kubectl apply -f deploy/configmap.yaml
kubectl apply -f deploy/apps.yaml          # edit the nodeSelectors first
kubectl -n 5gcore get pods -l app=photonpath-edge -w
```

Placement is not a detail:

- **edge-app** goes on the node that hosts the UPF doing the breakout. If
  it lands somewhere else, the packet leaves the user plane and then
  crosses a wire anyway, and you are measuring a topology you did not
  intend to describe.
- **remote-app** goes on the far side of the transport network, and *not*
  on the anchor UPF's node. Sharing a node with the anchor hands the remote
  path a free hop that the edge path has to pay for.
- Neither of them shares a node with the RAN simulator, if you can help it.

Check the banner:

```bash
kubectl -n 5gcore logs deploy/photonpath-edge
```

You want `kernel_stamp=yes` and no buffer warning.

## 3. Make the client reachable from the UE pod

The client runs inside the pod that owns the PDU session, so it can bind to
the tunnel address. Mount the same ConfigMap there. In a UERANSIM pod, add
to the tester deployment:

```yaml
      volumes:
        - name: photonpath
          configMap:
            name: photonpath
      containers:
        - name: photon-client
          image: python:3.12-slim
          command: ["sleep", "infinity"]   # we exec into it per run
          volumeMounts:
            - name: photonpath
              mountPath: /opt/photonpath
              readOnly: true
```

A sidecar that just sleeps is the easiest arrangement: it shares the pod's
network namespace, so it sees the `uesimtun0` interface, and you exec into
it once per run without restarting anything.

## 4. Find the UE address and run

```bash
# the tunnel address UERANSIM got for the PDU session
kubectl -n 5gcore exec deploy/ueransim -c ue -- \
    ip -4 -o addr show uesimtun0 | awk '{print $4}'
```

Then one run against each server:

```bash
kubectl -n 5gcore exec deploy/ueransim -c photon-client -- \
    python3 /opt/photonpath/photon_client.py \
      --server edge-app:9100 --bind 10.60.0.1 \
      --duration 60 --warmup 5 --label edge \
      --csv /tmp/edge.csv --json /tmp/edge.json

kubectl -n 5gcore exec deploy/ueransim -c photon-client -- \
    python3 /opt/photonpath/photon_client.py \
      --server remote-app:9100 --bind 10.60.0.1 \
      --duration 60 --warmup 5 --label remote \
      --csv /tmp/remote.csv --json /tmp/remote.json
```

`--bind` has to be the UE address. Without it the traffic can leave through
the pod's own interface, skip the 5G data path entirely, and give you
beautifully low latency that means nothing. If you are unsure, check that
the packets show up on the tunnel:

```bash
kubectl -n 5gcore exec deploy/ueransim -c ue -- \
    timeout 3 tcpdump -ni uesimtun0 udp port 9100 -c 5
```

Pull the files out with `kubectl cp` when the run is done.

## 5. Confirm the breakout is actually happening

The point of having two servers is that they should take different paths.
Prove it rather than assuming it:

```bash
# from inside the UE container
traceroute -n -U -p 9100 <edge-app ClusterIP>
traceroute -n -U -p 9100 <remote-app ClusterIP>
```

You can also watch the edge UPF's interface while a run is in progress and
confirm the edge traffic is there and the remote traffic is not. If both
destinations look identical, your traffic steering rule is not matching and
every number you collect afterwards compares a path with itself.

## 6. Scraping the metrics

If you run the Prometheus Operator, `deploy/servicemonitor.yaml` wires both
servers up. The label under `metadata.labels` has to match your Prometheus
`serviceMonitorSelector`, or it is ignored without any error.

Without the operator, read them directly:

```bash
kubectl -n 5gcore exec deploy/photonpath-edge -- python3 -c \
  "import urllib.request; print(urllib.request.urlopen('http://localhost:9101/metrics').read().decode())"
```

The server counters you want next to every run: `frame_packets_total` (for
the exact loss, paired with the client's `dl_packets_rx_run`),
`send_dropped_total`, `frames_late_total` and `pacing_error_seconds`.

## Things that bit me

| Symptom | Cause |
| --- | --- |
| `NO complete frame received` | `--bind` is not the UE address, or there is no route back from the server to the UE subnet. Check the UPF's route table, not the pod's. |
| Loss climbs with bitrate on one path only | Socket buffers on that node (step 1), not the network. Compare the `rcvbuf=` line in both banners. |
| `dl_continuity_pct` below 100 | The flow was interrupted. Look for a UPF or session restart, not for packet loss. |
| Latency numbers that get worse every run of a campaign | Something is accumulating: check `ctxt_switches_involuntary`, and check that the previous run's server process really exited. |
| `kernel_stamp=NO` | You are not on Linux, or the kernel refused `SO_TIMESTAMPNS`. Those runs are not publishable. |
| Everything is fast and loss is zero | Suspicious. Verify the traffic is actually going through the tunnel (step 4) before celebrating. |
