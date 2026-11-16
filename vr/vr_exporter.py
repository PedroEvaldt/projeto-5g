#!/usr/bin/env python3
"""
Exporter da VR: processo principal (PID 1) do container vr-client do UE de VR.

Roda o photon_client pelo pp_launcher (para ter o buffer forçado), escreve uma linha de log
por janela no formato do player DASH ([T+...s] [VR] ...) e publica a última janela em
/metrics, no formato texto do Prometheus. Só usa a biblioteca padrão.

A sessão é contínua (desde 17/11/2026): um cliente só, do começo ao fim de cada trecho ativo,
que imprime um resumo a cada VR_WINDOW_S segundos (--report-interval). Antes, o exporter
subia um cliente novo a cada janela (sessão e numeração novas, ~0,7 s sem tráfego entre uma e
outra, e 2 s de aquecimento fora da medida em cada uma). O aquecimento agora só existe no
começo da sessão.

health mede o instrumento, não a rede: 1 quando o cliente rodou com carimbo do kernel, buffer
forçado e sem descarte nos sockets do pod. Perda e continuidade ficam fora, porque são o
efeito do leilão que se quer medir. Cliente que não chegou a medir (DNS, erro) ou servidor
fora do ar dão health=0, e não contam como perda. Se a medida parar por um erro não previsto,
o processo sai com código 1 e o Kubernetes reinicia o container, em vez de o /metrics seguir
servindo a última janela.

Sinais (kubectl exec ... -c vr-client -- sh -c 'kill -USR1 1'; o kill é o do sh, a imagem slim não tem o programa):
  USR1  pausa: encerra a sessão (a janela em andamento é descartada) e para de gerar tráfego
  USR2  retoma, com uma sessão nova
  TERM  encerra o cliente e sai (sem tratador, o PID 1 ignoraria o TERM)
"""

import fcntl
import json
import os
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LANCADOR = os.environ.get("VR_LAUNCHER", "/opt/vr/pp_launcher.py")
SERVIDOR = os.environ.get("PP_SERVER", "vr-app-01:9100")
SAUDE_SERVIDOR = os.environ.get("VR_SERVER_HEALTH", "http://vr-app-01:9101/healthz")
IFACE = os.environ.get("VR_IFACE", "uesimtun0")
JANELA = os.environ.get("VR_WINDOW_S", "12")
AQUECIMENTO = os.environ.get("VR_WARMUP_S", "2")
PORTA = int(os.environ.get("VR_METRICS_PORT", "9200"))
FATIA = os.environ.get("VR_SLICE", "slice04")
RCVBUF_FORCADO = 16 << 20                                  # 16777216: os 8 MB do PhotonPath, dobrados pelo kernel

valores = {"vr_health_ok": 0, "vr_paused": 0}
trava = threading.Lock()
pausado = threading.Event()
inicio = time.time()
cliente = None                                             # processo da sessão em andamento


def pausar(*_):
    pausado.set()
    p = cliente
    if p is not None:
        p.terminate()                                      # sem BYE: o servidor para de mandar quando a sessão expira (2 s)
    print("[COMANDO] pausa", flush=True)


def retomar(*_):
    pausado.clear()
    print("[COMANDO] retomada", flush=True)


def encerrar(*_):
    p = cliente
    if p is not None:
        p.terminate()
    print("[COMANDO] encerrando", flush=True)
    sys.exit(0)


signal.signal(signal.SIGUSR1, pausar)
signal.signal(signal.SIGUSR2, retomar)
signal.signal(signal.SIGTERM, encerrar)


def definir(**novos):
    """Atualiza os números do /metrics, sem publicar None (o Prometheus não aceita)."""
    with trava:
        valores.update({k: v for k, v in novos.items() if v is not None})


def ip_da_interface(nome):
    """IP de uma interface sem o comando ip (ioctl SIOCGIFADDR = 0x8915)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        return socket.inet_ntoa(fcntl.ioctl(s.fileno(), 0x8915, struct.pack("256s", nome[:15].encode()))[20:24])


def servidor_no_ar():
    """Pergunta ao /healthz do servidor, pela rede do pod (fora do túnel)."""
    try:
        with urllib.request.urlopen(SAUDE_SERVIDOR, timeout=2) as r:
            return r.status == 200
    except OSError:
        return False


def janela(s):
    """Uma janela do cliente (o JSON de um intervalo): a linha [VR] do log e os números do /metrics.
    Devolve False se a janela veio sem nenhum quadro."""
    t = time.time() - inicio
    h = s["health"]
    ok = int(bool(h["rx_timestamp_kernel"]) and (h["sock_rcvbuf_bytes"] or 0) >= RCVBUF_FORCADO
             and not h["dl_sock_drops"] and not h["udp_rcvbuf_errors"])

    if not s["frames_in_stats"]:
        # Nenhum quadro completo na janela. Com o servidor de pé, é a fatia sem serviço (QoE);
        # com o servidor fora do ar, é falha do testbed.
        if not servidor_no_ar():
            print(f"[T+{t:.1f}s] [VR] servidor_fora_do_ar health=0", flush=True)
            definir(vr_health_ok=0, vr_paused=0)
            return False
        print(f"[T+{t:.1f}s] [VR] sem_quadros loss_pct=100 cont_pct=0 health={ok}", flush=True)
        definir(vr_loss_pct=100.0, vr_continuity_pct=0.0, vr_health_ok=ok, vr_paused=0)
        return False

    ms = lambda bloco, q: round(s[bloco][q] / 1000, 2)
    print(f"[T+{t:.1f}s] [VR] m2p_p95_ms={ms('m2p_us', 'p95')} net_p95_ms={ms('net_rtt_us', 'p95')} "
          f"hold_p95_ms={ms('hold_us', 'p95')} loss_pct={s['dl_packet_loss_pct']} "
          f"cont_pct={s['dl_continuity_pct']} thr_mbps={s['dl_throughput_mbps']} "
          f"ctx_inv={h['ctxt_switches_involuntary']} health={ok}", flush=True)
    definir(vr_m2p_p95_us=s["m2p_us"]["p95"], vr_m2p_p99_us=s["m2p_us"]["p99"],
            vr_net_p95_us=s["net_rtt_us"]["p95"], vr_hold_p95_us=s["hold_us"]["p95"],
            vr_loss_pct=s["dl_packet_loss_pct"], vr_continuity_pct=s["dl_continuity_pct"],
            vr_throughput_mbps=s["dl_throughput_mbps"], vr_health_ok=ok, vr_paused=0)
    return True


def tunel_mudou(ip):
    try:
        return ip_da_interface(IFACE) != ip
    except OSError:
        return True


def medir():
    global cliente
    while True:
        if pausado.is_set():
            definir(vr_paused=1)
            time.sleep(0.2)
            continue
        try:
            ip = ip_da_interface(IFACE)
        except OSError:
            time.sleep(1)                                  # túnel ainda não subiu
            continue

        # Uma sessão: o cliente roda até ser encerrado (--duration 0) e imprime uma linha JSON por janela.
        cliente = p = subprocess.Popen(
            ["python3", "-u", LANCADOR, "photon_client.py", "--server", SERVIDOR, "--bind", ip,
             "--duration", "0", "--report-interval", JANELA, "--warmup", AQUECIMENTO, "--label", FATIA],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        erros = deque(maxlen=1)                            # a última linha do stderr, para o log de falha
        threading.Thread(target=lambda: erros.extend(l.strip() for l in p.stderr if l.strip()), daemon=True).start()
        if pausado.is_set():                               # USR1 chegou entre a checagem e o Popen
            p.terminate()
        tunel_novo = False
        for linha in p.stdout:
            if pausado.is_set():                           # janela que fechou junto com a pausa: descartada
                break
            try:
                com_quadros = janela(json.loads(linha))
            except (json.JSONDecodeError, KeyError, TypeError):
                continue                                   # linha que não é uma janela
            if not com_quadros and tunel_mudou(ip):
                # O cliente está preso ao IP de um túnel que caiu ou foi refeito: só uma sessão nova volta a medir.
                tunel_novo = True
                p.terminate()
                break
        rc, cliente = p.wait(), None
        if pausado.is_set():
            continue
        if tunel_novo:
            print("[vr_exporter] o túnel mudou; sessão nova", flush=True)
            continue

        # O cliente saiu sozinho (DNS, erro no código, o túnel caiu...): falha do instrumento, não da fatia.
        t = time.time() - inicio
        msg = erros[-1] if erros else "sem saída"
        print(f'[T+{t:.1f}s] [VR] falha_cliente rc={rc} erro="{msg}" health=0', flush=True)
        definir(vr_health_ok=0, vr_paused=0)
        time.sleep(1)                                      # não entrar em laço apertado se falhar de cara


def medir_ou_sair():
    """Roda a medida; se ela morrer por erro não previsto, derruba o processo inteiro."""
    try:
        medir()
    except Exception:
        traceback.print_exc()
        print("[vr_exporter] a medida parou por erro; saindo para o Kubernetes reiniciar", flush=True)
        os._exit(1)                                        # sys.exit numa thread só encerraria a thread


class Metricas(BaseHTTPRequestHandler):
    def do_GET(self):
        with trava:
            corpo = "".join(f'# TYPE {k} gauge\n{k}{{fatia="{FATIA}"}} {v}\n' for k, v in valores.items()).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.end_headers()
        self.wfile.write(corpo)

    def log_message(self, *_):
        pass                                               # sem uma linha de log por coleta do Prometheus


if __name__ == "__main__":
    print(f"[vr_exporter] fatia={FATIA} servidor={SERVIDOR} janela={JANELA}s aquecimento={AQUECIMENTO}s "
          f"iface={IFACE} metrics=:{PORTA}", flush=True)
    threading.Thread(target=medir_ou_sair, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORTA), Metricas).serve_forever()
