#!/usr/bin/env python3
"""
Fase 3: figuras da calibração da VR, uma por recurso da upf4 (CPU e banda), a partir dos CSVs do
vr/calibrar.py. Cada figura tem três painéis com o mesmo eixo x:
  (a) net_p95, em escala log, com o limite do SLA (net_ref + 3 ms);
  (b) vazão, com o limite do SLA (95% do fluxo);
  (c) % de janelas dentro do SLA, com a linha de 95%: é daqui que sai o piso.

A unidade de repetição é a medida (um ponto, uma repetição), não a janela. As 9 janelas de uma
medida são a mesma sessão e a mesma fila; tratá-las como repetições estreitaria o intervalo de
confiança à toa (pseudorreplicação). Por medida, a mediana das janelas; por ponto, a média das
medidas, com IC de 95% pela t de Student.

Janela sem quadros: atraso infinito (o fluxo parou), vazão 0 (o exporter não escreve a vazão
nessa linha; algum pacote solto pode ter chegado) e sempre fora do SLA. Janela com health=0 é
falha do instrumento, não da fatia, e fica de fora.

Rótulos em inglês, como as figuras do artigo (metricas_scripts/csv_graficos_ieee.py).

Uso (da raiz do repositório), só com os CSVs que valem (ver o LEIAME.md da pasta de resultados):
  venv/bin/python vr/graficos_calibracao.py metricas_scripts/resultados/calibracao/calib_cpu_20261027_184453.csv ...

Saída: figuras/calib_<recurso>.pdf e .png, na pasta do primeiro CSV, e uma tabela por ponto no terminal.
"""

import argparse
import os

import matplotlib.pyplot as plt
import matplotlib.transforms as mtransforms
import numpy as np
import pandas as pd

FLUXO_MBPS = 8.05                    # o fluxo inteiro do perfil vr8: 60 quadros/s × 14 pacotes × 1200 B
SLA_NET_FOLGA_MS = 3                 # net_p95 ≤ net_ref + 3 ms; net_ref = net_p95 no ponto mais folgado
SLA_PERDA_MAX = 1.0                  # %
SLA_CONT_MIN = 98.0                  # %
SLA_VAZAO_MIN = 0.95 * FLUXO_MBPS    # 7,65 Mbps: a perda e a continuidade não veem um fluxo que chega pingando
SLA_JANELAS = 95.0                   # % de janelas no SLA que o piso exige

# t de Student bicaudal de 95%, por graus de liberdade (medidas - 1). Acima de 10, que não acontece
# aqui, vale a aproximação normal.
T95 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228}

EIXO_X = {"cpu": "UPF CPU limit (cores)", "banda": "UPF bandwidth limit (Mbps)"}
COR = "#1f4e79"                      # a série: uma por painel, então uma cor só e sem legenda
COR_MEDIDA = "#9aa5b1"               # cada medida, atrás da média
COR_LIMITE = "#555555"               # limites do SLA, tracejados

# O padrão IEEE do csv_graficos_ieee.py, com a grade contínua: aqui o tracejado é dos limites do SLA
plt.rcParams.update({
    'font.family': 'serif',
    'font.serif': ['Times New Roman', 'DejaVu Serif'],
    'font.size': 9,
    'axes.labelsize': 9,
    'xtick.labelsize': 8,
    'ytick.labelsize': 8,
    'axes.grid': True,
    'grid.alpha': 0.25,
    'grid.linewidth': 0.5,
    'axes.spines.top': False,
    'axes.spines.right': False,
})


def carregar(arquivos):
    """Junta os CSVs e calcula, por janela, o atraso e a vazão que os gráficos usam."""
    partes = []
    for a in arquivos:
        d = pd.read_csv(a)
        d["arquivo"] = os.path.basename(a)                     # a coluna medida recomeça em 1 em cada arquivo
        partes.append(d)
    df = pd.concat(partes, ignore_index=True)
    falhas = df["health"] != 1
    if falhas.any():
        print(f"[AVISO] {falhas.sum()} janelas com health=0 (falha do instrumento) ficaram de fora")
    df = df[~falhas].copy()
    com_quadros = df["tipo"] == "normal"
    df["net"] = df["net_p95_ms"].where(com_quadros, np.inf)   # sem quadros: o fluxo parou
    df["vazao"] = df["thr_mbps"].where(com_quadros, 0.0)
    return df


def marcar_sla(df):
    """Marca, por janela, se ela está dentro do SLA. Devolve o net_ref do recurso."""
    net_ref = df.loc[df["valor"] == df["valor"].max(), "net"].median()   # o ponto mais folgado
    df["no_sla"] = ((df["net"] <= net_ref + SLA_NET_FOLGA_MS) & (df["loss_pct"] <= SLA_PERDA_MAX)
                    & (df["cont_pct"] >= SLA_CONT_MIN) & (df["vazao"] >= SLA_VAZAO_MIN))
    return net_ref


def media_ic(x):
    """Média e meia-largura do IC de 95% (t de Student) de uma lista de medidas."""
    x = np.asarray(x, dtype=float)
    if len(x) < 2:
        return x.mean(), 0.0
    return x.mean(), T95.get(len(x) - 1, 1.96) * x.std(ddof=1) / np.sqrt(len(x))


def por_ponto(df):
    """Uma linha por medida (mediana das janelas) e uma por ponto (média e IC das medidas)."""
    medidas = (df.groupby(["valor", "arquivo", "medida"])
                 .agg(net=("net", "median"), vazao=("vazao", "median"), sla=("no_sla", "mean"),
                      janelas=("janela", "size"), sem_quadros=("tipo", lambda t: (t != "normal").sum()))
                 .reset_index())
    medidas["sla"] *= 100
    linhas = []
    for valor, m in medidas.groupby("valor"):
        # Se alguma medida ficou com a maioria das janelas sem quadros, o atraso dela é infinito, e o ponto não tem média
        net, net_ic = media_ic(m["net"]) if np.isfinite(m["net"]).all() else (np.nan, np.nan)
        vazao, vazao_ic = media_ic(m["vazao"])
        sla, sla_ic = media_ic(m["sla"])
        linhas.append({"valor": valor, "medidas": len(m), "janelas": m["janelas"].sum(),
                       "sem_quadros": m["sem_quadros"].sum(), "net": net, "net_ic": net_ic,
                       "vazao": vazao, "vazao_ic": vazao_ic, "sla": sla, "sla_ic": sla_ic})
    return medidas, pd.DataFrame(linhas)


def imprimir(recurso, pontos, net_ref):
    """Tabela por ponto, pronta para o LEIAME, e o menor ponto que passa no SLA."""
    print(f"\n=== {recurso}: net_ref {net_ref:.2f} ms. SLA por janela: net_p95 ≤ {net_ref + SLA_NET_FOLGA_MS:.2f} ms, "
          f"perda ≤ {SLA_PERDA_MAX:g}%, continuidade ≥ {SLA_CONT_MIN:g}%, vazão ≥ {SLA_VAZAO_MIN:.2f} Mbps")
    print("| Valor | Medidas | Janelas (sem quadros) | net_p95 (ms) | Vazão (Mbps) | Janelas no SLA (%) |")
    print("| --- | --- | --- | --- | --- | --- |")
    for p in pontos.itertuples():
        net = "—" if np.isnan(p.net) else f"{p.net:.2f} ± {p.net_ic:.2f}"
        print(f"| {p.valor:g} | {p.medidas} | {p.janelas} ({p.sem_quadros}) | {net} "
              f"| {p.vazao:.2f} ± {p.vazao_ic:.2f} | {p.sla:.1f} ± {p.sla_ic:.1f} |")
    # O menor ponto a partir do qual todos os pontos acima também passam: um ponto baixo que passasse por sorte não conta
    passa = (pontos["sla"] >= SLA_JANELAS).astype(int)
    daqui_para_cima = passa[::-1].cummin()[::-1].astype(bool)
    if daqui_para_cima.any():
        print(f"Menor ponto com ≥ {SLA_JANELAS:g}% das janelas no SLA (e todos acima dele): "
              f"{pontos.loc[daqui_para_cima, 'valor'].min():g}")
    else:
        print(f"Nenhum ponto tem ≥ {SLA_JANELAS:g}% das janelas no SLA.")


def figura(recurso, medidas, pontos, net_ref, pasta):
    """Os três painéis de um recurso, em PDF (artigo) e PNG (olhar rápido)."""
    fig, (ax_net, ax_vaz, ax_sla) = plt.subplots(3, 1, figsize=(3.45, 5.2), sharex=True)
    # O rótulo do limite vai do lado da linha tracejada onde não há dados: acima no atraso (os pontos bons ficam
    # embaixo), abaixo na vazão e no SLA (os pontos bons ficam em cima)
    paineis = [(ax_net, "net", "Net RTT p95 (ms)", net_ref + SLA_NET_FOLGA_MS, f"SLA: ref + {SLA_NET_FOLGA_MS} ms", 2),
               (ax_vaz, "vazao", "Throughput (Mbps)", SLA_VAZAO_MIN, "SLA: 95% of stream", -2),
               (ax_sla, "sla", "Windows in SLA (%)", SLA_JANELAS, f"{SLA_JANELAS:g}% of windows", -2)]
    for ax, col, rotulo, limite, texto, desloc in paineis:
        finitas = medidas[np.isfinite(medidas[col])]
        ax.scatter(finitas["valor"], finitas[col], s=8, color=COR_MEDIDA, linewidths=0, zorder=2)
        ax.errorbar(pontos["valor"], pontos[col], yerr=pontos[f"{col}_ic"], color=COR, marker="o",
                    ms=3.5, lw=1.2, capsize=2, zorder=3)
        ax.axhline(limite, ls="--", lw=0.8, color=COR_LIMITE, zorder=1)
        ax.annotate(texto, xy=(1, limite), xycoords=("axes fraction", "data"), xytext=(0, desloc),
                    textcoords="offset points", ha="right", va="bottom" if desloc > 0 else "top",
                    fontsize=7, color=COR_LIMITE)
        ax.set_ylabel(rotulo)

    # (a) atraso: escala log (de ~2 ms a ~10 s) e ▲ no topo para as medidas sem quadros
    ax_net.set_yscale("log")
    finitos = medidas.loc[np.isfinite(medidas["net"]), "net"]
    ax_net.set_ylim(finitos.min() * 0.6, finitos.max() * 6)               # folga em cima para os ▲
    sem = np.sort(medidas.loc[~np.isfinite(medidas["net"]), "valor"].unique())
    if len(sem):
        topo = mtransforms.blended_transform_factory(ax_net.transData, ax_net.transAxes)
        ax_net.scatter(sem, [0.93] * len(sem), marker="^", s=18, color=COR, transform=topo, zorder=3)
        ax_net.annotate("no frames", xy=(sem[-1], 0.93), xycoords=topo, xytext=(5, 0),
                        textcoords="offset points", va="center", fontsize=7, color=COR)
    ax_vaz.set_ylim(0, FLUXO_MBPS * 1.12)
    ax_sla.set_ylim(-3, 105)
    ax_sla.set_xlabel(EIXO_X.get(recurso, recurso))
    fig.align_ylabels()

    os.makedirs(pasta, exist_ok=True)
    base = os.path.join(pasta, f"calib_{recurso}")
    fig.savefig(base + ".pdf", bbox_inches="tight")
    fig.savefig(base + ".png", dpi=200, bbox_inches="tight")
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description="Fase 3: figuras da calibração da VR")
    ap.add_argument("csvs", nargs="+", help="CSVs do calibrar.py que valem (ver o LEIAME.md)")
    ap.add_argument("--saida", help="pasta das figuras (padrão: figuras/, na pasta do primeiro CSV)")
    args = ap.parse_args()
    pasta = args.saida or os.path.join(os.path.dirname(os.path.abspath(args.csvs[0])), "figuras")

    df = carregar(args.csvs)
    for recurso, d in df.groupby("recurso"):
        d = d.copy()
        net_ref = marcar_sla(d)
        medidas, pontos = por_ponto(d)
        imprimir(recurso, pontos, net_ref)
        figura(recurso, medidas, pontos, net_ref, pasta)
    print(f"\n[FIGURAS] {pasta}")


if __name__ == "__main__":
    main()
