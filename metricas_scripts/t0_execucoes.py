#!/usr/bin/env python3
"""
Lista o horário de início (T+0, no relógio do pod, que é o do Prometheus) de cada execução com VR. É a entrada do
prom_cpu_no_hvr_l2.py, na investigação da queda da hvr_l2 (docs/render-real.md, "A causa da queda").

Uso (de metricas_scripts/): pares <grupo> <padrão dos qoe_vr_timeseries.csv>; imprime "grupo execução t0" por linha.
    ../venv/bin/python t0_execucoes.py fase8 "resultados/hvr_l2/*/qoe_vr_timeseries.csv" fase9m "resultados/fase9m/hvr_l2/*/qoe_vr_timeseries.csv"
"""
import sys, glob, pandas as pd
for grupo, padrao in zip(sys.argv[1::2], sys.argv[2::2]):
    for f in sorted(glob.glob(padrao)):
        j = pd.read_csv(f)
        fim = pd.to_datetime(j["Timestamp_Pod"], utc=True).astype("int64") / 1e9
        t0 = (fim - j["Timestamp_Offset_Seconds"]).median()
        print(grupo, f.split("/")[-2], int(t0))
