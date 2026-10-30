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

Um SLA só para os dois recursos: o net_ref é a mediana do net_p95 nas janelas dos pontos de folga
(os do FOLGA do calibrar.py). Se faltar o ponto de folga de um recurso, o script para, em vez de tirar
o net_ref de um ponto que já está apertado.

Com os CSVs dos dois recursos, grava também o vr/calibracao.yaml: o SLA por janela, os pisos (menor
ponto que passa + 10%) e a conferência do pool do tests_configs/test_config.yaml. É dele que as Fases
4 a 6 leem esses números.

Séries com vídeo (Fase 4): os CSVs passados em --com-video foram medidos com os 3 vídeos tocando. Eles
não entram no net_ref (o SLA é o que a VR precisa, medido sem carga), viram uma segunda curva na figura e,
na CPU, dão o piso: com vídeo nenhum ponto chega a 95% das janelas (um resto fora das cotas segura a VR
em ~91%), então o ponto mais alto da série faz papel de folga, e o piso é o menor ponto cujo % no SLA cai
dentro do IC dele (com todos os de cima também), + 10%. O piso sem vídeo fica no yaml como referência.

Rótulos em inglês, como as figuras do artigo (metricas_scripts/csv_graficos_ieee.py).

Uso (da raiz do repositório), com todos os CSVs que valem de uma vez (ver o LEIAME.md da pasta de resultados):
  venv/bin/python vr/graficos_calibracao.py metricas_scripts/resultados/calibracao/calib_cpu_20261027_184453.csv ... \
      --com-video metricas_scripts/resultados/calibracao/calib_cpu_20261030_104624.csv

Saída: figuras/calib_<recurso>.pdf e .png, na pasta do primeiro CSV, uma tabela por ponto no terminal e,
com os dois recursos, o vr/calibracao.yaml.
"""

import argparse
import math
import os
import sys
from datetime import datetime

import matplotlib.pyplot as plt
import matplotlib.transforms as mtransforms
import numpy as np
import pandas as pd
import yaml

FLUXO_MBPS = 8.05                    # o fluxo inteiro do perfil vr8: 60 quadros/s × 14 pacotes × 1200 B
SLA_NET_FOLGA_MS = 3                 # net_p95 ≤ net_ref + 3 ms
SLA_PERDA_MAX = 1.0                  # %
SLA_CONT_MIN = 98.0                  # %
SLA_VAZAO_MIN = round(0.95 * FLUXO_MBPS, 2)   # 7,65 Mbps: a perda e a continuidade não veem um fluxo que chega pingando
SLA_JANELAS = 95.0                   # % de janelas no SLA que o piso exige

FOLGA = {"cpu": 0.4, "banda": 15}    # o ponto sem aperto de cada série (o mesmo do calibrar.py): dele sai o net_ref
MARGEM_PISO = 1.10                   # piso = menor ponto que passa + 10%
# Piso arredondado para cima: a CPU em 10m, porque o systemd arredonda a cota assim de qualquer jeito
# (docs/validacao-alavancas.md); a banda em 0,1 Mbps
PASSO_PISO = {"cpu": 0.01, "banda": 0.1}
VIDEOS = 3                           # fatias de vídeo que dividem o pool com a VR
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(RAIZ, "tests_configs", "test_config.yaml")
SAIDA_YAML = os.path.join(RAIZ, "vr", "calibracao.yaml")

# t de Student bicaudal de 95%, por graus de liberdade (medidas - 1). Acima de 10, que não acontece
# aqui, vale a aproximação normal.
T95 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228}

EIXO_X = {"cpu": "UPF CPU limit (cores)", "banda": "UPF bandwidth limit (Mbps)"}
# Uma curva por carga: azul e laranja se distinguem também para daltônicos, e o marcador muda junto
CORES = {"sem": "#1f4e79", "video": "#c55a11"}
MARCADOR = {"sem": "o", "video": "s"}
ROTULO = {"sem": "VR alone", "video": "VR + 3 videos"}
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


def carregar(arquivos, com_video=()):
    """Junta os CSVs e calcula, por janela, o atraso e a vazão que os gráficos usam. A coluna carga diz se a
    série foi medida sem vídeo ("sem") ou com os 3 vídeos tocando ("video")."""
    video = {os.path.abspath(a) for a in com_video}
    partes = []
    for a in arquivos:
        d = pd.read_csv(a)
        d["arquivo"] = os.path.basename(a)                     # a coluna medida recomeça em 1 em cada arquivo
        d["carga"] = "video" if os.path.abspath(a) in video else "sem"
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


def calcular_net_ref(df):
    """Mediana do net_p95 nas janelas dos pontos de folga de todos os recursos presentes."""
    folga = pd.Series(False, index=df.index)
    for recurso, valor in FOLGA.items():
        folga |= (df["recurso"] == recurso) & np.isclose(df["valor"], valor)
    faltando = sorted(set(df["recurso"]) - set(df.loc[folga, "recurso"]))
    if faltando:
        sys.exit(f"[ERRO] Falta o ponto de folga de {', '.join(faltando)} "
                 f"({', '.join(f'{r} {FOLGA[r]:g}' for r in faltando)}). Sem ele, o net_ref sairia de um ponto "
                 f"apertado. Passe também o CSV que tem esse ponto (a CPU de folga está no 184453).")
    return df.loc[folga, "net"].median()


def marcar_sla(df, net_ref):
    """Marca, por janela, se ela está dentro do SLA."""
    df["no_sla"] = ((df["net"] <= net_ref + SLA_NET_FOLGA_MS) & (df["loss_pct"] <= SLA_PERDA_MAX)
                    & (df["cont_pct"] >= SLA_CONT_MIN) & (df["vazao"] >= SLA_VAZAO_MIN))


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


def menor_que_passa(pontos):
    """O menor ponto a partir do qual todos os pontos acima também passam (None se nenhum).
    Um ponto baixo que passasse por sorte, com um ponto acima dele reprovado, não conta."""
    passa = (pontos["sla"] >= SLA_JANELAS).astype(int)
    daqui_para_cima = passa[::-1].cummin()[::-1].astype(bool)
    return float(pontos.loc[daqui_para_cima, "valor"].min()) if daqui_para_cima.any() else None


def menor_equivalente(pontos):
    """Série com vídeo: o ponto mais alto faz papel de folga. O menor ponto cujo % no SLA não fica abaixo do IC
    do mais alto, com todos os de cima também (None se nenhum). Acima dele, mais recurso não muda o resultado."""
    topo = pontos.iloc[-1]
    rende = (pontos["sla"] >= topo["sla"] - topo["sla_ic"]).astype(int)
    daqui_para_cima = rende[::-1].cummin()[::-1].astype(bool)
    return float(pontos.loc[daqui_para_cima, "valor"].min()) if daqui_para_cima.any() else None


def imprimir(recurso, pontos, net_ref, passou, equivalente=False):
    """Tabela por ponto, pronta para o LEIAME, e o menor ponto que passa no SLA (ou, com vídeo, que rende o mesmo
    que a folga)."""
    print(f"\n=== {recurso}: net_ref {net_ref:.2f} ms. SLA por janela: net_p95 ≤ {net_ref + SLA_NET_FOLGA_MS:.2f} ms, "
          f"perda ≤ {SLA_PERDA_MAX:g}%, continuidade ≥ {SLA_CONT_MIN:g}%, vazão ≥ {SLA_VAZAO_MIN:.2f} Mbps")
    print("| Valor | Medidas | Janelas (sem quadros) | net_p95 (ms) | Vazão (Mbps) | Janelas no SLA (%) |")
    print("| --- | --- | --- | --- | --- | --- |")
    for p in pontos.itertuples():
        net = "—" if np.isnan(p.net) else f"{p.net:.2f} ± {p.net_ic:.2f}"
        print(f"| {p.valor:g} | {p.medidas} | {p.janelas} ({p.sem_quadros}) | {net} "
              f"| {p.vazao:.2f} ± {p.vazao_ic:.2f} | {p.sla:.1f} ± {p.sla_ic:.1f} |")
    if equivalente:
        topo = pontos.iloc[-1]
        regra = (f"com % no SLA ≥ {topo['sla'] - topo['sla_ic']:.1f} (o IC do ponto mais alto, {topo['valor']:g}), "
                 f"e todos acima dele")
    else:
        regra = f"com ≥ {SLA_JANELAS:g}% das janelas no SLA (e todos acima dele)"
    print(f"Menor ponto {regra}: {passou:g}" if passou is not None else f"Nenhum ponto {regra}.")


def piso_de(recurso, passou):
    """Menor ponto que passa + 10%, arredondado para cima no passo do recurso."""
    passo = PASSO_PISO[recurso]
    # O round antes do ceil: em ponto flutuante, 0,2 × 1,1 dá 0,22000000000000003, e o ceil sozinho subiria para 0,23
    return round(math.ceil(round(passou * MARGEM_PISO / passo, 6)) * passo, 2)


def ler_pool():
    """Mínimo do vídeo e total do pool de cada recurso, da seção auction do test_config.yaml."""
    with open(CONFIG) as f:
        leilao = yaml.safe_load(f)["auction"]
    return {"cpu": (leilao["minimum_cpu"], leilao["total_cpu_pool"]),
            "banda": (leilao["minimum_bw"], leilao["total_bw_pool"])}


def escrever_yaml(caminho, csvs, com_video, net_ref, passou):
    """O calibracao.yaml: o SLA por janela, os pisos e a conferência do pool, para as Fases 4 a 6.
    Com a série de CPU com vídeo, o piso de CPU sai dela, e o sem vídeo fica como referência."""
    tem_video = passou.get("cpu_video") is not None
    piso = {"cpu": piso_de("cpu", passou["cpu_video"] if tem_video else passou["cpu"]),
            "banda": piso_de("banda", passou["banda"])}
    conta = {}
    for r, (minimo, total) in ler_pool().items():
        soma = round(piso[r] + VIDEOS * minimo, 2)
        conta[r] = f"{{piso_mais_videos: {soma:g}, total: {total:g}, cabe: {str(soma <= total).lower()}}}"
        if soma > total:
            print(f"[AVISO] O piso de {r} não cabe no pool: {piso[r]:g} + {VIDEOS} × {minimo:g} = {soma:g} > {total:g}")

    # (linha, comentário): o comentário fica alinhado numa coluna, para o arquivo ser lido por gente também
    linhas = [
        ("# Calibração da VR (Fases 3 e 4): SLA e pisos da fatia de VR na upf4.", None),
        (f"# Gerado por vr/graficos_calibracao.py em {datetime.now():%Y-%m-%d %H:%M}. "
         "Não editar à mão: rodar o script de novo.", None),
        ("# Fontes (metricas_scripts/resultados/calibracao/, fora do git):", None),
        *[(f"#   {os.path.basename(c)}", None) for c in csvs],
        *[(f"#   {os.path.basename(c)}  (com os 3 vídeos tocando)", None) for c in com_video],
        ("", None),
        ("sla:", "por janela de 12 s; falhar em qualquer critério = fora do SLA"),
        (f"  net_ref_ms: {net_ref:.2f}", "net_p95 sem aperto (pontos de folga: CPU 0,4 e banda 15)"),
        (f"  folga_net_ms: {SLA_NET_FOLGA_MS:g}", f"net_p95 ≤ {net_ref + SLA_NET_FOLGA_MS:.2f} ms"),
        (f"  perda_max_pct: {SLA_PERDA_MAX:g}", None),
        (f"  cont_min_pct: {SLA_CONT_MIN:g}", None),
        (f"  vazao_min_mbps: {SLA_VAZAO_MIN:g}", f"95% do fluxo de {FLUXO_MBPS:g} Mbps"),
        ("piso:", "menor ponto que passa (e todos acima dele), + 10%"),
        *([(f"  cpu: {piso['cpu']:g}", f"com os 3 vídeos: rende o mesmo que a folga desde {passou['cpu_video']:g} "
                                       "núcleo; para cima em 10m"),
           (f"  cpu_sem_video: {piso_de('cpu', passou['cpu']):g}", f"só referência (VR sozinha, Fase 3): "
                                                                  f"≥ {SLA_JANELAS:g}% das janelas desde {passou['cpu']:g}")]
          if tem_video else
          [(f"  cpu: {piso['cpu']:g}", f"≥ {SLA_JANELAS:g}% das janelas desde {passou['cpu']:g} núcleo; para cima em 10m")]),
        (f"  banda_mbps: {piso['banda']:g}", f"≥ {SLA_JANELAS:g}% das janelas desde {passou['banda']:g} Mbps; "
                                             "para cima em 0,1"),
        ("pool:", f"piso + {VIDEOS} × mínimo do vídeo ≤ total (tests_configs/test_config.yaml, auction)"),
        (f"  cpu: {conta['cpu']}", None),
        (f"  banda_mbps: {conta['banda']}", None),
    ]
    with open(caminho, "w") as f:
        for linha, comentario in linhas:
            f.write(f"{linha:<34}# {comentario}\n" if comentario else f"{linha}\n")
    print(f"\n[YAML] {caminho}: piso de CPU {piso['cpu']:g}, piso de banda {piso['banda']:g} Mbps")


def figura(recurso, series, net_ref, pasta):
    """Os três painéis de um recurso, em PDF (artigo) e PNG (olhar rápido). series: [(carga, medidas, pontos)],
    uma curva por carga (sem vídeo / com vídeo), com legenda quando há mais de uma."""
    fig, (ax_net, ax_vaz, ax_sla) = plt.subplots(3, 1, figsize=(3.45, 5.2), sharex=True)
    # O rótulo do limite vai do lado da linha tracejada onde não há dados: acima no atraso (os pontos bons ficam
    # embaixo), abaixo na vazão e no SLA (os pontos bons ficam em cima); no SLA, à esquerda, porque à direita a
    # curva com vídeo fica logo abaixo dos 95%
    paineis = [(ax_net, "net", "Net RTT p95 (ms)", net_ref + SLA_NET_FOLGA_MS, f"SLA: ref + {SLA_NET_FOLGA_MS} ms", 2, 1),
               (ax_vaz, "vazao", "Throughput (Mbps)", SLA_VAZAO_MIN, "SLA: 95% of stream", -2, 1),
               (ax_sla, "sla", "Windows in SLA (%)", SLA_JANELAS, f"{SLA_JANELAS:g}% of windows", -2, 0)]
    for ax, col, rotulo, limite, texto, desloc, lado in paineis:
        for carga, medidas, pontos in series:
            finitas = medidas[np.isfinite(medidas[col])]
            ax.scatter(finitas["valor"], finitas[col], s=8, color=CORES[carga], alpha=0.3, linewidths=0, zorder=2)
            ax.errorbar(pontos["valor"], pontos[col], yerr=pontos[f"{col}_ic"], color=CORES[carga],
                        marker=MARCADOR[carga], ms=3.5, lw=1.2, capsize=2, zorder=3, label=ROTULO[carga])
        ax.axhline(limite, ls="--", lw=0.8, color=COR_LIMITE, zorder=1)
        ax.annotate(texto, xy=(lado, limite), xycoords=("axes fraction", "data"), xytext=(0 if lado else 3, desloc),
                    textcoords="offset points", ha="right" if lado else "left", va="bottom" if desloc > 0 else "top",
                    fontsize=7, color=COR_LIMITE)
        ax.set_ylabel(rotulo)

    # (a) atraso: escala log (de ~2 ms a ~10 s) e ▲ no topo para as medidas sem quadros
    ax_net.set_yscale("log")
    todas = pd.concat([medidas for _, medidas, _ in series])
    finitos = todas.loc[np.isfinite(todas["net"]), "net"]
    ax_net.set_ylim(finitos.min() * 0.6, finitos.max() * 6)               # folga em cima para os ▲
    topo = mtransforms.blended_transform_factory(ax_net.transData, ax_net.transAxes)
    for carga, medidas, _ in series:
        sem = np.sort(medidas.loc[~np.isfinite(medidas["net"]), "valor"].unique())
        if len(sem):
            ax_net.scatter(sem, [0.93] * len(sem), marker="^", s=18, color=CORES[carga], transform=topo, zorder=3)
            ax_net.annotate("no frames", xy=(sem[-1], 0.93), xycoords=topo, xytext=(5, 0),
                            textcoords="offset points", va="center", fontsize=7, color=CORES[carga])
    if len(series) > 1:
        ax_vaz.legend(loc="center right", fontsize=7, frameon=False)       # no meio da vazão não há dados nem rótulos
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
    ap.add_argument("--yaml", default=SAIDA_YAML, help="onde gravar o calibracao.yaml (padrão: vr/calibracao.yaml)")
    ap.add_argument("--com-video", nargs="+", default=[], metavar="CSV",
                    help="CSVs medidos com os 3 vídeos tocando (fora do net_ref; na CPU, dão o piso)")
    args = ap.parse_args()
    pasta = args.saida or os.path.join(os.path.dirname(os.path.abspath(args.csvs[0])), "figuras")

    df = carregar(args.csvs + args.com_video, args.com_video)
    if not (df["carga"] == "sem").any():
        sys.exit("[ERRO] Faltam as séries sem vídeo: é delas que sai o net_ref do SLA.")
    net_ref = calcular_net_ref(df[df["carga"] == "sem"])
    marcar_sla(df, net_ref)
    passou, series = {}, {}
    for (recurso, carga), d in df.groupby(["recurso", "carga"], sort=False):
        medidas, pontos = por_ponto(d)
        if carga == "video":
            passou[f"{recurso}_video"] = menor_equivalente(pontos)
            imprimir(f"{recurso} com os 3 vídeos", pontos, net_ref, passou[f"{recurso}_video"], equivalente=True)
        else:
            passou[recurso] = menor_que_passa(pontos)
            imprimir(recurso, pontos, net_ref, passou[recurso])
        series.setdefault(recurso, []).append((carga, medidas, pontos))
    for recurso, s in series.items():
        figura(recurso, sorted(s, key=lambda x: x[0] != "sem"), net_ref, pasta)   # a curva sem vídeo primeiro
    print(f"\n[FIGURAS] {pasta}")

    falta = [r for r in ("cpu", "banda") if passou.get(r) is None]
    if args.com_video and passou.get("cpu_video") is None:
        falta.append("cpu com vídeo")
    if falta:
        print(f"[YAML] não gravado: falta um ponto que passe em {', '.join(falta)} (ou o CSV desse recurso)")
        return
    escrever_yaml(args.yaml, args.csvs, args.com_video, net_ref, passou)


if __name__ == "__main__":
    main()
