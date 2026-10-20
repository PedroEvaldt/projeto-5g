#!/usr/bin/env bash
# Smallest possible run: server and client on the same machine, over
# loopback. Good for checking that both sides talk to each other and that
# you can read the output. It is NOT a measurement: on loopback there is no
# network to measure, and on macOS there are no kernel timestamps either.
#
#   ./examples/local-demo.sh [duration_seconds]
set -euo pipefail

DUR="${1:-15}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${TMPDIR:-/tmp}/photonpath-demo"
mkdir -p "$OUT"

echo "== starting the server (8 Mbps, 60 fps) =="
python3 -u "$HERE/src/photon_server.py" \
    --bitrate-mbps 8 --fps 60 --role demo > "$OUT/server.log" 2>&1 &
SRV=$!
trap 'kill $SRV 2>/dev/null || true' EXIT
sleep 2
cat "$OUT/server.log"

echo
echo "== running the client for ${DUR}s =="
python3 "$HERE/src/photon_client.py" \
    --server 127.0.0.1:9100 --bind 127.0.0.1 \
    --duration "$DUR" --warmup 3 \
    --label demo --csv "$OUT/demo.csv" --json "$OUT/demo.json"

echo
echo "per-frame CSV:  $OUT/demo.csv"
echo "JSON summary:   $OUT/demo.json"
echo "server log:     $OUT/server.log"
