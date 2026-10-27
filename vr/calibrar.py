#!/usr/bin/env python3
"""
Fase 3: calibração da VR. Fixa a banda ou a CPU da upf4 em cada ponto, deixa a VR rodar e
guarda as janelas que o exporter escreve no log (linhas [VR]), uma linha de CSV por janela.

Uma medida (1 ponto, 1 repetição):
  1. pausa a VR (USR1): o exporter descarta a janela em andamento, que teria os dois valores;
  2. aplica o valor na upf4 com o mesmo patch do resource_agent e espera valer;
  3. retoma a VR (USR2) e lê do log as janelas escritas depois da retomada, até ter --janelas.

Varia um recurso por vez. O outro fica folgado, no topo da série dele: CPU de 0,4 núcleo na
série de banda e banda de 15 Mbps na de CPU. Em cada repetição, os pontos vão numa ordem
sorteada de novo, para que uma mudança lenta no cluster não fique colada a um valor.
O script não julga o SLA: só guarda os números. O SLA e os pisos saem da análise.

Antes de rodar: leilão desligado (nenhuma sessão exp_ no tmux), ue-vr-07 medindo com health=1
e vídeos parados. Roda na ledzeppelin, numa sessão tmux.

Uso (da raiz do repositório):
  venv/bin/python vr/calibrar.py --recurso banda          # 7 pontos × 5 repetições (~75 min)
  venv/bin/python vr/calibrar.py --recurso cpu            # 6 pontos × 5 repetições (~65 min)
  venv/bin/python vr/calibrar.py --recurso cpu --pontos 0.01 0.4 --repeticoes 1 --janelas 2   # teste curto

No fim, mesmo com Ctrl+C ou erro, a upf4 volta para a CPU (request e limit) e a banda que
tinha antes, e a VR é retomada.
"""

import argparse
import csv
import os
import random
import re
import subprocess
import time
from datetime import datetime
from statistics import median

from kubernetes import client, config

NAMESPACE = "pfsevaldt"
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAIDA_PADRAO = os.path.join(REPO, "metricas_scripts", "resultados", "calibracao")
ANOT_ENTRADA = "qos.projectcalico.org/ingressBandwidth"
ANOT_SAIDA = "qos.projectcalico.org/egressBandwidth"

PONTOS = {"banda": [6, 8, 8.5, 9, 10, 12, 15],             # Mbps
          "cpu": [0.01, 0.02, 0.05, 0.1, 0.2, 0.4]}        # núcleos, múltiplos de 10m (ver Fase 1)
UNIDADE = {"banda": "Mbps", "cpu": "núcleo"}
CPU_FOLGADA = 0.4                                          # núcleo, durante a série de banda
BANDA_FOLGADA = 15                                         # Mbps, durante a série de CPU

PAUSA_S = 3              # depois do USR1: o servidor só para de mandar quando a sessão expira (2 s)
ASSENTAR_BANDA_S = 2     # a anotação vale em menos de 1 s (Fase 1), mas o Calico não devolve status
CICLO_S = 13             # uma janela do exporter: 12 s de cliente mais ~1 s até a próxima
CAMPO = re.compile(r'(\w+)=("[^"]*"|\S+)')                 # chave=valor da linha [VR]

COLUNAS = ["recurso", "valor", "unidade", "repeticao", "medida", "janela", "hora", "tipo",
           "m2p_p95_ms", "net_p95_ms", "hold_p95_ms", "loss_pct", "cont_pct", "thr_mbps", "ctx_inv",
           "health", "cpu_aplicado_m", "banda_anotada", "detalhe"]


# --- Copiadas de vr/validar_cpu.py e vr/validar_banda.py (Fase 1): as mesmas chamadas do leiloeiro ---

def para_mili(valor):
    """'229m' -> 229, '1' -> 1000, '1.5' -> 1500 (formato de CPU do Kubernetes)."""
    if valor is None:
        return None
    if valor.endswith("m"):
        return int(valor[:-1])
    return int(round(float(valor) * 1000))


def pedir_cpu(v1, pod, mili):
    """Mesmo patch do resource_agent.update_pod_cpu: request = limit, pelo subrecurso resize."""
    patch = [
        {"op": "replace", "path": "/spec/containers/0/resources/requests/cpu", "value": f"{mili}m"},
        {"op": "replace", "path": "/spec/containers/0/resources/limits/cpu", "value": f"{mili}m"},
    ]
    v1.patch_namespaced_pod_resize(name=pod, namespace=NAMESPACE, body=patch)


def esperar_status(v1, pod, mili, limite_s):
    """Espera o kubelet aplicar. Devolve (aplicado_m, segundos, motivo_pendente, reinícios)."""
    t0 = time.monotonic()
    while True:
        p = v1.read_namespaced_pod(name=pod, namespace=NAMESPACE)
        cs = p.status.container_statuses[0]
        aplicado = para_mili(((cs.resources and cs.resources.limits) or {}).get("cpu"))
        pendente = [f"{c.reason}: {c.message}" for c in (p.status.conditions or [])
                    if c.type == "PodResizePending" and c.status == "True"]
        decorrido = time.monotonic() - t0
        if aplicado == mili or pendente or decorrido > limite_s:
            return aplicado, decorrido, (pendente[0] if pendente else ""), cs.restart_count
        time.sleep(0.5)


def pedir_banda(v1, pod, mbps):
    """Mesmo patch do resource_agent.update_pod_bandwidth: entrada e saída, em "k"."""
    bw_kilo = int(mbps * 1000)
    patch = {"metadata": {"annotations": {ANOT_ENTRADA: f"{bw_kilo}k", ANOT_SAIDA: f"{bw_kilo}k"}}}
    v1.patch_namespaced_pod(name=pod, namespace=NAMESPACE, body=patch)


# --- UPF ---

def aplicar(v1, pod, recurso, valor):
    """Aplica um ponto na UPF e espera valer."""
    if recurso == "cpu":
        mili = int(round(valor * 1000))
        pedir_cpu(v1, pod, mili)
        aplicado, seg, pendente, _ = esperar_status(v1, pod, mili, 30)
        if aplicado != mili:
            raise RuntimeError(f"o kubelet não aplicou {mili}m em {seg:.0f} s (está em {aplicado}m) {pendente}")
    else:
        pedir_banda(v1, pod, valor)
        time.sleep(ASSENTAR_BANDA_S)


def estado_upf(v1, pod):
    """O que está valendo na UPF: a CPU que o kubelet aplicou (m) e a anotação de banda de saída."""
    p = v1.read_namespaced_pod(name=pod, namespace=NAMESPACE)
    cs = p.status.container_statuses[0]
    cpu = para_mili(((cs.resources and cs.resources.limits) or {}).get("cpu"))
    return cpu, (p.metadata.annotations or {}).get(ANOT_SAIDA)


def devolver(v1, pod, original):
    """Devolve a CPU (request e limit, cada um no seu valor) e a banda que a UPF tinha antes."""
    patch = [
        {"op": "replace", "path": "/spec/containers/0/resources/requests/cpu", "value": original["cpu_request"]},
        {"op": "replace", "path": "/spec/containers/0/resources/limits/cpu", "value": original["cpu_limit"]},
    ]
    v1.patch_namespaced_pod_resize(name=pod, namespace=NAMESPACE, body=patch)
    v1.patch_namespaced_pod(name=pod, namespace=NAMESPACE, body={"metadata": {"annotations": original["banda"]}})


# --- VR ---

def sinal_vr(ue, sinal, check=True):
    """USR1 pausa e USR2 retoma o exporter (PID 1 do vr-client; o kill é o do sh)."""
    subprocess.run(["kubectl", "exec", "-n", NAMESPACE, f"deploy/{ue}", "-c", "vr-client", "--",
                    "sh", "-c", f"kill -{sinal} 1"], capture_output=True, text=True, timeout=30, check=check)


def janelas_do_log(ue, linhas):
    """Janelas [VR] escritas depois da última retomada, entre as últimas `linhas` do log do vr-client."""
    saida = subprocess.run(["kubectl", "logs", "-n", NAMESPACE, f"deploy/{ue}", "-c", "vr-client",
                            f"--tail={linhas}", "--timestamps"],
                           capture_output=True, text=True, timeout=30, check=True).stdout
    janelas = []
    for linha in saida.splitlines():
        hora, _, texto = linha.partition(" ")              # o --timestamps põe a hora real na frente
        if "[COMANDO] retomada" in texto:
            janelas = []                                   # o que veio antes da retomada é de outro valor
        elif "] [VR] " in texto:
            campos = texto.split("] [VR] ", 1)[1]
            primeiro = campos.split(" ", 1)[0]
            tipo = "normal" if "=" in primeiro else primeiro   # sem_quadros, falha_cliente, servidor_fora_do_ar
            janelas.append({"hora": hora, "tipo": tipo, **dict(CAMPO.findall(campos)),
                            "detalhe": "" if tipo == "normal" else campos})
    return janelas


def coletar(ue, n):
    """Espera o exporter escrever n janelas depois da retomada e devolve as n primeiras."""
    limite_s = 2 * n * CICLO_S + 30
    t0 = time.monotonic()
    while True:
        time.sleep(15)
        janelas = janelas_do_log(ue, n + 20)
        if len(janelas) >= n:
            return janelas[:n]
        if time.monotonic() - t0 > limite_s:
            raise RuntimeError(f"só {len(janelas)} de {n} janelas em {limite_s} s: o túnel ou o exporter caiu?")


def resumo(janelas):
    """Uma linha para acompanhar no tmux: mediana do net_p95, pior perda e janelas com health=1."""
    net = [float(j["net_p95_ms"]) for j in janelas if j["tipo"] == "normal"]
    perda = [float(j["loss_pct"]) for j in janelas if "loss_pct" in j]
    saude = sum(j.get("health") == "1" for j in janelas)
    net_txt = f"{median(net):.2f} ms" if net else "-"
    perda_txt = f"{max(perda):g}%" if perda else "-"
    return (f"{len(janelas)} janelas ({len(net)} normais) | net_p95 mediana {net_txt} | "
            f"perda máx {perda_txt} | health {saude}/{len(janelas)}")


def checar_antes(ue):
    """Aborta se o leilão estiver rodando (ele sobrescreveria a UPF) ou se a VR não estiver medindo direito."""
    sessoes = subprocess.run(["tmux", "ls"], capture_output=True, text=True).stdout
    exp = [s.split(":")[0] for s in sessoes.splitlines() if s.startswith("exp_")]
    if exp:
        raise SystemExit(f"[ERRO] Experimento rodando no tmux ({', '.join(exp)}): o leilão mexeria na UPF.")
    ultimas = janelas_do_log(ue, 50)
    if not ultimas or ultimas[-1].get("health") != "1":
        raise SystemExit(f"[ERRO] A última janela da VR não tem health=1: {ultimas[-1] if ultimas else 'nenhuma janela'}")


def main():
    ap = argparse.ArgumentParser(description="Fase 3: calibração da VR (banda ou CPU da upf4)")
    ap.add_argument("--recurso", required=True, choices=["banda", "cpu"], help="o recurso que varia")
    ap.add_argument("--pontos", type=float, nargs="+",
                    help="valores a medir (banda em Mbps, CPU em núcleos); padrão: os do plano")
    ap.add_argument("--repeticoes", type=int, default=5, help="medidas por ponto")
    ap.add_argument("--janelas", type=int, default=9, help="janelas do exporter por medida (~13 s cada)")
    ap.add_argument("--upf", default="upf4", help="label app da UPF (padrão: upf4)")
    ap.add_argument("--ue", default="ue-vr-07", help="Deployment do UE de VR")
    ap.add_argument("--saida", default=SAIDA_PADRAO, help="pasta do CSV")
    args = ap.parse_args()
    pontos = args.pontos or PONTOS[args.recurso]
    unidade = UNIDADE[args.recurso]

    checar_antes(args.ue)
    config.load_kube_config()
    v1 = client.CoreV1Api()
    pods = v1.list_namespaced_pod(namespace=NAMESPACE, label_selector=f"app={args.upf}").items
    if not pods:
        raise SystemExit(f"[ERRO] Nenhum pod com app={args.upf} em {NAMESPACE}")
    pod = pods[0].metadata.name
    rec = pods[0].spec.containers[0].resources
    anot = pods[0].metadata.annotations or {}
    original = {"cpu_request": rec.requests["cpu"], "cpu_limit": rec.limits["cpu"],
                "banda": {ANOT_ENTRADA: anot.get(ANOT_ENTRADA), ANOT_SAIDA: anot.get(ANOT_SAIDA)}}

    # Ordem intercalada: cada repetição passa por todos os pontos, numa ordem sorteada de novo
    ordem = [(rep, valor) for rep in range(1, args.repeticoes + 1) for valor in random.sample(pontos, len(pontos))]
    minutos = len(ordem) * (args.janelas * CICLO_S + 20) / 60
    print(f"[INIT] {pod}: CPU {original['cpu_request']}/{original['cpu_limit']} (request/limit), "
          f"banda {original['banda'][ANOT_SAIDA]}. {len(ordem)} medidas de {args.janelas} janelas, ~{minutos:.0f} min.")
    for rep in range(1, args.repeticoes + 1):
        print(f"[ORDEM] rep {rep}: " + " ".join(f"{v:g}" for r, v in ordem if r == rep))

    os.makedirs(args.saida, exist_ok=True)
    arquivo = os.path.join(args.saida, f"calib_{args.recurso}_{datetime.now():%Y%m%d_%H%M%S}.csv")
    with open(arquivo, "w", newline="") as f:
        csv.DictWriter(f, fieldnames=COLUNAS).writeheader()

    try:
        # O outro recurso fica folgado, no topo da série dele
        if args.recurso == "banda":
            aplicar(v1, pod, "cpu", CPU_FOLGADA)
        else:
            aplicar(v1, pod, "banda", BANDA_FOLGADA)

        for i, (rep, valor) in enumerate(ordem, 1):
            sinal_vr(args.ue, "USR1")                      # pausa: a janela em andamento é descartada
            time.sleep(PAUSA_S)
            aplicar(v1, pod, args.recurso, valor)
            cpu_m, banda = estado_upf(v1, pod)
            sinal_vr(args.ue, "USR2")                      # retoma: a próxima janela já começa com o valor novo
            janelas = coletar(args.ue, args.janelas)

            with open(arquivo, "a", newline="") as f:     # grava a cada medida: uma queda não perde o que já foi medido
                w = csv.DictWriter(f, fieldnames=COLUNAS, extrasaction="ignore")
                for n, j in enumerate(janelas, 1):
                    w.writerow({**j, "recurso": args.recurso, "valor": f"{valor:g}", "unidade": unidade,
                                "repeticao": rep, "medida": i, "janela": n,
                                "cpu_aplicado_m": cpu_m, "banda_anotada": banda})
            print(f"[{i}/{len(ordem)}] {args.recurso} {valor:g} {unidade} rep {rep}: {resumo(janelas)}", flush=True)
    finally:
        devolver(v1, pod, original)
        sinal_vr(args.ue, "USR2", check=False)             # se parou no meio de uma pausa
        print(f"[FIM] {pod} devolvida para CPU {original['cpu_request']}/{original['cpu_limit']} e banda "
              f"{original['banda'][ANOT_SAIDA]}; VR retomada.")
        print(f"[CSV] {arquivo}")


if __name__ == "__main__":
    main()
