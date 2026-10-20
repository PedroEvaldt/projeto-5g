#!/usr/bin/env python3
"""
Fase 1.1: valida a alavanca de CPU do leilão (resize da UPF com o pod rodando).

Para cada valor pedido, aplica o mesmo patch do resource_agent.update_pod_cpu e mede:
  1. o que o kubelet diz ter aplicado (status.containerStatuses[0].resources) e quanto demorou;
  2. a cota que o kernel recebeu (cpu.max do cgroup do container);
  3. o efeito: quanta CPU dois laços ocupados conseguem usar dentro do container,
     e em que fração dos períodos do CFS o container foi estrangulado.

Uso (da raiz do repositório):
  venv/bin/python vr/validar_cpu.py                                   # upf4, valores padrão
  venv/bin/python vr/validar_cpu.py --upf upf4 --valores 0.2 0.05     # outros valores, em núcleos

No fim, a UPF volta para a CPU que tinha antes. O padrão é a upf4 porque nenhuma
fatia de vídeo passa por ela: o teste não mexe nos experimentos do artigo.
"""

import argparse
import csv
import os
import subprocess
import time
from datetime import datetime

from kubernetes import client, config

NAMESPACE = "pfsevaldt"
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAIDA_PADRAO = os.path.join(REPO, "metricas_scripts", "resultados", "validacao")

# Roda dentro do container da UPF. Ele é privilegiado e enxerga a raiz do cgroup do nó,
# então o cgroup do container é o caminho que aparece em /proc/self/cgroup.
SCRIPT_CGROUP = r"""
C=/sys/fs/cgroup$(cut -d: -f3 /proc/self/cgroup)
stat_de() { grep "^$1 " $C/cpu.stat | cut -d' ' -f2; }
echo "cpu_max $(cat $C/cpu.max)"
u0=$(stat_de usage_usec); p0=$(stat_de nr_periods); e0=$(stat_de nr_throttled); t0=$(date +%s%N)
for i in 1 2; do timeout {seg} sh -c 'while :; do :; done' & done; wait
u1=$(stat_de usage_usec); p1=$(stat_de nr_periods); e1=$(stat_de nr_throttled); t1=$(date +%s%N)
echo "uso_usec $((u1 - u0))"
echo "parede_usec $(((t1 - t0) / 1000))"
echo "periodos $((p1 - p0))"
echo "estrangulados $((e1 - e0))"
"""


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


def medir_no_container(pod, container, segundos):
    """Lê cpu.max e satura a CPU do container por alguns segundos."""
    cmd = ["kubectl", "exec", "-n", NAMESPACE, pod, "-c", container, "--",
           "sh", "-c", SCRIPT_CGROUP.replace("{seg}", str(segundos))]
    saida = subprocess.run(cmd, capture_output=True, text=True, timeout=segundos + 30)
    m = {}
    for linha in saida.stdout.splitlines():
        chave, _, resto = linha.partition(" ")
        m[chave] = resto
    if "cpu_max" not in m:
        raise RuntimeError(f"kubectl exec falhou: {saida.stderr.strip()}")
    cota, periodo = m["cpu_max"].split()
    return {
        "cpu_max": m["cpu_max"],
        "kernel_m": None if cota == "max" else round(int(cota) / int(periodo) * 1000, 1),
        "medido_m": round(int(m["uso_usec"]) / int(m["parede_usec"]) * 1000, 1),
        "estrangulado_pct": round(int(m["estrangulados"]) / max(1, int(m["periodos"])) * 100, 1),
    }


def main():
    ap = argparse.ArgumentParser(description="Fase 1.1: valida o resize de CPU de uma UPF")
    ap.add_argument("--upf", default="upf4", help="label app da UPF (padrão: upf4)")
    ap.add_argument("--valores", type=float, nargs="+",
                    default=[0.4, 0.1, 0.05, 0.01, 0.229, 0.015],
                    help="CPUs a pedir, em núcleos (0,229 e 0,015 mostram o arredondamento)")
    ap.add_argument("--carga-s", type=int, default=5, help="segundos de laço ocupado por valor")
    ap.add_argument("--espera-s", type=float, default=30, help="tempo máximo esperando o kubelet")
    ap.add_argument("--saida", default=SAIDA_PADRAO, help="pasta do CSV")
    args = ap.parse_args()

    config.load_kube_config()
    v1 = client.CoreV1Api()
    pods = v1.list_namespaced_pod(namespace=NAMESPACE, label_selector=f"app={args.upf}").items
    if not pods:
        raise SystemExit(f"[ERRO] Nenhum pod com app={args.upf} em {NAMESPACE}")
    pod = pods[0].metadata.name
    original = para_mili(pods[0].spec.containers[0].resources.limits["cpu"])
    print(f"[INIT] {pod}: CPU atual {original}m. Testando {args.valores} núcleos.")

    linhas = []
    try:
        for nucleos in args.valores:
            mili = int(round(nucleos * 1000))
            pedir_cpu(v1, pod, mili)
            aplicado, seg, pendente, reinicios = esperar_status(v1, pod, mili, args.espera_s)
            medida = medir_no_container(pod, args.upf, args.carga_s)
            linha = {"upf": args.upf, "pod": pod, "pedido_m": mili, "status_m": aplicado,
                     "t_status_s": round(seg, 1), "pendente": pendente, **medida,
                     "reinicios": reinicios}
            linhas.append(linha)
            print(f"[CPU] pedido {mili}m -> status {aplicado}m em {seg:.1f}s | cpu.max {medida['cpu_max']} "
                  f"({medida['kernel_m']}m) | medido {medida['medido_m']}m | "
                  f"estrangulado {medida['estrangulado_pct']}% | reinícios {reinicios} {pendente}")
    finally:
        pedir_cpu(v1, pod, original)
        print(f"[FIM] {pod} devolvida para {original}m.")

    os.makedirs(args.saida, exist_ok=True)
    arquivo = os.path.join(args.saida, f"cpu_{args.upf}_{datetime.now():%Y%m%d_%H%M%S}.csv")
    with open(arquivo, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(linhas[0].keys()))
        w.writeheader()
        w.writerows(linhas)
    print(f"[CSV] {arquivo}")

    # Tabela pronta para colar no docs/validacao-alavancas.md
    print("\n| Pedido | Status (kubelet) | Tempo | cpu.max (kernel) | Medido sob carga | Estrangulado | Reinícios |")
    print("| --- | --- | --- | --- | --- | --- | --- |")
    for l in linhas:
        print(f"| {l['pedido_m']}m | {l['status_m']}m | {l['t_status_s']} s | {l['cpu_max']} ({l['kernel_m']}m) "
              f"| {l['medido_m']}m | {l['estrangulado_pct']}% | {l['reinicios']} |")


if __name__ == "__main__":
    main()
