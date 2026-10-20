#!/usr/bin/env python3
"""
Fase 1.2: valida a alavanca de banda do leilão (anotação do Calico com o pod da UPF rodando).

Para cada valor, roda um iperf3 de download (-R) pelo túnel do UE de teste e, no meio dele,
aplica na UPF o mesmo patch do resource_agent.update_pod_bandwidth. Mede:
  1. a vazão antes e depois da mudança (média dos intervalos de 0,5 s do iperf);
  2. quanto tempo depois do patch a vazão já estava mais perto do novo patamar que do antigo.

Pré-requisito: bash vr/subir_teste_banda.sh (UE ue-iperf-08 na slice04 e servidor iperf01).

Uso (da raiz do repositório):
  venv/bin/python vr/validar_banda.py                       # upf4, valores padrão
  venv/bin/python vr/validar_banda.py --valores 5 15 2      # outros valores, em Mbps

No fim, a UPF volta para as anotações que tinha antes. O tempo até o efeito compara o
relógio do PC com o do pod; os dois precisam estar sincronizados (medido: ~0,1 s).
"""

import argparse
import csv
import json
import os
import subprocess
import time
from datetime import datetime
from statistics import mean

from kubernetes import client, config

NAMESPACE = "pfsevaldt"
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAIDA_PADRAO = os.path.join(REPO, "metricas_scripts", "resultados", "validacao")
ANOT_ENTRADA = "qos.projectcalico.org/ingressBandwidth"
ANOT_SAIDA = "qos.projectcalico.org/egressBandwidth"


def pedir_banda(v1, pod, mbps):
    """Mesmo patch do resource_agent.update_pod_bandwidth: entrada e saída, em "k"."""
    bw_kilo = int(mbps * 1000)
    patch = {"metadata": {"annotations": {ANOT_ENTRADA: f"{bw_kilo}k", ANOT_SAIDA: f"{bw_kilo}k"}}}
    v1.patch_namespaced_pod(name=pod, namespace=NAMESPACE, body=patch)


def ip_do_ue(ue):
    """IP do túnel do UE, sem o "/16"."""
    saida = subprocess.run(["kubectl", "exec", "-n", NAMESPACE, f"deploy/{ue}", "-c", "ue", "--",
                            "ip", "-4", "-o", "addr", "show", "uesimtun0"],
                           capture_output=True, text=True, check=True).stdout
    return saida.split()[3].split("/")[0]


def iniciar_iperf(ue, ip, servidor, duracao):
    """Download pelo túnel (-B: sai pelo IP do UE; -R: o servidor manda). A 1ª linha é a hora de início."""
    cmd = ["kubectl", "exec", "-n", NAMESPACE, f"deploy/{ue}", "-c", "iperf-client", "--", "sh", "-c",
           f"date +%s.%N; iperf3 -c {servidor} -B {ip} -R -t {duracao} -i 0.5 -J"]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def medir_mudanca(v1, pod, mbps, args, ip):
    proc = iniciar_iperf(args.ue, ip, args.servidor, args.duracao_s)
    time.sleep(args.antes_s)
    t_patch = time.time()
    pedir_banda(v1, pod, mbps)
    saida, erro = proc.communicate(timeout=args.duracao_s + 30)
    primeira, _, resto = saida.partition("\n")
    dados = json.loads(resto)
    if "error" in dados:
        raise RuntimeError(f"iperf3: {dados['error']} {erro.strip()}")

    rel = t_patch - float(primeira)                     # segundos entre o início do iperf e o patch
    iv = [(i["sum"]["start"], i["sum"]["end"], i["sum"]["bits_per_second"] / 1e6) for i in dados["intervals"]]
    antes = mean(x for s, e, x in iv if s >= 1 and e <= rel)            # o 1º segundo é a partida do TCP
    depois = mean(x for s, e, x in iv if s >= rel + args.assentar_s)
    mudou = abs(depois - antes) > 0.1 * antes
    t_efeito = None
    if mudou:
        t_efeito = next((round(e - rel, 1) for s, e, x in iv
                         if e > rel and abs(x - depois) < abs(x - antes)), None)
    return {"pedido_mbps": mbps, "antes_mbps": round(antes, 2), "depois_mbps": round(depois, 2),
            "depois_sobre_pedido": round(depois / mbps, 2), "mudou": mudou, "t_efeito_s": t_efeito,
            "patch_aos_s": round(rel, 1)}


def main():
    ap = argparse.ArgumentParser(description="Fase 1.2: valida a anotação de banda do Calico numa UPF")
    ap.add_argument("--upf", default="upf4", help="label app da UPF (padrão: upf4)")
    ap.add_argument("--ue", default="ue-iperf-08", help="Deployment do UE de iperf")
    ap.add_argument("--servidor", default="iperf01", help="Service do servidor iperf3")
    ap.add_argument("--valores", type=float, nargs="+", default=[5, 15, 2, 8],
                    help="bandas a pedir, em Mbps, na ordem (sobe e desce de propósito)")
    ap.add_argument("--duracao-s", type=int, default=20, help="duração de cada iperf")
    ap.add_argument("--antes-s", type=float, default=6, help="segundos de iperf antes do patch")
    ap.add_argument("--assentar-s", type=float, default=4, help="segundos ignorados depois do patch")
    ap.add_argument("--saida", default=SAIDA_PADRAO, help="pasta do CSV")
    args = ap.parse_args()

    config.load_kube_config()
    v1 = client.CoreV1Api()
    pods = v1.list_namespaced_pod(namespace=NAMESPACE, label_selector=f"app={args.upf}").items
    if not pods:
        raise SystemExit(f"[ERRO] Nenhum pod com app={args.upf} em {NAMESPACE}")
    pod = pods[0].metadata.name
    anot = pods[0].metadata.annotations or {}
    original = {ANOT_ENTRADA: anot.get(ANOT_ENTRADA), ANOT_SAIDA: anot.get(ANOT_SAIDA)}
    ip = ip_do_ue(args.ue)
    print(f"[INIT] {pod}: banda atual {original}. UE {args.ue} com IP {ip}. Testando {args.valores} Mbps.")

    linhas = []
    try:
        for mbps in args.valores:
            m = medir_mudanca(v1, pod, mbps, args, ip)
            linhas.append({"upf": args.upf, "pod": pod, **m})
            print(f"[BANDA] pedido {mbps} Mbps (patch aos {m['patch_aos_s']} s): antes {m['antes_mbps']} -> "
                  f"depois {m['depois_mbps']} Mbps ({m['depois_sobre_pedido']} do pedido) | "
                  f"mudou {m['mudou']} | efeito em {m['t_efeito_s']} s")
    finally:
        v1.patch_namespaced_pod(name=pod, namespace=NAMESPACE, body={"metadata": {"annotations": original}})
        print(f"[FIM] {pod} devolvida para {original}.")

    os.makedirs(args.saida, exist_ok=True)
    arquivo = os.path.join(args.saida, f"banda_{args.upf}_{datetime.now():%Y%m%d_%H%M%S}.csv")
    with open(arquivo, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(linhas[0].keys()))
        w.writeheader()
        w.writerows(linhas)
    print(f"[CSV] {arquivo}")

    # Tabela pronta para colar no docs/validacao-alavancas.md
    print("\n| Pedido | Antes | Depois (iperf) | Depois / pedido | Mudou | Tempo até o efeito |")
    print("| --- | --- | --- | --- | --- | --- |")
    for l in linhas:
        print(f"| {l['pedido_mbps']:g} Mbps | {l['antes_mbps']} Mbps | {l['depois_mbps']} Mbps "
              f"| {l['depois_sobre_pedido']} | {'sim' if l['mudou'] else 'não'} | {l['t_efeito_s']} s |")


if __name__ == "__main__":
    main()
