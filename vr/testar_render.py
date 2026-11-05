#!/usr/bin/env python3
"""
Medidor do render: faz o papel do PhotonPath para testar o Unity sozinho, no PC ou num pod.

Manda poses no formato que o photon_server vai repassar (PPOS de 128 B + recv_ns), lê os quadros
PPRF que o render devolve e diz, a cada 5 s, se o render está entregando 60 quadros por segundo,
quanto cada um custou e se cabe nos 14 pacotes do perfil vr8. Com --salvar, grava o H.264 cru para
você ver o modelo renderizado (ffplay arquivo.h264).

Contrato (docs/plano-render-real.md, "O contrato entre o PhotonPath e o render"):
  pose repassada  (-> render, porta 9300): PPOS 128 B como chega do cliente, mais !Q recv_ns
    PPOS = !4sIQQI (magic PPOS, session, seq, t_client_ns, flags) + padding !I7f (índice, x y z, qx qy qz qw)
  quadro          (<- render, porta 9301): !4sQQQII (magic PPRF, pose_seq, t_pose_ns, recv_ns, render_us, enc_bytes) + bytes

Uso:
  python3 vr/testar_render.py --dest 127.0.0.1:9300 --listen 9301 --passeio vr/passeio.csv --duracao 30 --salvar /tmp/modelo.h264
"""

import argparse
import csv
import socket
import struct
import sys
import threading
import time

POSE_HDR = struct.Struct("!4sIQQI")
POSE_PAD = struct.Struct("!I7f")
POSE_BYTES = 128
POSE_MAGIC = b"PPOS"
RENDER_HDR = struct.Struct("!4sQQQII")
RENDER_MAGIC = b"PPRF"
PAYLOAD_POR_QUADRO = 14 * (1200 - 48)          # 16128 B: o que cabe nos 14 pacotes do perfil vr8


def pct(valores, q):
    if not valores:
        return 0
    v = sorted(valores)
    k = (len(v) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def ler_passeio(caminho):
    with open(caminho) as f:
        linhas = [tuple(float(x) for x in (r["t_s"], r["x"], r["y"], r["z"], r["qx"], r["qy"], r["qz"], r["qw"]))
                  for r in csv.DictReader(f)]
    if len(linhas) < 2:
        sys.exit(f"{caminho}: precisa de pelo menos 2 amostras")
    return linhas


def amostra(passeio, t):
    """Amostra interpolada no tempo t, em laço: depois da última volta ao início."""
    dur = passeio[-1][0] + (passeio[-1][0] - passeio[-2][0])      # a última amostra emenda na primeira
    t = t % dur
    lo, hi = 0, len(passeio) - 1
    while lo < hi:                                                  # busca binária pelo último t_s <= t
        m = (lo + hi + 1) // 2
        if passeio[m][0] <= t:
            lo = m
        else:
            hi = m - 1
    a = passeio[lo]
    b = passeio[lo + 1] if lo + 1 < len(passeio) else passeio[0]
    tb = b[0] if lo + 1 < len(passeio) else dur
    f = 0.0 if tb == a[0] else (t - a[0]) / (tb - a[0])
    return lo, [a[i] + (b[i] - a[i]) * f for i in range(1, 8)]      # sem normalizar o quaternion: o Unity normaliza


class Medidor:
    def __init__(self, a):
        self.a = a
        host, _, porta = a.dest.rpartition(":")
        self.dest = (host, int(porta))
        self.sock_tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock_rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock_rx.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 << 20)
        self.sock_rx.bind(("0.0.0.0", a.listen))
        self.sock_rx.settimeout(0.2)
        self.passeio = ler_passeio(a.passeio)
        self.rodando = True
        self.trava = threading.Lock()
        self.poses = {}                                             # seq -> t_envio (perf_counter)
        self.quadros = []                                           # (t_chegada, pose_seq, render_us, enc_bytes, idade_s)
        self.total_quadros = 0
        self.total_bytes = 0
        self.arquivo = open(a.salvar, "wb") if a.salvar else None

    def tx(self):
        periodo = 1.0 / self.a.pose_hz
        seq = 0
        inicio = prox = time.perf_counter()
        fim = inicio + self.a.duracao
        while self.rodando and time.perf_counter() < fim:
            prox += periodo
            seq += 1
            agora = time.perf_counter()
            idx, v = amostra(self.passeio, agora - inicio)
            t_ns = time.time_ns()
            pad = POSE_PAD.pack(idx, *v)
            pose = POSE_HDR.pack(POSE_MAGIC, 7, seq, t_ns, 0) + pad + bytes(POSE_BYTES - POSE_HDR.size - POSE_PAD.size)
            recv_ns = time.monotonic_ns()                            # o que o photon_server anexaria (carimbo de chegada)
            with self.trava:
                self.poses[seq] = agora
                if len(self.poses) > 2000:
                    for k in sorted(self.poses)[:1000]:
                        self.poses.pop(k, None)
            try:
                self.sock_tx.sendto(pose + struct.pack("!Q", recv_ns), self.dest)
            except OSError as e:
                print(f"erro ao mandar pose: {e}", file=sys.stderr)
            espera = prox - time.perf_counter()
            if espera > 0:
                time.sleep(espera)
            else:
                prox = time.perf_counter()
        self.rodando = False

    def rx(self):
        buf = bytearray(65536)
        while self.rodando:
            try:
                n, _ = self.sock_rx.recvfrom_into(buf)
            except socket.timeout:
                continue
            if n < RENDER_HDR.size:
                continue
            magic, pose_seq, t_pose_ns, recv_ns, render_us, enc_bytes = RENDER_HDR.unpack_from(buf)
            if magic != RENDER_MAGIC:
                continue
            agora = time.perf_counter()
            dados = bytes(buf[RENDER_HDR.size:RENDER_HDR.size + enc_bytes])
            with self.trava:
                t_pose = self.poses.get(pose_seq)
                idade = (agora - t_pose) if t_pose is not None else None
                self.quadros.append((agora, pose_seq, render_us, len(dados), idade))
                self.total_quadros += 1
                self.total_bytes += len(dados)
            if self.arquivo:
                self.arquivo.write(dados)

    def relatorio(self, janela, final=False):
        with self.trava:
            q = [x for x in self.quadros if x[0] >= janela]
            self.quadros = q[:]
        if not q:
            print("[render] nenhum quadro na janela: o render está de pé e mandando para esta porta?", flush=True)
            return
        dt = max(1e-6, q[-1][0] - q[0][0]) if len(q) > 1 else 1.0
        fps = (len(q) - 1) / dt if len(q) > 1 else 0
        render = [x[2] / 1000 for x in q]
        tam = [x[3] for x in q]
        idades = [x[4] * 1000 for x in q if x[4] is not None]
        estourados = sum(1 for t in tam if t > PAYLOAD_POR_QUADRO)
        seqs = [x[1] for x in q]
        repetidos = sum(1 for i in range(1, len(seqs)) if seqs[i] == seqs[i - 1])
        print(f"[render] fps={fps:.1f} render_ms p50={pct(render, .5):.2f} p95={pct(render, .95):.2f} max={max(render):.2f} "
              f"enc_kB p50={pct(tam, .5) / 1000:.1f} max={max(tam) / 1000:.1f} estourados={estourados}/{len(q)} "
              f"idade_ms p50={pct(idades, .5):.1f} p95={pct(idades, .95):.1f} pose_repetida={repetidos}", flush=True)
        if final:
            print(f"[render] total: {self.total_quadros} quadros, {self.total_bytes / 1e6:.1f} MB"
                  + (f", H.264 em {self.a.salvar} (ffplay {self.a.salvar})" if self.a.salvar else ""), flush=True)

    def rodar(self):
        print(f"[render] poses -> {self.dest[0]}:{self.dest[1]} a {self.a.pose_hz:.0f} Hz, quadros <- :{self.a.listen}, "
              f"{self.a.duracao:.0f} s, passeio {self.a.passeio} ({len(self.passeio)} amostras)", flush=True)
        threading.Thread(target=self.rx, daemon=True).start()
        threading.Thread(target=self.tx, daemon=True).start()
        janela = time.perf_counter()
        while self.rodando:
            time.sleep(0.1)
            if time.perf_counter() - janela >= 5.0:
                self.relatorio(janela)
                janela = time.perf_counter()
        time.sleep(0.3)
        self.relatorio(janela, final=True)
        if self.arquivo:
            self.arquivo.close()


def main():
    p = argparse.ArgumentParser(description="medidor do render (faz o papel do PhotonPath)")
    p.add_argument("--dest", default="127.0.0.1:9300", help="host:porta onde o render recebe as poses")
    p.add_argument("--listen", type=int, default=9301, help="porta onde este script recebe os quadros")
    p.add_argument("--passeio", default="vr/passeio.csv")
    p.add_argument("--duracao", type=float, default=30.0)
    p.add_argument("--pose-hz", type=float, default=250.0)
    p.add_argument("--salvar", default="", help="grava o H.264 cru aqui, para ver com ffplay")
    Medidor(p.parse_args()).rodar()


if __name__ == "__main__":
    main()
