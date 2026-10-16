#!/usr/bin/env python3
"""
Resumo de UMA execução, direto no terminal.

Uso (de dentro de metricas_scripts/):
    ../venv/bin/python resumo_execucao.py resultados/slices_hierarquicos/exec01/ [deslocamento_s]

Mostra:
  1. a QoE média de cada fatia na execução inteira;
  2. a QoE de cada fatia em cada FASE do teste (os trechos entre um evento e outro);
  3. quantas rodadas do leilão cada fatia venceu, quanto pagou e quanto recurso teve em média.

Sobre o deslocamento: os eventos são contados a partir do início do deploy_test.py, e a QoE a
partir do início do player, que sobe uns segundos ANTES (o run_experiment espera 20 s entre
reiniciar os players e iniciar os eventos, e o player leva alguns segundos para começar).
O deslocamento (padrão 10 s) é somado aos tempos dos eventos para alinhá-los com a QoE.
"""
import sys
from pathlib import Path

import pandas as pd

pasta = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
deslocamento = float(sys.argv[2]) if len(sys.argv) > 2 else 10.0

qoe = pd.read_csv(pasta / "qoe_timeseries.csv")
eventos = pd.read_csv(pasta / "test_events.csv")
leilao = pd.read_csv(pasta / "auction_history.csv")

for col in ["Timestamp_Offset_Seconds", "Bitrate_kbps", "Buffer_Seconds", "Stalls", "Quality_Switches"]:
    qoe[col] = pd.to_numeric(qoe[col], errors="coerce")
tocando = qoe[qoe["Status"] == "OK"]                    # tira as amostras pausadas (bitrate 0 de propósito)

pd.set_option("display.width", 120)

# 1. Execução inteira
print("=" * 120)
print("1. QoE POR FATIA (execução inteira, só amostras tocando)")
print("=" * 120)
resumo = tocando.groupby("Slice").agg(
    amostras=("Bitrate_kbps", "size"),
    bitrate_medio_kbps=("Bitrate_kbps", "mean"),
    bitrate_min_kbps=("Bitrate_kbps", "min"),
    buffer_medio_s=("Buffer_Seconds", "mean"),
    buffer_min_s=("Buffer_Seconds", "min"),
)
resumo["stalls"] = qoe.groupby("Slice")["Stalls"].max()                 # é cumulativo: o máximo é o total
resumo["trocas_qualidade"] = qoe.groupby("Slice")["Quality_Switches"].max()
print(resumo.round(1).to_string())

# 2. Por fase
print()
print("=" * 120)
print(f"2. BITRATE MÉDIO (kbps) POR FASE (eventos deslocados +{deslocamento:.0f} s)")
print("=" * 120)
ev = eventos[eventos["Event_Type"].isin(["TRAFFIC_PAUSE", "TRAFFIC_RESUME"])].copy()
ev["t"] = pd.to_numeric(ev["Timestamp_Offset_Seconds"], errors="coerce") + deslocamento
ev["fatia"] = ev["Description"].astype(str).str.split().str[-1]          # "Tráfego pausado em gold" -> "gold"

# Colunas na ordem de prioridade (gold, silver, bronze); outros nomes vão no fim, em ordem alfabética
presentes = list(qoe["Slice"].dropna().unique())
ordem = [s for s in ["gold", "silver", "bronze"] if s in presentes] + sorted(s for s in presentes if s not in ["gold", "silver", "bronze"])

inicios = [0.0] + ev["t"].tolist()
fins = ev["t"].tolist() + [qoe["Timestamp_Offset_Seconds"].max()]
aberturas = ["início do teste"] + [("pausa " if tipo == "TRAFFIC_PAUSE" else "volta ") + f for tipo, f in zip(ev["Event_Type"], ev["fatia"])]

pausadas = set()
linhas = []
for i, (ini, fim, abertura) in enumerate(zip(inicios, fins, aberturas)):
    if i > 0:                                                           # aplica o evento que abriu esta fase
        tipo, f = ev.iloc[i - 1][["Event_Type", "fatia"]]
        pausadas.add(f) if tipo == "TRAFFIC_PAUSE" else pausadas.discard(f)
    trecho = tocando[(tocando["Timestamp_Offset_Seconds"] >= ini) & (tocando["Timestamp_Offset_Seconds"] < fim)]
    medias = trecho.groupby("Slice")["Bitrate_kbps"].mean()
    linha = {"fase": f"{ini:.0f}-{fim:.0f}s", "começou com": abertura}
    for s in ordem:
        if s in pausadas:
            linha[s] = "pausado"
        elif s in medias.index:
            linha[s] = f"{medias[s]:.0f}"
        else:
            linha[s] = "sem dados"
    linhas.append(linha)

print(pd.DataFrame(linhas).to_string(index=False))
print("Valores: bitrate médio em kbps, só das amostras com o vídeo tocando.")
print("Logo depois de uma retomada o player recomeça em qualidade baixa, então a média dessa fase sai menor.")

# 3. Leilão
print()
print("=" * 120)
print("3. LEILÃO POR FATIA")
print("=" * 120)
for col in ["Bid_Value", "Price_Paid", "CPU_Allocated", "BW_Allocated"]:
    leilao[col] = pd.to_numeric(leilao[col], errors="coerce")
res = leilao.groupby("Agent").agg(
    rodadas=("Auction_ID", "nunique"),
    vitorias=("Result", lambda r: (r == "WINNER").sum()),
    lance_medio=("Bid_Value", "mean"),
    total_pago=("Price_Paid", "sum"),
    cpu_media=("CPU_Allocated", "mean"),
    cpu_min=("CPU_Allocated", "min"),
    banda_media_mbps=("BW_Allocated", "mean"),
    banda_min_mbps=("BW_Allocated", "min"),
)
print(res.round(2).to_string())
lances_por_rodada = leilao.groupby("Auction_ID")["Agent"].nunique()
incompletas = (lances_por_rodada < leilao["Agent"].nunique()).sum()
print(f"\nRodadas: {len(lances_por_rodada)}  |  rodadas com menos lances que fatias: {incompletas}")
