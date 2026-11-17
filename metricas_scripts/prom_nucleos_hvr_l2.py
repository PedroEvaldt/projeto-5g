#!/usr/bin/env python3
"""
O nó dos UEs núcleo a núcleo em cada execução hvr_l2, tirado do Prometheus: a conferência de 17/11/2026 do que muda
entre o blacksabbath (Fase 8) e o molejo (Fase 9) (docs/render-real.md, "O blacksabbath e o molejo"). Roda na
ledzeppelin (port-forward do Prometheus em localhost:35235); o Prometheus guarda 30 dias.

Lê do stdin as linhas "grupo execução t0" do t0_execucoes.py e, para as mesmas duas fases do prom_cpu_no_hvr_l2.py
(64-346 s e 791-901 s), imprime um CSV com a mediana no tempo de: frequência real (média dos núcleos e o núcleo mais
lento), frequência máxima configurada, throttling por calor, temperatura máxima, ocupação do núcleo mais ocupado e
núcleos acima de 90%, softirq (total e do núcleo com mais), pacotes tratados pelo kernel (mil/s) e a parte do núcleo
que trata mais, softnet squeezed (/s), espera do escalonador do núcleo que mais espera, CPU do ue e do gnb do gold
(UERANSIM) e o digest da imagem do UERANSIM no ue-vr-07 (12 primeiros caracteres).

Uso (de metricas_scripts/):
    ../venv/bin/python t0_execucoes.py <grupo> "<padrão>" ... | ../venv/bin/python prom_nucleos_hvr_l2.py > prom_nucleos.csv
"""
import sys, requests, statistics
from collections import defaultdict
P = "http://localhost:35235/api/v1/"

def serie(q, a, b):
    """{núcleo: {t: valor}} de uma consulta por núcleo (rótulo cpu)."""
    r = requests.get(P + "query_range", params={"query": q, "start": a, "end": b, "step": 15}, timeout=60).json()
    return {s["metric"].get("cpu", str(n)): {float(t): float(v) for t, v in s["values"]} for n, s in enumerate(r["data"]["result"])}

def por_instante(d, f):
    """Aplica f nos valores de todos os núcleos em cada instante; devolve a mediana no tempo."""
    t = defaultdict(list)
    for v in d.values():
        for k, x in v.items():
            t[k].append(x)
    r = [f(xs) for xs in t.values() if xs]
    return statistics.median(r) if r else float("nan")

def um(q, t):
    r = requests.get(P + "query", params={"query": q, "time": t}, timeout=30).json()["data"]["result"]
    return float(r[0]["value"][1]) if r else float("nan")

def faixa(q, a, b):
    return por_instante(serie(q, a, b), sum)

print("grupo,execucao,fase,no,ncpu,freq_media_ghz,freq_min_nucleo_ghz,freq_max_conf_ghz,throttles,temp_max_c,"
      "ocup_nucleo_max,nucleos_acima_90,softirq_total,softirq_nucleo_max,pacotes_kps,pacotes_parte_nucleo_max,"
      "squeezed_total,espera_nucleo_max,ue_gold,gnb_gold,ueransim")
for l in sys.stdin:
    grupo, ex, t0 = l.split(); t0 = int(t0)
    r = requests.get(P + "query", params={"query": 'max by (node) (container_cpu_usage_seconds_total{namespace="pfsevaldt",container="dash-client",pod=~"ue-video-01.*"})', "time": t0 + 800}, timeout=30).json()
    no = r["data"]["result"][0]["metric"]["node"]
    r = requests.get(P + "query", params={"query": f'node_uname_info{{nodename="{no}"}}', "time": t0 + 800}, timeout=30).json()
    i = r["data"]["result"][0]["metric"]["instance"]
    r = requests.get(P + "query", params={"query": 'kube_pod_container_info{namespace="pfsevaldt",container="ue",pod=~"ue-vr-07.*"}', "time": t0 + 800}, timeout=30).json()
    imagem = r["data"]["result"][0]["metric"]["image_id"].split(":")[-1][:12] if r["data"]["result"] else "?"
    for fase, a, b in (("64-346", 124, 346), ("791-901", 851, 901)):
        a, b = t0 + a, t0 + b
        freq = serie(f'node_cpu_scaling_frequency_hertz{{instance="{i}"}}', a, b)
        idle = serie(f'rate(node_cpu_seconds_total{{instance="{i}",mode="idle"}}[1m])', a, b)
        sirq = serie(f'rate(node_cpu_seconds_total{{instance="{i}",mode="softirq"}}[1m])', a, b)
        pac = serie(f'rate(node_softnet_processed_total{{instance="{i}"}}[1m])', a, b)
        gold = lambda cont: faixa(f'sum(rate(container_cpu_usage_seconds_total{{namespace="pfsevaldt",container="{cont}",pod=~"ue-video-01.*"}}[1m]))', a, b)
        v = [len(idle),
             por_instante(freq, lambda xs: sum(xs) / len(xs) / 1e9),
             por_instante(freq, lambda xs: min(xs) / 1e9),
             um(f'max(node_cpu_scaling_frequency_max_hertz{{instance="{i}"}})', b) / 1e9,
             um(f'sum(increase(node_cpu_core_throttles_total{{instance="{i}"}}[90s])) + sum(increase(node_cpu_package_throttles_total{{instance="{i}"}}[90s]))', b),
             por_instante(serie(f'node_hwmon_temp_celsius{{instance="{i}"}}', a, b), max),
             por_instante(idle, lambda xs: 1 - min(xs)),
             por_instante(idle, lambda xs: sum(1 for x in xs if 1 - x > 0.9)),
             por_instante(sirq, sum),
             por_instante(sirq, max),
             por_instante(pac, sum) / 1e3,
             por_instante(pac, lambda xs: max(xs) / sum(xs) if sum(xs) else float("nan")),
             um(f'sum(rate(node_softnet_times_squeezed_total{{instance="{i}"}}[90s]))', b),
             por_instante(serie(f'rate(node_schedstat_waiting_seconds_total{{instance="{i}"}}[1m])', a, b), max),
             gold("ue"), gold("gnb")]
        print(",".join([grupo, ex, fase, no] + [f"{x:.3f}" for x in v] + [imagem]), flush=True)
