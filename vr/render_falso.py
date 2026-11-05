#!/usr/bin/env python3
"""
Render de mentira: fala o contrato do Unity sem renderizar nada.

Lê as poses repassadas em :9300, "renderiza" dormindo --render-ms e devolve um quadro PPRF com
--enc-bytes de dados a cada período de 1/--fps s, respondendo à última pose recebida. Serve para
testar o testar_render.py e, na etapa 3 do plano, o photon_server com --render-sock, sem GPU.

Uso:
  python3 vr/render_falso.py --listen 9300 --dest 127.0.0.1:9301 --fps 60 --render-ms 3 --enc-bytes 15000
"""

import argparse
import socket
import struct
import threading
import time

POSE_HDR = struct.Struct("!4sIQQI")
POSE_MAGIC = b"PPOS"
RENDER_HDR = struct.Struct("!4sQQQII")
RENDER_MAGIC = b"PPRF"


def main():
    p = argparse.ArgumentParser(description="render falso (o Unity de mentira)")
    p.add_argument("--listen", type=int, default=9300)
    p.add_argument("--dest", default="127.0.0.1:9301")
    p.add_argument("--fps", type=float, default=60.0)
    p.add_argument("--render-ms", type=float, default=3.0)
    p.add_argument("--enc-bytes", type=int, default=15000)
    a = p.parse_args()
    host, _, porta = a.dest.rpartition(":")
    dest = (host, int(porta))

    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(("0.0.0.0", a.listen))
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    ultima = {"seq": 0, "t": 0, "recv": 0}
    trava = threading.Lock()

    def receber():
        while True:
            dados, _ = rx.recvfrom(2048)
            if len(dados) < POSE_HDR.size + 8:
                continue
            magic, _, seq, t_ns, _ = POSE_HDR.unpack_from(dados)
            if magic != POSE_MAGIC:
                continue
            (recv_ns,) = struct.unpack_from("!Q", dados, len(dados) - 8)
            with trava:
                ultima.update(seq=seq, t=t_ns, recv=recv_ns)

    threading.Thread(target=receber, daemon=True).start()
    print(f"[render_falso] poses em :{a.listen}, quadros -> {dest[0]}:{dest[1]}, {a.fps:.0f} fps, "
          f"render {a.render_ms} ms, {a.enc_bytes} B por quadro", flush=True)
    corpo = bytes((i * 7 + 1) & 0xFF for i in range(a.enc_bytes))      # não zeros
    periodo = 1.0 / a.fps
    prox = time.perf_counter()
    while True:
        prox += periodo
        with trava:
            seq, t_ns, recv_ns = ultima["seq"], ultima["t"], ultima["recv"]
        if seq:
            t0 = time.perf_counter()
            time.sleep(a.render_ms / 1000)
            render_us = int((time.perf_counter() - t0) * 1e6)
            tx.sendto(RENDER_HDR.pack(RENDER_MAGIC, seq, t_ns, recv_ns, render_us, len(corpo)) + corpo, dest)
        espera = prox - time.perf_counter()
        if espera > 0:
            time.sleep(espera)
        else:
            prox = time.perf_counter()


if __name__ == "__main__":
    main()
