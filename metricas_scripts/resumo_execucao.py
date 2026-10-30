#!/usr/bin/env python3
"""
Resumo de UMA execução, direto no terminal.

Uso (de dentro de metricas_scripts/):
    ../venv/bin/python resumo_execucao.py resultados/slices_hierarquicos/exec01/ [deslocamento_s]

Mostra:
  1. a QoE média de cada fatia na execução inteira;
  2. a QoE de cada fatia em cada FASE do teste (os trechos entre um evento e outro);
  3. quantas rodadas do leilão cada fatia venceu, quanto pagou e quanto recurso teve em média;
  4. a VR, se a pasta tiver o qoe_vr_timeseries.csv (do analise_metricas_vr.py): SLA na execução
     inteira e por fase, e quanto tempo a VR levou para entrar no SLA em cada entrada.
Uma seção cujo CSV falta na pasta (ex.: um teste só da VR) é pulada.

Sobre o deslocamento: os eventos são contados a partir do início do deploy_test.py, e a QoE a
partir do início do player, que sobe uns segundos ANTES (o run_experiment espera 20 s entre
reiniciar os players e iniciar os eventos, e o player leva alguns segundos para começar).
O deslocamento (padrão 10 s) é somado aos tempos dos eventos para alinhá-los com a QoE.
A VR não usa o deslocamento: o coletor já pôs as janelas na régua dos eventos.
"""
import sys
from pathlib import Path

import pandas as pd
import yaml

pasta = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
deslocamento = float(sys.argv[2]) if len(sys.argv) > 2 else 10.0
CALIBRACAO = Path(__file__).resolve().parent.parent / "vr" / "calibracao.yaml"


def ler(nome):
    """O CSV da pasta, ou None se a execução não o tiver."""
    arq = pasta / nome
    return pd.read_csv(arq) if arq.exists() else None


qoe = ler("qoe_timeseries.csv")
eventos = pd.read_csv(pasta / "test_events.csv")
leilao = ler("auction_history.csv")
vr = ler("qoe_vr_timeseries.csv")

pd.set_option("display.width", 120)

if qoe is not None:
    for col in ["Timestamp_Offset_Seconds", "Bitrate_kbps", "Buffer_Seconds", "Stalls", "Quality_Switches"]:
        qoe[col] = pd.to_numeric(qoe[col], errors="coerce")
    tocando = qoe[qoe["Status"] == "OK"]                    # tira as amostras pausadas (bitrate 0 de propósito)

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

if leilao is not None:
    # 3. Leilão
    print()
    print("=" * 120)
    print("3. LEILÃO POR FATIA")
    print("=" * 120)
    for col in ["Bid_Value", "Price_Paid", "CPU_Allocated", "BW_Allocated"]:
        leilao[col] = pd.to_numeric(leilao[col], errors="coerce")
    # Só as rodadas do teste: o leiloeiro segue rodando depois do TEST_END, até a sessão do tmux ser fechada.
    # Precisa da hora real do início (inicio=, desde a Fase 4); execuções antigas ficam como sempre foram.
    detalhes = eventos.loc[eventos["Event_Type"] == "TEST_START", "Details"].astype(str)
    achou = detalhes.str.extract(r"inicio=(\S+)", expand=False).dropna()
    if len(achou):
        inicio = pd.Timestamp(achou.iloc[0]).tz_localize(None)        # o leiloeiro grava a hora local da mesma máquina, sem fuso
        fim_ev = pd.to_numeric(eventos.loc[eventos["Event_Type"] == "TEST_END", "Timestamp_Offset_Seconds"], errors="coerce")
        fim = inicio + pd.Timedelta(seconds=float(fim_ev.iloc[0]) if len(fim_ev) else 900.0)
        hora = pd.to_datetime(leilao["Timestamp"])
        total = leilao["Auction_ID"].nunique()
        leilao = leilao[(hora >= inicio) & (hora <= fim)]
        print(f"Só as rodadas do teste: {leilao['Auction_ID'].nunique()} de {total} no arquivo "
              f"(as outras são de antes do início ou de depois do fim)\n")
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

if vr is not None:
    # 4. VR
    with open(CALIBRACAO) as f:
        sla = yaml.safe_load(f)["sla"]
    net_max = sla["net_ref_ms"] + sla["folga_net_ms"]
    marca = vr["Window_Type"].isin(["pausa", "retomada"])
    janelas = vr[~marca]
    contam = janelas[janelas["Health"] == 1]               # health=0 é falha do instrumento: fora da conta
    normais = contam[contam["Window_Type"] == "normal"]

    if qoe is not None or leilao is not None:               # separa das seções de cima, se houver
        print()
    print("=" * 120)
    print(f"4. VR ({', '.join(vr['Slice'].dropna().unique())}): janelas de ~12.6 s, SLA do vr/calibracao.yaml, sem deslocamento")
    print("=" * 120)
    tipos = ", ".join(f"{n} {t}" for t, n in janelas["Window_Type"].value_counts().items())
    print(f"Janelas: {len(janelas)} ({tipos}); fora da conta (health=0): {len(janelas) - len(contam)}")
    if len(contam):
        print(f"No SLA: {int(contam['SLA_OK'].sum())} de {len(contam)} ({100 * contam['SLA_OK'].mean():.1f}%)")
    if len(normais):
        print(f"net_p95: mediana {normais['Net_p95_ms'].median():.2f} ms, p95 das janelas {normais['Net_p95_ms'].quantile(0.95):.2f} ms "
              f"(limite {net_max:.2f})  |  m2p_p95 mediana {normais['M2P_p95_ms'].median():.2f} ms")
        print(f"perda máx {normais['Loss_pct'].max():g}%  |  continuidade mín {normais['Continuity_pct'].min():g}%  |  "
              f"vazão mediana {normais['Throughput_Mbps'].median():.2f} Mbps")
    # Qual critério reprovou: só o net = falta de CPU; vazão junto = banda ou CPU muito baixa (Fase 3)
    print(f"Reprovadas por (uma janela pode falhar em mais de um): "
          f"net {(normais['Net_p95_ms'] > net_max).sum()}, vazão {(normais['Throughput_Mbps'] < sla['vazao_min_mbps']).sum()}, "
          f"perda {(normais['Loss_pct'] > sla['perda_max_pct']).sum()}, continuidade {(normais['Continuity_pct'] < sla['cont_min_pct']).sum()}, "
          f"sem quadros {(contam['Window_Type'] == 'sem_quadros').sum()}")

    # 4b. Por fase: os trechos entre um evento e outro, sem deslocamento; a janela conta na fase em que termina
    print("\nPor fase:")
    fim_teste = eventos.loc[eventos["Event_Type"] == "TEST_END", "Timestamp_Offset_Seconds"]
    fim_teste = float(fim_teste.iloc[0]) if len(fim_teste) else vr["Timestamp_Offset_Seconds"].max()
    tipo_ev = eventos["Event_Type"].astype(str)
    evf = eventos[~tipo_ev.isin(["TEST_START", "TEST_END", "TEST_INTERRUPTED", "TEST_ERROR"])
                  & ~tipo_ev.str.contains("SENT|FAIL|ERROR")]
    tempos = pd.to_numeric(evf["Timestamp_Offset_Seconds"], errors="coerce").tolist()
    aberturas = ["início do teste"] + [str(d).split("|")[0].strip()[:45] for d in evf["Description"]]
    linhas = []
    for ini, fim, abertura in zip([0.0] + tempos, tempos + [fim_teste], aberturas):
        t = contam["Timestamp_Offset_Seconds"]
        trecho = contam[(t >= ini) & (t < fim)]
        linhas.append({"fase": f"{ini:.0f}-{fim:.0f}s", "começou com": abertura, "janelas": len(trecho),
                       "no_sla": f"{100 * trecho['SLA_OK'].mean():.0f}%" if len(trecho) else "—",
                       "net_mediano_ms": f"{trecho['Net_p95_ms'].median():.2f}" if trecho["Net_p95_ms"].notna().any() else "—"})
    print(pd.DataFrame(linhas).to_string(index=False))

    # 4c. Cada entrada (marca de retomada): tempo até a 1ª janela no SLA. Marca e janelas vêm do mesmo relógio
    # (o do pod), então a diferença não tem erro de conversão; a resolução é de uma janela
    print("\nEntradas da VR (da retomada até a 1ª janela no SLA):")
    marcas = vr[marca]
    retomadas = marcas.index[marcas["Window_Type"] == "retomada"]
    if not len(retomadas):
        print("  nenhuma marca de retomada: a VR não entrou durante a execução")
    for r in retomadas:
        t0 = vr.loc[r, "Timestamp_Offset_Seconds"]
        depois = vr.loc[r + 1:]
        saida = depois.index[depois["Window_Type"] == "pausa"]
        depois = depois.loc[:saida[0] - 1] if len(saida) else depois   # até a próxima pausa
        jan = depois[~depois["Window_Type"].isin(["pausa", "retomada"])].reset_index(drop=True)
        ok = jan.index[jan["SLA_OK"] == 1]
        if len(ok):
            print(f"  entrada em {t0:.1f} s: no SLA depois de {jan.loc[ok[0], 'Timestamp_Offset_Seconds'] - t0:.1f} s, "
                  f"na {ok[0] + 1}ª janela")
        else:
            print(f"  entrada em {t0:.1f} s: não entrou no SLA ({len(jan)} janelas até a saída ou o fim)")
