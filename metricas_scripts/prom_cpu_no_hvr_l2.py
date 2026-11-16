#!/usr/bin/env python3
"""
CPU do nó dos UEs em cada execução hvr_l2, tirada do Prometheus: a investigação da queda da VR nos controles da Fase 9
(docs/render-real.md, "A causa da queda"). Roda na ledzeppelin (port-forward do Prometheus em localhost:35235); o
Prometheus guarda 30 dias.

Lê do stdin as linhas "grupo execução t0" do t0_execucoes.py e, para duas fases do teste (64-346 s: VR com bronze e
silver; 791-901 s: os 3 vídeos, gold no alto), imprime um CSV com a mediana de: CPU do dash-client do gold e dos 3
players, espera do escalonador do nó (node_schedstat_waiting, s/s), CPU ocupada do nó, softnet squeezed, CPU do ue e
do gnb do ue-vr-07, CPU da upf4 e CPU dos outros namespaces no nó. O nó é o do ue-video-01 na hora da execução.

Uso (de metricas_scripts/):
    ../venv/bin/python t0_execucoes.py <grupo> "<padrão>" ... | ../venv/bin/python prom_cpu_no_hvr_l2.py > prom_fases.csv
"""
import sys, requests, statistics
P = "http://localhost:35235/api/v1/"
def faixa(q, a, b):
    r = requests.get(P + "query_range", params={"query": q, "start": a, "end": b, "step": 15}, timeout=60).json()
    v = [float(x[1]) for s in r["data"]["result"] for x in s["values"]]
    return statistics.median(v) if v else float("nan")
print("grupo,execucao,fase,no,dash_gold,dash_todos,espera_sched,ocupado,softnet_squeezed,ue_vr,gnb_vr,upf4,outros_ns")
for l in sys.stdin:
    grupo, ex, t0 = l.split(); t0 = int(t0)
    r = requests.get(P + "query", params={"query": 'max by (node) (container_cpu_usage_seconds_total{namespace="pfsevaldt",container="dash-client",pod=~"ue-video-01.*"})', "time": t0 + 800}, timeout=30).json()
    no = r["data"]["result"][0]["metric"]["node"]
    r = requests.get(P + "query", params={"query": f'node_uname_info{{nodename="{no}"}}', "time": t0 + 800}, timeout=30).json()
    inst = r["data"]["result"][0]["metric"]["instance"]
    for fase, a, b in (("64-346", 124, 346), ("791-901", 851, 901)):
        a, b = t0 + a, t0 + b
        c = lambda pod, cont: faixa(f'sum(rate(container_cpu_usage_seconds_total{{namespace="pfsevaldt",container="{cont}",pod=~"{pod}"}}[1m]))', a, b)
        v = [c("ue-video-01.*", "dash-client"), c("ue-video-.*", "dash-client"),
             faixa(f'sum(rate(node_schedstat_waiting_seconds_total{{instance="{inst}"}}[1m]))', a, b),
             faixa(f'sum(rate(node_cpu_seconds_total{{instance="{inst}",mode!="idle"}}[1m]))', a, b),
             faixa(f'sum(rate(node_softnet_times_squeezed_total{{instance="{inst}"}}[1m]))', a, b),
             c("ue-vr-07.*", "ue"), c("ue-vr-07.*", "gnb"), c("upf4.*", "upf4"),
             faixa(f'sum(rate(container_cpu_usage_seconds_total{{node="{no}",container!="",namespace!="pfsevaldt"}}[1m]))', a, b)]
        print(",".join([grupo, ex, fase, no] + [f"{x:.3f}" for x in v]), flush=True)
