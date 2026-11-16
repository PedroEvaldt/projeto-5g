#!/usr/bin/env python3
"""
Cruza as sondas de latência (scripts/sonda_rede.sh) com as janelas da VR das execuções. É a investigação da hvr_l2
nos controles da Fase 9: a VR sai do SLA pelo net quando o gold toca no alto (docs/render-real.md).

Uso (de metricas_scripts/):
    ../venv/bin/python analise_sondas.py <pasta das sondas> <pasta das execuções>
    # ex.: ../venv/bin/python analise_sondas.py resultados/fase9m_sonda resultados/fase9m/hvr_l2

Para cada janela normal da VR (qoe_vr_timeseries.csv), pega os pings de cada sonda dentro dela, pela hora do pod
(o ping -D e o Timestamp_Pod vêm do mesmo nó), e calcula o p95 do RTT e as respostas que não vieram. As janelas se
dividem em boas e ruins pelo net_p95, com o limite da calibração de cada execução (net_ref + folga). Por sonda, o
resumo traz a mediana do p95 em cada grupo e a correlação de Spearman com o net_p95. A sonda que sobe junto com o
net aponta o pedaço do caminho onde está o atraso.

Grava <pasta das sondas>/janelas.csv (uma linha por janela) e imprime o resumo.
"""
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import calibracao_vr

SONDAS = ["upf4_rede", "upf4_tunel", "servidor_rede", "servidor_tunel"]   # a ordem do caminho, como no sonda_rede.sh
RESPOSTA = re.compile(r"^\[(\d+\.\d+)\] \d+ bytes from .* time=([\d.]+) ms")
SEM_RESPOSTA = re.compile(r"^\[(\d+\.\d+)\] no answer yet")


def ler_sonda(arq):
    """DataFrame (t, rtt_ms), com rtt NaN para a resposta que não veio. t em segundos Unix, no relógio do pod."""
    linhas = []
    if arq.exists():
        for l in open(arq, errors="replace"):
            if m := RESPOSTA.match(l):
                linhas.append((float(m[1]), float(m[2])))
            elif m := SEM_RESPOSTA.match(l):
                linhas.append((float(m[1]), np.nan))
    return pd.DataFrame(linhas, columns=["t", "rtt_ms"]).sort_values("t")


def janelas(pasta):
    """As janelas normais de uma execução, com o início e o fim no relógio do pod e o limite do net."""
    j = pd.read_csv(pasta / "qoe_vr_timeseries.csv")
    j["fim"] = pd.to_datetime(j["Timestamp_Pod"], utc=True).astype("int64") / 1e9
    dur = j["fim"].diff()[j["Window_Type"] == "normal"].median()     # ~12,7 s: a janela do exporter
    j = j[(j["Window_Type"] == "normal") & j["Net_p95_ms"].notna()].copy()
    sla = calibracao_vr.ler(pasta)["sla"]
    j["inicio"] = j["fim"] - dur
    j["limite"] = sla["net_ref_ms"] + sla["folga_net_ms"]
    j["execucao"] = pasta.name
    return j[["execucao", "Timestamp_Offset_Seconds", "inicio", "fim", "Net_p95_ms", "limite"]]


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    pasta_sondas, pasta_exec = Path(sys.argv[1]), Path(sys.argv[2])
    sondas = {s: ler_sonda(pasta_sondas / f"{s}.txt") for s in SONDAS}
    execs = sorted(p.parent for p in pasta_exec.glob("*/qoe_vr_timeseries.csv"))
    if not execs:
        sys.exit(f"[ERRO] nenhuma execução com qoe_vr_timeseries.csv em {pasta_exec}")
    j = pd.concat([janelas(p) for p in execs], ignore_index=True)
    for s, d in sondas.items():
        p95, perdas, n = [], [], []
        for ini, fim in zip(j["inicio"], j["fim"]):
            r = d["rtt_ms"][(d["t"] > ini) & (d["t"] <= fim)]
            n.append(len(r))
            perdas.append(int(r.isna().sum()))
            p95.append(np.nanpercentile(r, 95) if r.notna().any() else np.nan)
        j[f"{s}_p95_ms"], j[f"{s}_perdidos"], j[f"{s}_n"] = p95, perdas, n
    j["ruim"] = j["Net_p95_ms"] > j["limite"]
    j.to_csv(pasta_sondas / "janelas.csv", index=False)

    boas, ruins = j[~j["ruim"]], j[j["ruim"]]
    print(f"{len(execs)} execuções, {len(j)} janelas: {len(boas)} boas e {len(ruins)} ruins (net_p95 acima do limite)\n")
    print(f"| Medida | p95 mediano, janelas boas (ms) | p95 mediano, janelas ruins (ms) | Spearman com o net_p95 | "
          f"Pings perdidos | Janelas sem ping |")
    print("| --- | --- | --- | --- | --- | --- |")
    print(f"| net_p95 da VR | {boas['Net_p95_ms'].median():.2f} | {ruins['Net_p95_ms'].median():.2f} | — | — | — |")
    for s in SONDAS:
        c = f"{s}_p95_ms"
        print(f"| {s} | {boas[c].median():.2f} | {ruins[c].median():.2f} | "
              f"{j[[c, 'Net_p95_ms']].corr(method='spearman').iloc[0, 1]:.2f} | "
              f"{j[f'{s}_perdidos'].sum()} de {j[f'{s}_n'].sum()} | {(j[f'{s}_n'] == 0).sum()} |")
    print(f"\n[OK] {pasta_sondas / 'janelas.csv'}")


if __name__ == "__main__":
    main()
