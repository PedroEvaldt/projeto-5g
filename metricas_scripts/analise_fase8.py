#!/usr/bin/env python3
"""
Análise da Fase 8: as tabelas e as figuras das 150 execuções (5 combinações × 30), direto dos CSVs brutos.

Uso (de metricas_scripts/):
    ../venv/bin/python analise_fase8.py                         # lê resultados/ e grava em resultados/fase8_analise/
    ../venv/bin/python analise_fase8.py <resultados> <saída>

A unidade é a execução: cada métrica sai de uma execução (por_execucao.csv, uma linha por execução) e depois é
resumida por combinação com a média e o intervalo de confiança de 95% (t de Student; n = execuções que têm o
valor, escrito na célula quando é menor que o total). As comparações do plano (L0 × L1 e L1 × L2 no H-VR, L2 × L3
no SC-VR) dão a diferença das médias, com o IC de 95% e o p do teste t de Welch.

Grava em <saída>:
  por_execucao.csv           uma linha por execução, com todas as métricas
  tempo.csv                  as séries por período de 30 s (média e IC entre as execuções), base das linhas do tempo
  tabelas.md                 as tabelas, prontas para os docs e os slides (e um tabela_<nome>.csv para cada)
  <figura>.png / .pdf        PNG para os slides, PDF para o artigo

As réguas de tempo, como no resumo_execucao.py: os eventos e as janelas da VR contam do TEST_START; a QoE do vídeo
conta do início do player, ~10 s antes (DESLOCAMENTO_QOE_S); o leilão grava a hora local da ledzeppelin, convertida
pelo inicio= do TEST_START. O PFCP (upf_pfcp.txt) está no relógio do cluster, com a diferença gravada no próprio
arquivo, e só contam os eventos do nosso SMF, como no conferir_execucao.py. O ciclo 1 rodou antes de o arquivo
gravar o SMF de cada evento: ali o PFCP fica sem valor (n=29).
"""
import re
import sys
import warnings
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml
from scipy import stats

BASE = Path(__file__).resolve().parent
RESULTADOS = Path(sys.argv[1]) if len(sys.argv) > 1 else BASE / "resultados"
SAIDA = Path(sys.argv[2]) if len(sys.argv) > 2 else RESULTADOS / "fase8_analise"
CALIBRACAO = BASE.parent / "vr" / "calibracao.yaml"

COMBINACOES = {                                     # pasta -> (cenário, configuração)
    "hvr_l0": ("H-VR", "L0"),
    "hvr_l1": ("H-VR", "L1"),
    "hvr_l2": ("H-VR", "L2"),
    "scvr_l2": ("SC-VR", "L2"),
    "scvr_l3": ("SC-VR", "L3"),
}
HVR = ["hvr_l0", "hvr_l1", "hvr_l2"]
SCVR = ["scvr_l2", "scvr_l3"]
COMPARACOES = [("hvr_l0", "hvr_l1"), ("hvr_l1", "hvr_l2"), ("scvr_l2", "scvr_l3")]   # as perguntas do plano
VIDEOS = {"H-VR": ["gold", "silver", "bronze"], "SC-VR": ["policia", "bombeiros", "ambulancia"]}
NOMES = {"gold": "gold", "silver": "silver", "bronze": "bronze", "policia": "polícia", "bombeiros": "bombeiros",
         "ambulancia": "ambulância", "vr": "VR", "drone": "drone"}
DESLOCAMENTO_QOE_S = 10     # a QoE começa ~10 s antes dos eventos (o mesmo do resumo_execucao.py)
PERIODO_S = 30              # período das linhas do tempo, o mesmo do leilão
PFCP_PRESA_S = 30           # UPF sem PFCP no fim há mais que isso: presa (o mesmo do conferir_execucao.py)
AVISOS = {                  # tipo de aviso -> trecho do veredito.txt
    "VR ativa sem janela por mais de 60 s": "VR ativa sem janela",
    "UPF sem PFCP por mais de 60 s (voltou)": "sem PFCP por",
    "UPF presa no fim, com a sessão de pé": "sessão do cliente seguiu de pé",
    "vídeo com os quadros parados": "quadros parados",
}
# Uma cor por configuração (a L2 tem a mesma nos dois cenários): os 4 primeiros tons da paleta categórica da skill
# de visualização, validados para daltonismo; azul, laranja e verde-água ficam abaixo de 3:1 de contraste só no
# amarelo e no verde, por isso toda figura tem legenda e rótulos escritos
CORES = {"L0": "#2a78d6", "L1": "#eb6834", "L2": "#1baf7a", "L3": "#eda100"}
TINTA, TINTA_2, GRADE = "#222222", "#555555", "#dddddd"

plt.rcParams.update({
    "font.family": "sans-serif", "font.size": 10, "axes.titlesize": 11, "axes.labelsize": 10,
    "xtick.labelsize": 9, "ytick.labelsize": 9, "legend.fontsize": 9, "lines.linewidth": 1.8,
    "axes.edgecolor": "#888888", "axes.labelcolor": TINTA, "text.color": TINTA,
    "xtick.color": TINTA_2, "ytick.color": TINTA_2, "axes.grid": True, "grid.color": GRADE, "grid.linewidth": 0.6,
    "axes.spines.top": False, "axes.spines.right": False, "axes.axisbelow": True, "savefig.dpi": 200,
})


# ---------------------------------------------------------------------------------------------------------------
# Uma execução
# ---------------------------------------------------------------------------------------------------------------
def ler_csv(pasta, nome):
    arq = pasta / nome
    return pd.read_csv(arq) if arq.exists() else None


def tempos(ev, tipo):
    return pd.to_numeric(ev.loc[ev["Event_Type"] == tipo, "Timestamp_Offset_Seconds"], errors="coerce").tolist()


def trechos_vr(ev, t_fim):
    """Os trechos com a VR ativa: de cada VR_JOIN até o VR_LEAVE seguinte (ou o fim)."""
    saidas = tempos(ev, "VR_LEAVE")
    return [(e, min(next((s for s in saidas if s > e), t_fim), t_fim)) for e in tempos(ev, "VR_JOIN")]


def dentro(t, trechos):
    """Máscara: cada valor de t cai em algum dos trechos."""
    t = pd.Series(t)
    m = pd.Series(False, index=t.index)
    for a, b in trechos:
        m |= (t >= a) & (t < b)
    return m


def inicio_do_teste(ev):
    detalhes = " ".join(ev.loc[ev["Event_Type"] == "TEST_START", "Details"].astype(str))
    return pd.Timestamp(re.search(r"inicio=(\S+)", detalhes).group(1))


def metricas_vr(j, ev, sla, trechos):
    """SLA, latência e entradas da VR, das janelas do coletor (qoe_vr_timeseries.csv)."""
    m = {}
    marca = j["Window_Type"].isin(["pausa", "retomada"])
    contam = j[~marca & (j["Health"] == 1)]                    # health=0 é falha do instrumento: fora da conta
    normais = contam[contam["Window_Type"] == "normal"]
    m["vr_janelas"] = len(contam)
    m["vr_sla_pct"] = 100 * contam["SLA_OK"].mean() if len(contam) else np.nan
    m["vr_net_p95_ms"] = normais["Net_p95_ms"].median()
    m["vr_m2p_p95_ms"] = normais["M2P_p95_ms"].median()
    m["vr_perda_pct"] = normais["Loss_pct"].mean()
    m["vr_vazao_mbps"] = normais["Throughput_Mbps"].median()
    m["vr_reprovadas_net"] = int((normais["Net_p95_ms"] > sla["net_ref_ms"] + sla["folga_net_ms"]).sum())
    m["vr_reprovadas_vazao"] = int((normais["Throughput_Mbps"] < sla["vazao_min_mbps"]).sum())
    m["vr_sem_quadros"] = int((contam["Window_Type"] == "sem_quadros").sum())

    # Cada entrada (marca de retomada): tempo até a 1ª janela no SLA, como no resumo_execucao.py
    for i, r in enumerate(j.index[j["Window_Type"] == "retomada"], 1):
        t0 = j.loc[r, "Timestamp_Offset_Seconds"]
        depois = j.loc[r + 1:]
        pausa = depois.index[depois["Window_Type"] == "pausa"]
        depois = depois.loc[:pausa[0] - 1] if len(pausa) else depois
        ok = depois[~depois["Window_Type"].isin(["pausa", "retomada"]) & (depois["SLA_OK"] == 1)]
        m[f"vr_entrada{i}_sla_s"] = ok["Timestamp_Offset_Seconds"].iloc[0] - t0 if len(ok) else np.nan
        m[f"vr_entrada{i}_no_sla"] = int(len(ok) > 0)

    # Tempo até o piso, do VR_JOIN (o deploy_test.py escreve "piso aplicado em X s"; a L0 não tem piso)
    pisos = ev.loc[ev["Event_Type"] == "VR_JOIN", "Details"].astype(str).str.extract(r"piso aplicado em ([\d.]+) s")[0]
    for i, p in enumerate(pd.to_numeric(pisos, errors="coerce"), 1):
        m[f"vr_entrada{i}_piso_s"] = p

    # Tempo com a VR ativa e sem janela (na L0, a VR sem sessão). O maior trecho é a conta do conferir_execucao.py
    # (~13 s é o normal: uma janela); o total soma o que passa de uma janela em cada intervalo maior que 1,5 janela
    janela = janela_s(j)
    maior, falta, ativa, ultimo = 0.0, 0.0, False, None

    def intervalo(dt):
        nonlocal maior, falta
        maior = max(maior, dt)
        falta += dt - janela if dt > 1.5 * janela else 0.0

    for _, r in j.sort_values("Timestamp_Offset_Seconds").iterrows():
        t = r["Timestamp_Offset_Seconds"]
        if r["Window_Type"] == "retomada":
            ativa, ultimo = True, t
        elif r["Window_Type"] == "pausa":
            if ativa:
                intervalo(t - ultimo)
            ativa = False
        elif ativa:
            intervalo(t - ultimo)
            ultimo = t
    if ativa:
        intervalo(trechos[-1][1] - ultimo)
    m["vr_sem_janela_s"] = maior
    m["vr_sem_janela_total_s"] = falta
    # Tempo ativo no SLA: como o % de janelas, mas o tempo sem janela conta como fora (as janelas que faltaram)
    m["vr_tempo_sla_pct"] = 100 * contam["SLA_OK"].sum() / (len(contam) + falta / janela) if len(contam) else np.nan
    return m


def janela_s(j):
    """Duração de uma janela da VR (~12,6 s): a mediana do intervalo entre janelas seguidas."""
    t = j.loc[~j["Window_Type"].isin(["pausa", "retomada"]), "Timestamp_Offset_Seconds"].diff()
    t = t[t < 20]
    return t.median() if len(t) else 12.6


def alocacao(leilao, agente, t):
    """CPU e banda dadas pelo leilão a uma fatia em cada instante t: o último valor gravado antes (lances e piso)."""
    a = leilao.loc[leilao["Agent"] == agente, ["t", "CPU_Allocated", "BW_Allocated"]].sort_values("t")
    return pd.merge_asof(pd.DataFrame({"t": np.asarray(t, dtype=float)}), a, on="t")


def metricas_leilao(leilao, vr, videos, trechos, t_fim):
    m = {}
    lances = leilao[leilao["Result"].isin(["WINNER", "LOSER"]) & leilao["t"].between(0, t_fim)]
    da_vr = lances[lances["Agent"] == f"{vr}_slice"]
    m["rodadas"] = lances["Auction_ID"].nunique()
    m["vr_vitorias"] = int((da_vr["Result"] == "WINNER").sum())
    m["vr_vitorias_lance0"] = int(((da_vr["Result"] == "WINNER") & (da_vr["Bid_Value"] == 0)).sum())
    m["vr_lance_medio"] = da_vr["Bid_Value"].mean()
    # Médias no tempo (segundo a segundo), com a VR ativa: o que a VR teve e o que sobrou para cada vídeo
    grade = np.arange(0.0, t_fim, 1.0)
    ativa = dentro(grade, trechos).values
    al = alocacao(leilao, f"{vr}_slice", grade)
    m["vr_cpu_ativa"] = al.loc[ativa, "CPU_Allocated"].mean()
    m["vr_banda_ativa_mbps"] = al.loc[ativa, "BW_Allocated"].mean()
    for f in videos:
        al = alocacao(leilao, f"{f}_slice", grade)
        m[f"{f}_banda_vr_ativa_mbps"] = al.loc[ativa, "BW_Allocated"].mean()
    return m


def metricas_video(q, videos, trechos):
    """QoE de cada vídeo: só as amostras tocando, na execução inteira, com a VR ativa e com a VR fora."""
    m = {}
    tocando = q[q["Status"] == "OK"]
    ativa = dentro(tocando["t"], trechos).values
    for f in videos:
        e = (tocando["Slice"] == f).values
        m[f"{f}_bitrate_kbps"] = tocando.loc[e, "Bitrate_kbps"].mean()
        m[f"{f}_bitrate_vr_ativa_kbps"] = tocando.loc[e & ativa, "Bitrate_kbps"].mean()
        m[f"{f}_bitrate_vr_fora_kbps"] = tocando.loc[e & ~ativa, "Bitrate_kbps"].mean()
        m[f"{f}_stalls"] = q.loc[q["Slice"] == f, "Stalls"].max()              # cumulativos: o máximo é o total
        m[f"{f}_trocas"] = q.loc[q["Slice"] == f, "Quality_Switches"].max()
    return m


def quedas_pfcp(texto, inicio, t_fim):
    """Quedas do PFCP com o nosso SMF na régua do teste: [(upf, início, fim ou None se ainda caída no fim)].
    None se o arquivo não tem o SMF de cada evento (ciclo 1) ou a diferença de relógio."""
    dif = re.search(r"diferenca_s=(-?[\d.]+)", texto)
    smf = re.search(r"^# smf=(\S+)", texto, re.M)
    if not (dif and smf):
        return None
    t0 = inicio.tz_convert("UTC") + pd.Timedelta(seconds=float(dif.group(1)))     # no relógio do cluster
    por_upf = {}
    for linha in texto.splitlines():
        p = linha.split()
        if len(p) == 4 and p[0].startswith("upf") and p[3] == smf.group(1):
            por_upf.setdefault(p[0], []).append(((pd.Timestamp(p[1]) - t0).total_seconds(), p[2]))
    quedas = []
    for upf, evs in por_upf.items():
        desde = None
        for t, estado in sorted(evs):
            if t > t_fim:
                break
            if estado == "de-associated":
                desde = t if desde is None else desde
            elif desde is not None:
                if t >= 0:                                     # as que caíram e voltaram antes do início não contam
                    quedas.append((upf, max(desde, 0.0), t))
                desde = None
        if desde is not None:
            quedas.append((upf, max(desde, 0.0), None))
    return quedas


def metricas_pfcp(quedas, upf_vr, t_fim):
    if quedas is None:
        return {}
    dur = [(f if f is not None else t_fim) - i for _, i, f in quedas]
    return {
        "pfcp_quedas": len(quedas),
        "pfcp_quedas_upf_vr": sum(u == upf_vr for u, _, _ in quedas),
        "pfcp_quedas_upf_video": sum(u != upf_vr for u, _, _ in quedas),
        "pfcp_maior_queda_s": max(dur, default=0.0),
        "pfcp_upf_presa_fim": sum(f is None and t_fim - i > PFCP_PRESA_S for _, i, f in quedas),
    }


def metricas_llm(llm, vr, iniciais):
    """Respostas do LLM e o que ele subiu na 1ª intenção (o incêndio, quando o drone entra)."""
    validas = llm[llm["Status"] == "valida"]
    primeira = validas[validas["Timestamp"] == validas["Timestamp"].min()]
    subiu = sorted(NOMES.get(f, f) for f, p in zip(primeira["Fatia"], primeira["Prioridade"]) if p > iniciais.get(f, 5))
    drone = validas.loc[validas["Fatia"] == vr, "Prioridade"]
    return {
        "llm_respostas": llm["Timestamp"].nunique(),
        "llm_validas": len(validas),
        "llm_descartadas": int((llm["Status"] != "valida").sum()),
        "llm_incendio_subiu": " + ".join(subiu) or "nenhuma",
        "llm_vr_prioridade_max": drone.max() if len(drone) else iniciais[vr],
    }


def serie_tempo(m, periodos, chave, valores):
    """Acrescenta uma série por período a m (lista de dicts): {período: valor}."""
    for p in periodos:
        if p in valores and pd.notna(valores[p]):
            m.append({"serie": chave, "periodo": p, "valor": valores[p]})


def analisar(pasta, combinacao, sla):
    """Lê uma execução. Devolve (métricas, séries por período de 30 s)."""
    cenario, _ = COMBINACOES[combinacao]
    cfg = yaml.safe_load(open(pasta / "config.yaml"))
    fatias = cfg["slices"]
    vr = next(n for n, s in fatias.items() if s.get("tipo") == "vr")
    videos = VIDEOS[cenario]
    ev = pd.read_csv(pasta / "test_events.csv")
    t_fim = (tempos(ev, "TEST_END") or [cfg["test_metadata"]["duration_seconds"]])[0]
    trechos = trechos_vr(ev, t_fim)
    inicio = inicio_do_teste(ev)
    veredito = (pasta / "veredito.txt").read_text()
    m = {"combinacao": combinacao, "cenario": cenario, "configuracao": COMBINACOES[combinacao][1], "nome": pasta.name,
         "veredito": veredito.split(":")[0].split()[0]}
    for tipo, trecho in AVISOS.items():
        m[f"aviso: {tipo}"] = int(trecho in veredito)

    j = pd.read_csv(pasta / "qoe_vr_timeseries.csv")
    m.update(metricas_vr(j, ev, sla, trechos))

    leilao = pd.read_csv(pasta / "auction_history.csv")
    for col in ["Bid_Value", "Price_Paid", "CPU_Allocated", "BW_Allocated"]:
        leilao[col] = pd.to_numeric(leilao[col], errors="coerce")
    leilao["t"] = (pd.to_datetime(leilao["Timestamp"]) - inicio.tz_localize(None)).dt.total_seconds()
    m.update(metricas_leilao(leilao, vr, videos, trechos, t_fim))

    q = pd.read_csv(pasta / "qoe_timeseries.csv")
    for col in ["Timestamp_Offset_Seconds", "Bitrate_kbps", "Stalls", "Quality_Switches"]:
        q[col] = pd.to_numeric(q[col], errors="coerce")
    q["t"] = q["Timestamp_Offset_Seconds"] - DESLOCAMENTO_QOE_S       # na régua dos eventos
    m.update(metricas_video(q, videos, trechos))

    pfcp = pasta / "upf_pfcp.txt"
    if pfcp.exists():
        m.update(metricas_pfcp(quedas_pfcp(pfcp.read_text(), inicio, t_fim), fatias[vr]["upf"], t_fim))
    llm = ler_csv(pasta, "llm_decisoes.csv")
    if llm is not None:
        m.update(metricas_llm(llm, vr, {n: s.get("initial_priority", 5) for n, s in fatias.items()}))

    # Séries por período de 30 s (a janela conta no período em que termina; a alocação é a do meio do período).
    # VR fora do SLA: um período inteiro com a VR ativa e sem nenhuma janela (sem sessão) conta como 100%
    periodos = list(range(0, int(t_fim) // PERIODO_S * PERIODO_S, PERIODO_S))
    serie = []
    jan = j[~j["Window_Type"].isin(["pausa", "retomada"]) & (j["Health"] == 1)]
    por_p = (100 * (1 - jan.groupby(jan["Timestamp_Offset_Seconds"] // PERIODO_S * PERIODO_S)["SLA_OK"].mean())).to_dict()
    for p in periodos:
        if p not in por_p and any(a <= p and p + PERIODO_S <= b for a, b in trechos):
            por_p[p] = 100.0
    serie_tempo(serie, periodos, "vr_fora_sla_pct", por_p)
    al = alocacao(leilao, f"{vr}_slice", [p + PERIODO_S / 2 for p in periodos])
    serie_tempo(serie, periodos, "vr_banda_mbps", dict(zip(periodos, al["BW_Allocated"])))
    serie_tempo(serie, periodos, "vr_cpu", dict(zip(periodos, al["CPU_Allocated"])))
    tocando = q[(q["Status"] == "OK") & (q["t"] >= 0)]
    br = tocando.groupby([tocando["t"] // PERIODO_S * PERIODO_S, "Slice"])["Bitrate_kbps"].mean()
    for f in videos:
        if f in br.index.get_level_values(1):
            serie_tempo(serie, periodos, f"{f}_bitrate_kbps", br.xs(f, level=1).to_dict())
    serie_tempo(serie, periodos, "videos_bitrate_total_mbps", (br.groupby(level=0).sum() / 1000).to_dict())
    if llm is not None:                                   # prioridade dada pelo LLM (as válidas), no meio do período
        validas = llm[llm["Status"] == "valida"].copy()
        validas["t"] = (pd.to_datetime(validas["Timestamp"]) - inicio.tz_localize(None)).dt.total_seconds()
        for f in [vr, "bombeiros"]:
            d = validas.loc[validas["Fatia"] == f, ["t", "Prioridade"]].sort_values("t")
            pr = pd.merge_asof(pd.DataFrame({"t": [p + PERIODO_S / 2 for p in periodos]}), d, on="t")["Prioridade"]
            serie_tempo(serie, periodos, f"{f}_prioridade",
                        dict(zip(periodos, pr.fillna(fatias[f].get("initial_priority", 5)))))
    tempo = pd.DataFrame(serie)
    tempo.insert(0, "nome", pasta.name)
    tempo.insert(0, "combinacao", combinacao)
    return m, tempo


# ---------------------------------------------------------------------------------------------------------------
# Estatística e tabelas
# ---------------------------------------------------------------------------------------------------------------
def ic95(x):
    """Média, meia-largura do IC de 95% (t de Student) e n, sem os valores que faltam."""
    x = pd.to_numeric(pd.Series(x), errors="coerce").dropna()
    n = len(x)
    if n == 0:
        return np.nan, np.nan, 0
    h = stats.t.ppf(0.975, n - 1) * x.std(ddof=1) / np.sqrt(n) if n > 1 else np.nan
    return x.mean(), h, n


def num(v, casas):
    """Número com vírgula decimal, como nos docs."""
    return f"{v:,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def celula(x, casas, total):
    m, h, n = ic95(x)
    if n == 0:
        return "—"
    txt = num(m, casas) + (f" ± {num(h, casas)}" if pd.notna(h) else "")
    return txt + (f" (n={n})" if n < total else "")


def tabela(df, combos, linhas):
    """Uma linha por métrica (coluna, rótulo, casas), uma coluna por combinação: média ± IC de 95%."""
    corpo = []
    for col, rotulo, casas in linhas:
        linha = [rotulo]
        for c in combos:
            d = df[df["combinacao"] == c]
            linha.append(celula(d[col], casas, len(d)) if col in d else "—")
        corpo.append(linha)
    return pd.DataFrame(corpo, columns=["Métrica"] + [" ".join(COMBINACOES[c]) for c in combos])


def markdown(t):
    linhas = ["| " + " | ".join(t.columns) + " |", "| " + " | ".join("---" for _ in t.columns) + " |"]
    return "\n".join(linhas + ["| " + " | ".join(str(v) for v in r) + " |" for r in t.itertuples(index=False)])


def comparar(df, pares):
    """Diferença das médias (B − A) com o IC de 95% e o p do teste t de Welch, para cada (A, B, coluna, rótulo, casas)."""
    corpo = []
    for a, b, col, rotulo, casas in pares:
        xa = pd.to_numeric(df.loc[df["combinacao"] == a, col], errors="coerce").dropna()
        xb = pd.to_numeric(df.loc[df["combinacao"] == b, col], errors="coerce").dropna()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")                     # duas amostras constantes (ex.: 100% e 100%)
            r = stats.ttest_ind(xb, xa, equal_var=False)
            lo, hi = r.confidence_interval(0.95)
        dif = xb.mean() - xa.mean()
        p = "—" if np.isnan(r.pvalue) else ("< 0,001" if r.pvalue < 0.001 else num(r.pvalue, 3))
        ic = "—" if np.isnan(lo) else f"[{num(lo, casas)}; {num(hi, casas)}]"
        corpo.append([f"{' '.join(COMBINACOES[a])} → {COMBINACOES[b][1]}", rotulo, num(xa.mean(), casas),
                      num(xb.mean(), casas), num(dif, casas), ic, p])
    return pd.DataFrame(corpo, columns=["Comparação", "Métrica", "A", "B", "B − A", "IC 95% de B − A", "p (Welch)"])


def tabelas(df, fila, reparos):
    """Todas as tabelas, na ordem do tabelas.md: [(nome, título, nota, DataFrame)]."""
    todas = list(COMBINACOES)
    saida = []

    # 1. Execuções: contagens (não médias)
    linhas = []
    for c in todas:
        d, f = df[df["combinacao"] == c], fila[fila["combinacao"] == c]
        linha = {"Combinação": " ".join(COMBINACOES[c]), "Válidas": len(d), "Com aviso": int((d["veredito"] == "aviso").sum()),
                 "Tentativas": len(f), "Descartadas": int((f["status"] == "falhou").sum())}
        for tipo in AVISOS:
            linha[tipo] = int(d[f"aviso: {tipo}"].sum())
        linha["Consertos depois dela"] = int((reparos["depois_de"] == c).sum())
        linhas.append(linha)
    saida.append(("execucoes", "Execuções, descartes e avisos",
                  "Uma execução com aviso vale e entra na análise. \"Consertos depois dela\": os consertos automáticos "
                  "feitos antes da tentativa seguinte, contados na combinação da tentativa anterior (a que deixou o "
                  "problema; ele pode ter vindo na pausa de 60 s entre as duas).", pd.DataFrame(linhas)))

    # 2. VR
    saida.append(("vr", "VR: SLA, latência e entradas", "Média ± IC de 95% entre as execuções. As janelas com health=0 "
                  "ficam fora da conta. O % de janelas não vê o tempo em que a VR ficou sem sessão (sem janela); o "
                  "tempo ativa no SLA conta esse tempo como fora. A 1ª entrada do H-VR é com os 3 vídeos tocando (rede cheia); a 2ª, só com o gold.",
                  tabela(df, todas, [
                      ("vr_sla_pct", "Janelas no SLA (%)", 1),
                      ("vr_tempo_sla_pct", "Tempo ativa no SLA (%): sem janela = fora", 1),
                      ("vr_net_p95_ms", "net p95, mediana das janelas (ms)", 2),
                      ("vr_m2p_p95_ms", "m2p p95, mediana das janelas (ms)", 2),
                      ("vr_perda_pct", "Perda média (%)", 3),
                      ("vr_vazao_mbps", "Vazão, mediana das janelas (Mbps)", 2),
                      ("vr_reprovadas_net", "Janelas reprovadas pelo net", 1),
                      ("vr_reprovadas_vazao", "Janelas reprovadas pela vazão", 1),
                      ("vr_sem_quadros", "Janelas sem quadros", 1),
                      ("vr_sem_janela_s", "Maior trecho ativa sem janela (s)", 1),
                      ("vr_sem_janela_total_s", "Tempo ativa sem janela, além do normal (s)", 1),
                      ("vr_entrada1_sla_s", "1ª entrada: tempo até o SLA (s)", 1),
                      ("vr_entrada2_sla_s", "2ª entrada: tempo até o SLA (s)", 1),
                      ("vr_entrada1_piso_s", "1ª entrada: tempo até o piso (s)", 1),
                      ("vr_entrada2_piso_s", "2ª entrada: tempo até o piso (s)", 1),
                  ])))

    # 3. Leilão da VR
    saida.append(("leilao_vr", "VR no leilão", "Média ± IC de 95% entre as execuções; CPU e banda são médias no tempo "
                  "com a VR ativa (o que o leilão deu, segundo a segundo).", tabela(df, todas, [
                      ("rodadas", "Rodadas do teste", 1),
                      ("vr_vitorias", "Rodadas vencidas pela VR", 1),
                      ("vr_vitorias_lance0", "Vencidas com lance 0", 1),
                      ("vr_lance_medio", "Lance médio da VR", 1),
                      ("vr_cpu_ativa", "CPU da VR com ela ativa (núcleos)", 3),
                      ("vr_banda_ativa_mbps", "Banda da VR com ela ativa (Mbps)", 2),
                  ])))

    # 4 e 5. Vídeo, por cenário
    for cen, combos, nome in [("H-VR", HVR, "video_hvr"), ("SC-VR", SCVR, "video_scvr")]:
        linhas = []
        for f in VIDEOS[cen]:
            n = NOMES[f]
            linhas += [(f"{f}_bitrate_kbps", f"{n}: bitrate, execução inteira (kbps)", 0),
                       (f"{f}_bitrate_vr_ativa_kbps", f"{n}: bitrate com a VR ativa (kbps)", 0),
                       (f"{f}_bitrate_vr_fora_kbps", f"{n}: bitrate com a VR fora (kbps)", 0),
                       (f"{f}_banda_vr_ativa_mbps", f"{n}: banda do leilão com a VR ativa (Mbps)", 2),
                       (f"{f}_stalls", f"{n}: stalls", 2),
                       (f"{f}_trocas", f"{n}: trocas de qualidade", 1)]
        saida.append((nome, f"Vídeo no {cen}", "Média ± IC de 95% entre as execuções; bitrate só das amostras tocando "
                      "(um vídeo pausado não conta).", tabela(df, combos, linhas)))

    # 6. LLM (SC-VR)
    t = tabela(df, SCVR, [("llm_respostas", "Respostas do LLM (intenções)", 1),
                          ("llm_descartadas", "Respostas descartadas", 1),
                          ("llm_vr_prioridade_max", "Maior prioridade dada ao drone", 1)])
    linha = ["No incêndio, o LLM subiu"]
    for c in SCVR:
        cont = df.loc[df["combinacao"] == c, "llm_incendio_subiu"].value_counts()
        linha.append(", ".join(f"{k} ({v})" for k, v in cont.items()))
    t.loc[len(t)] = linha
    saida.append(("llm", "LLM no SC-VR", "A prioridade inicial é 5 para todas as fatias; \"subiu\" = acima disso na "
                  "resposta à 1ª intenção (o incêndio). Entre parênteses, em quantas execuções.", t))

    # 7. PFCP
    saida.append(("pfcp", "Plano de controle: quedas do PFCP com o nosso SMF", "Média ± IC de 95% por execução. As "
                  "quedas curtas voltam sozinhas; \"presa no fim\" = sem PFCP no TEST_END há mais de 30 s. O ciclo 1 "
                  "não tem o SMF de cada evento (n=29).", tabela(df, todas, [
                      ("pfcp_quedas", "Quedas por execução (todas as UPFs)", 2),
                      ("pfcp_quedas_upf_vr", "Quedas da upf4 (VR)", 2),
                      ("pfcp_quedas_upf_video", "Quedas das UPFs de vídeo", 2),
                      ("pfcp_maior_queda_s", "Maior queda (s)", 1),
                      ("pfcp_upf_presa_fim", "UPFs presas no fim", 2),
                  ])))

    # 8. Comparações do plano
    pares = []
    for a, b in COMPARACOES[:2]:
        pares += [(a, b, "vr_sla_pct", "VR: janelas no SLA (%)", 1),
                  (a, b, "vr_tempo_sla_pct", "VR: tempo ativa no SLA (%)", 1),
                  (a, b, "vr_entrada1_sla_s", "VR: 1ª entrada, tempo até o SLA (s)", 1),
                  (a, b, "vr_entrada2_sla_s", "VR: 2ª entrada, tempo até o SLA (s)", 1),
                  (a, b, "vr_net_p95_ms", "VR: net p95 (ms)", 2),
                  (a, b, "vr_banda_ativa_mbps", "VR: banda com ela ativa (Mbps)", 2),
                  (a, b, "vr_cpu_ativa", "VR: CPU com ela ativa (núcleos)", 3)]
        pares += [(a, b, f"{f}_bitrate_vr_ativa_kbps", f"{NOMES[f]}: bitrate com a VR ativa (kbps)", 0) for f in VIDEOS["H-VR"]]
    a, b = COMPARACOES[2]
    pares += [(a, b, "vr_sla_pct", "drone: janelas no SLA (%)", 1),
              (a, b, "llm_vr_prioridade_max", "drone: maior prioridade dada pelo LLM", 1),
              (a, b, "vr_banda_ativa_mbps", "drone: banda com ele ativo (Mbps)", 2)]
    pares += [(a, b, f"{f}_bitrate_vr_ativa_kbps", f"{NOMES[f]}: bitrate com o drone ativo (kbps)", 0) for f in VIDEOS["SC-VR"]]
    saida.append(("comparacoes", "Comparações do plano", "A e B são as médias das duas configurações; o IC e o p são do "
                  "teste t de Welch (variâncias diferentes). \"—\": as duas amostras sem variação (ex.: 100% e 100%).",
                  comparar(df, pares)))
    return saida


# ---------------------------------------------------------------------------------------------------------------
# Figuras
# ---------------------------------------------------------------------------------------------------------------
def salvar(fig, nome):
    for ext in ("png", "pdf"):
        fig.savefig(SAIDA / f"{nome}.{ext}", bbox_inches="tight")
    plt.close(fig)
    print(f"[OK] {nome}.png e .pdf")


def pontos_e_media(ax, df, combos, col, casas, sufixo=""):
    """Um ponto por execução (espalhados na horizontal) e, ao lado, a média com o IC de 95%, escrita."""
    rng = np.random.default_rng(8)
    for i, c in enumerate(combos):
        x = pd.to_numeric(df.loc[df["combinacao"] == c, col], errors="coerce").dropna()
        cor = CORES[COMBINACOES[c][1]]
        ax.scatter(i - 0.08 + rng.uniform(-0.16, 0.16, len(x)), x, s=26, color=cor, alpha=0.8,
                   edgecolor="white", linewidth=0.7, zorder=3)
        m, h, n = ic95(x)
        if n:
            ax.errorbar(i + 0.24, m, yerr=0 if np.isnan(h) else h, fmt="o", color=TINTA, ms=5, capsize=4,
                        lw=1.3, zorder=4)
            ax.annotate(num(m, casas) + sufixo, (i + 0.32, m), xytext=(3, 0), textcoords="offset points",
                        va="center", fontsize=9, color=TINTA)
    ax.set_xticks(range(len(combos)), [COMBINACOES[c][1] for c in combos])
    ax.set_xlim(-0.5, len(combos) - 0.1)
    ax.grid(axis="x", visible=False)


def fig_vr_sla(df):
    fig, axes = plt.subplots(1, 2, figsize=(9, 3.8), sharey=True, gridspec_kw={"width_ratios": [3, 2]})
    for ax, combos, titulo in [(axes[0], HVR, "H-VR"), (axes[1], SCVR, "SC-VR (drone)")]:
        pontos_e_media(ax, df, combos, "vr_tempo_sla_pct", 1, "%")
        ax.set_title(titulo)
    axes[0].set_ylabel("Tempo com a VR ativa\nno SLA (%)")
    axes[0].set_ylim(-3, 105)
    fig.suptitle("Um ponto por execução; média e IC de 95% à direita. O tempo sem janela (VR sem sessão) conta "
                 "como fora do SLA", fontsize=10, color=TINTA_2, y=0.99)
    salvar(fig, "vr_sla")


def fig_vr_entradas(df):
    fig, axes = plt.subplots(1, 3, figsize=(11, 3.8), sharey=True, gridspec_kw={"width_ratios": [3, 3, 2]})
    paineis = [(axes[0], HVR, "vr_entrada1_sla_s", "H-VR, 1ª entrada (rede cheia)"),
               (axes[1], HVR, "vr_entrada2_sla_s", "H-VR, 2ª entrada (com sobra)"),
               (axes[2], SCVR, "vr_entrada1_sla_s", "SC-VR (drone)")]
    for ax, combos, col, titulo in paineis:
        pontos_e_media(ax, df, combos, col, 0, " s")
        ax.set_title(titulo)
    axes[0].set_ylabel("Da entrada até a 1ª janela no SLA (s)")
    axes[0].set_ylim(bottom=0)
    fig.suptitle("Uma janela dura ~12,6 s: 12–13 s = no SLA já na 1ª janela", fontsize=10, color=TINTA_2, y=0.99)
    salvar(fig, "vr_entradas")


def fig_video(df, cenario, combos, nome):
    """Bitrate de cada vídeo com a VR ativa, por configuração. Só com a VR ativa: com ela fora, os vídeos que tocam
    são outros (no H-VR, o silver só toca com a VR fora no 1º minuto), e a comparação dentro da execução não vale."""
    videos = VIDEOS[cenario]
    fig, ax = plt.subplots(figsize=(6.5, 3.8))
    largura = 0.8 / len(combos)
    for k, c in enumerate(combos):
        conf = COMBINACOES[c][1]
        ms, hs = [], []
        for f in videos:
            m, h, _ = ic95(df.loc[df["combinacao"] == c, f"{f}_bitrate_vr_ativa_kbps"])
            ms.append(m / 1000)
            hs.append(0 if np.isnan(h) else h / 1000)
        x = np.arange(len(videos)) + (k - (len(combos) - 1) / 2) * largura
        ax.bar(x, ms, largura * 0.92, yerr=hs, color=CORES[conf], label=conf, capsize=3,
               error_kw={"elinewidth": 1.1, "ecolor": TINTA}, zorder=3)
    ax.set_xticks(range(len(videos)), [NOMES[f] for f in videos])
    ax.grid(axis="x", visible=False)
    ax.set_ylabel("Bitrate do vídeo com a\nVR ativa (Mbps)")
    ax.legend(frameon=False, ncol=len(combos), loc="upper right")
    fig.suptitle(f"Vídeo no {cenario}: média e IC de 95% entre as execuções (só amostras tocando)",
                 fontsize=10, color=TINTA_2, y=0.99)
    salvar(fig, nome)


def rotulo_evento(tipo, descricao):
    if tipo == "VR_JOIN":
        return "VR entra"
    if tipo == "VR_LEAVE":
        return "VR sai"
    if tipo in ("TRAFFIC_PAUSE", "TRAFFIC_RESUME"):
        f = descricao.split()[-1]
        return f"{NOMES.get(f, f)} {'pausa' if tipo == 'TRAFFIC_PAUSE' else 'volta'}"
    if tipo == "PRIORITY_CHANGE":
        return "LLM: " + re.sub(r"^Intent:\s*", "", descricao).split()[0].strip(".,")
    return None


def fig_tempo(tempo, df, combos, paineis, nome, titulo):
    """Linhas do tempo por período de 30 s: média entre as execuções, faixa = IC de 95%. Fundo roxo claro = VR ativa
    (nos tempos da 1ª execução: os eventos variam menos de 1 s entre elas)."""
    pasta = RESULTADOS / combos[0] / sorted(df.loc[df["combinacao"] == combos[0], "nome"])[0]
    ev = pd.read_csv(pasta / "test_events.csv")
    t_fim = tempos(ev, "TEST_END")[0]
    fig, axes = plt.subplots(len(paineis), 1, figsize=(10, 1.9 * len(paineis) + 0.8), sharex=True)
    for ax, (serie, rotulo) in zip(axes, paineis):
        for c in combos:
            d = tempo[(tempo["combinacao"] == c) & (tempo["serie"] == serie)]
            if d.empty:
                continue
            g = d.groupby("periodo")["valor"].apply(lambda v: pd.Series(ic95(v), index=["m", "h", "n"])).unstack()
            g = g.reindex(range(0, int(t_fim) // PERIODO_S * PERIODO_S, PERIODO_S))   # período sem valor = buraco
            x = g.index + PERIODO_S / 2
            cor = CORES[COMBINACOES[c][1]]
            teto = 100 if serie.endswith("_pct") else np.inf
            ax.plot(x, g["m"], color=cor, label=COMBINACOES[c][1], marker="o", ms=3, zorder=3)
            ax.fill_between(x, (g["m"] - g["h"].fillna(0)).clip(lower=0), (g["m"] + g["h"].fillna(0)).clip(upper=teto),
                            color=cor, alpha=0.18, linewidth=0, zorder=2)
        ax.set_ylabel(rotulo)
        ax.set_ylim(bottom=0, top=105 if serie.endswith("_pct") else None)
        for a, b in trechos_vr(ev, t_fim):
            ax.axvspan(a, b, color="#6250d6", alpha=0.06, linewidth=0, zorder=0)
    marcas = []                                           # eventos a menos de 15 s um do outro dividem o rótulo
    for _, r in ev.iterrows():
        rot = rotulo_evento(r["Event_Type"], str(r["Description"]))
        t = r["Timestamp_Offset_Seconds"]
        if rot and t > 1:
            if marcas and t - marcas[-1][0] < 15:
                marcas[-1][1] += f" · {rot}"
            else:
                marcas.append([t, rot])
    for t, rot in marcas:
        for ax in axes:
            ax.axvline(t, color="#999999", linestyle=":", linewidth=1, zorder=1)
        axes[0].annotate(rot, (t, 1), xycoords=("data", "axes fraction"), xytext=(0, 4), textcoords="offset points",
                         rotation=90, ha="center", va="bottom", fontsize=8, color=TINTA_2)
    axes[0].legend(frameon=False, ncol=len(combos), loc="lower left", bbox_to_anchor=(0, 1.42))
    axes[-1].set_xlabel("Tempo desde o início do teste (s); fundo roxo = VR ativa")
    axes[-1].set_xlim(0, t_fim)
    fig.align_ylabels(axes)
    fig.text(0.01, 0, titulo, ha="left", va="top", fontsize=9, color=TINTA_2)    # embaixo: em cima ficam os eventos
    salvar(fig, nome)


# ---------------------------------------------------------------------------------------------------------------
def main():
    SAIDA.mkdir(parents=True, exist_ok=True)
    with open(CALIBRACAO) as f:
        sla = yaml.safe_load(f)["sla"]

    metricas, series = [], []
    for c in COMBINACOES:
        pastas = sorted(p for p in (RESULTADOS / c).iterdir() if (p / "veredito.txt").exists())
        print(f"[LER] {c}: {len(pastas)} execuções")
        for p in pastas:
            m, t = analisar(p, c, sla)
            metricas.append(m)
            series.append(t)
    df = pd.DataFrame(metricas)
    tempo = pd.concat(series, ignore_index=True)

    # A fila: ciclo e commit de cada execução válida; os consertos, na combinação da tentativa anterior
    fila = pd.read_csv(RESULTADOS / "fase8_fila.csv")
    validas = fila[fila["status"] != "falhou"].drop_duplicates("nome", keep="last")
    df = df.merge(validas[["nome", "ciclo", "commit"]], on="nome", how="left")
    reparos = pd.read_csv(RESULTADOS / "fase8_reparos.csv")
    fim = pd.to_datetime(fila["fim"])
    reparos["depois_de"] = [fila.loc[fim[fim <= h].idxmax(), "combinacao"] if (fim <= h).any() else None
                            for h in pd.to_datetime(reparos["hora"])]
    df.to_csv(SAIDA / "por_execucao.csv", index=False)
    tempo.groupby(["combinacao", "serie", "periodo"])["valor"].apply(
        lambda v: pd.Series(ic95(v), index=["media", "ic95", "n"])).unstack().reset_index().to_csv(
        SAIDA / "tempo.csv", index=False)

    # Tabelas
    texto = ["# Fase 8: tabelas", "",
             f"Gerado pelo `metricas_scripts/analise_fase8.py` a partir de {len(df)} execuções válidas "
             f"({', '.join(f'{c}: {(df.combinacao == c).sum()}' for c in COMBINACOES)}).", ""]
    todas = tabelas(df, fila, reparos)
    for nome, titulo, nota, t in todas:
        t.to_csv(SAIDA / f"tabela_{nome}.csv", index=False)
        texto += [f"## {titulo}", "", nota, "", markdown(t), ""]
    texto += ["## Consertos automáticos", "", markdown(reparos[["hora", "depois_de", "antes_de", "alvo", "resultado"]]
                                                       .rename(columns={"depois_de": "depois de (combinação)"})), ""]
    (SAIDA / "tabelas.md").write_text("\n".join(texto))
    print(f"[OK] tabelas.md e {len(todas)} tabela_*.csv")

    # Figuras
    fig_vr_sla(df)
    fig_vr_entradas(df)
    fig_video(df, "H-VR", HVR, "video_hvr")
    fig_video(df, "SC-VR", SCVR, "video_scvr")
    fig_tempo(tempo, df, HVR, [("vr_fora_sla_pct", "VR fora do\nSLA (%)¹"), ("vr_banda_mbps", "Banda da\nVR (Mbps)"),
                               ("vr_cpu", "CPU da VR\n(núcleos)"), ("videos_bitrate_total_mbps", "Vídeos: bitrate\nsomado (Mbps)")],
              "hvr_tempo", "H-VR ao longo do teste: média entre as 30 execuções, faixa = IC de 95%\n"
              "¹ % de janelas fora do SLA; um período inteiro com a VR ativa e sem janela (sem sessão) conta como 100%")
    fig_tempo(tempo, df, SCVR, [("drone_prioridade", "Prioridade\ndo drone"), ("bombeiros_prioridade", "Prioridade\ndos bombeiros"),
                                ("bombeiros_bitrate_kbps", "Bombeiros\n(kbps)"),
                                ("policia_bitrate_kbps", "Polícia\n(kbps)"), ("ambulancia_bitrate_kbps", "Ambulância\n(kbps)")],
              "scvr_tempo", "SC-VR ao longo do teste: média entre as 30 execuções, faixa = IC de 95%\n"
              "Prioridades dadas pelo LLM; a banda do drone fica no piso (9,9 Mbps) nas duas configurações")
    print(f"\nTudo em {SAIDA}")


if __name__ == "__main__":
    main()
