#!/usr/bin/env python3
"""
Análise da Fase 9: as execuções com o render real (resultados/fase9/) comparadas com as mesmas combinações da Fase 8.

Uso (de metricas_scripts/):
    ../venv/bin/python analise_fase9.py                    # lê resultados/fase9/ e resultados/fase8_analise/
    ../venv/bin/python analise_fase9.py <pasta da fase 9> <por_execucao.csv da fase 8> <saída> [rótulo] [rótulo da referência]

    # o controle (servidor sintético no metallica) contra a Fase 8, e o render real contra o controle:
    ../venv/bin/python analise_fase9.py resultados/fase9s resultados/fase8_analise/por_execucao.csv resultados/fase9s_analise Controle
    ../venv/bin/python analise_fase9.py resultados/fase9 resultados/fase9s_analise/por_execucao.csv resultados/fase9_vs_9s "Fase 9" Controle

Cada execução da Fase 9 passa pelo mesmo analisar() do analise_fase8.py, com o SLA da calibração dela (vr.calibracao
do config.yaml: o vr/calibracao_render.yaml). A Fase 8 vem do por_execucao.csv que o analise_fase8.py gravou.

A pergunta é uma só: as métricas da Fase 9 ficam dentro da variação entre as execuções da Fase 8? Por combinação e
métrica: a média ± IC de 95% de cada fase, a faixa da Fase 8 (mínimo a máximo das 30), quantas execuções da Fase 9
caem nessa faixa e a diferença das médias, com o IC de 95% e o p do teste t de Welch.

Só da Fase 9, do render_timeseries.csv: quadros cortados, sem a pose da sessão, atrasados e reancorados.

Grava em <saída> (padrão: resultados/fase9_analise/): por_execucao.csv, tabelas.md e um tabela_<nome>.csv por tabela.
Os rótulos (padrão "Fase 9" e "Fase 8") nomeiam as colunas. A referência pode ser o por_execucao.csv de outra
análise deste script (ex.: o do controle). Entram só as combinações com pasta em <pasta da fase 9>.
"""
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

import analise_fase8 as a8
import calibracao_vr

BASE = Path(__file__).resolve().parent
FASE9 = Path(sys.argv[1]) if len(sys.argv) > 1 else BASE / "resultados" / "fase9"
FASE8 = Path(sys.argv[2]) if len(sys.argv) > 2 else BASE / "resultados" / "fase8_analise" / "por_execucao.csv"
SAIDA = Path(sys.argv[3]) if len(sys.argv) > 3 else BASE / "resultados" / "fase9_analise"
ROTULO = sys.argv[4] if len(sys.argv) > 4 else "Fase 9"
REF = sys.argv[5] if len(sys.argv) > 5 else "Fase 8"
RES = FASE9.parent                       # onde ficam o <pasta>_fila.csv e o <pasta>_reparos.csv (ex.: fase9_fila.csv)
COMBINACOES = ["hvr_l0", "hvr_l2", "scvr_l2"]
NET_MAX_FASE8 = 4.87                     # o limite do net do vr/calibracao.yaml (Fase 8), para a linha de conferência

# (coluna, rótulo, casas): as métricas da comparação, na ordem das tabelas
METRICAS_VR = [
    ("vr_sla_pct", "VR: janelas no SLA (%), cada fase com a sua calibração", 1),
    ("vr_sla_pct_limite_f8", f"VR: janelas no SLA (%) com o net ≤ {a8.num(NET_MAX_FASE8, 2)} ms da Fase 8", 1),
    ("vr_tempo_sla_pct", "VR: tempo ativa no SLA (%)", 1),
    ("vr_net_p95_ms", "VR: net p95, mediana das janelas (ms)", 2),
    ("vr_m2p_p95_ms", "VR: m2p p95, mediana das janelas (ms)", 2),
    ("vr_perda_pct", "VR: perda média (%)", 3),
    ("vr_vazao_mbps", "VR: vazão, mediana das janelas (Mbps)", 2),
    ("vr_entrada1_sla_s", "VR: 1ª entrada, tempo até o SLA (s)", 1),
    ("vr_entrada2_sla_s", "VR: 2ª entrada, tempo até o SLA (s)", 1),
    ("vr_vitorias", "Leilão: rodadas vencidas pela VR", 1),
    ("vr_cpu_ativa", "Leilão: CPU da VR com ela ativa (núcleos)", 3),
    ("vr_banda_ativa_mbps", "Leilão: banda da VR com ela ativa (Mbps)", 2),
]
METRICAS_PFCP = [
    ("pfcp_quedas", "PFCP: quedas por execução (todas as UPFs)", 2),
    ("pfcp_quedas_upf_vr", "PFCP: quedas da upf4", 2),
]


def video(cenario):
    return [(f"{f}_bitrate_vr_ativa_kbps", f"{a8.NOMES[f]}: bitrate com a VR ativa (kbps)", 0) for f in a8.VIDEOS[cenario]]


def metricas_render(pasta):
    """Contadores do servidor na execução (diferença entre o fim e o começo do render_timeseries.csv), em % dos enviados."""
    arq = pasta / "render_timeseries.csv"
    if not arq.exists():
        return {}
    r = pd.read_csv(arq)
    d = {c: r[c].max() - r[c].min() for c in r.columns if c.endswith("_Total")}
    enviados = d.get("Frames_Sent_Total", 0)
    pct = lambda c: 100 * d[c] / enviados if enviados and c in d else np.nan
    return {"render_enviados": enviados, "render_cortados_pct": pct("Enc_Truncated_Total"),
            "render_sem_pose_pct": pct("Frames_Pose_Fallback_Total"), "render_atrasados_pct": pct("Frames_Late_Total"),
            "render_reancorados": d.get("Frames_Reanchored_Total", np.nan),
            "render_idade_p95_ms": 1000 * r["Render_Frame_Age_S"].quantile(0.95) if "Render_Frame_Age_S" in r else np.nan,
            "render_source_min": r["Render_Source"].min() if "Render_Source" in r else np.nan}


def sla_limite_f8(pasta, sla):
    """% de janelas no SLA da Fase 9 com o limite do net da Fase 8 no lugar do novo (perda, continuidade e vazão iguais)."""
    j = pd.read_csv(pasta / "qoe_vr_timeseries.csv")
    contam = j[~j["Window_Type"].isin(["pausa", "retomada"]) & (j["Health"] == 1)]
    if not len(contam):
        return np.nan
    ok = ((contam["Window_Type"] == "normal") & (contam["Net_p95_ms"] <= NET_MAX_FASE8)
          & (contam["Loss_pct"] <= sla["perda_max_pct"]) & (contam["Continuity_pct"] >= sla["cont_min_pct"])
          & (contam["Throughput_Mbps"] >= sla["vazao_min_mbps"]))
    return 100 * ok.mean()


def faixa(x8, casas):
    x8 = pd.to_numeric(x8, errors="coerce").dropna()
    return f"[{a8.num(x8.min(), casas)}; {a8.num(x8.max(), casas)}]" if len(x8) else "—"


def comparar(f8, f9, combo, linhas):
    """Uma linha por métrica: referência e Fase 9 (média ± IC), a faixa da referência, quantas da Fase 9 caem nela e
    a diferença das médias (Fase 9 − referência) com o IC de 95% e o p do teste t de Welch."""
    a, b = f8[f8["combinacao"] == combo], f9[f9["combinacao"] == combo]
    corpo = []
    for col, rotulo, casas in linhas:
        xa = pd.to_numeric(a.get(col, pd.Series(dtype=float)), errors="coerce").dropna()
        xb = pd.to_numeric(b.get(col, pd.Series(dtype=float)), errors="coerce").dropna()
        dentro = f"{int(((xb >= xa.min()) & (xb <= xa.max())).sum())} de {len(xb)}" if len(xa) and len(xb) else "—"
        dif, ic, p = "—", "—", "—"
        if len(xa) > 1 and len(xb) > 1:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")                     # amostras constantes (ex.: 100% e 100%)
                r = stats.ttest_ind(xb, xa, equal_var=False)
                lo, hi = r.confidence_interval(0.95)
            dif = a8.num(xb.mean() - xa.mean(), casas)
            ic = "—" if np.isnan(lo) else f"[{a8.num(lo, casas)}; {a8.num(hi, casas)}]"
            p = "—" if np.isnan(r.pvalue) else ("< 0,001" if r.pvalue < 0.001 else a8.num(r.pvalue, 3))
        corpo.append([rotulo, a8.celula(xa, casas, len(a)), faixa(xa, casas), a8.celula(xb, casas, len(b)), dentro,
                      dif, ic, p])
    return pd.DataFrame(corpo, columns=["Métrica", REF, f"Faixa: {REF}", ROTULO, f"{ROTULO} na faixa",
                                        f"{ROTULO} − {REF}", "IC 95%", "p (Welch)"])


def main():
    SAIDA.mkdir(parents=True, exist_ok=True)
    f8 = pd.read_csv(FASE8)
    # só as combinações dos dois lados (o controle só tem a hvr_l2)
    combinacoes = [c for c in COMBINACOES if (FASE9 / c).exists() and (f8["combinacao"] == c).any()]
    f8 = f8[f8["combinacao"].isin(combinacoes)]

    metricas = []
    for c in combinacoes:
        pastas = sorted(p for p in (FASE9 / c).iterdir() if (p / "veredito.txt").exists()) if (FASE9 / c).exists() else []
        print(f"[LER] {c}: {len(pastas)} execuções ({ROTULO}), {(f8['combinacao'] == c).sum()} ({REF})")
        for p in pastas:
            sla = calibracao_vr.ler(p)["sla"]
            m, _ = a8.analisar(p, c, sla)
            m["vr_sla_pct_limite_f8"] = sla_limite_f8(p, sla)
            m["net_max_ms"] = sla["net_ref_ms"] + sla["folga_net_ms"]
            m.update(metricas_render(p))
            metricas.append(m)
    f9 = pd.DataFrame(metricas)
    if f9.empty:
        sys.exit(f"[ERRO] Nenhuma execução com veredito.txt em {FASE9}")
    if "vr_sla_pct_limite_f8" not in f8:
        f8 = f8.assign(vr_sla_pct_limite_f8=f8["vr_sla_pct"])       # na Fase 8, o limite é o dela
    f9.to_csv(SAIDA / "por_execucao.csv", index=False)

    texto = [f"# {ROTULO} × {REF}", "",
             f"Gerado pelo `metricas_scripts/analise_fase9.py`: {len(f9)} execuções válidas em `{FASE9.name}` ({ROTULO}: "
             f"{', '.join(f'{c}: {(f9.combinacao == c).sum()}' for c in combinacoes)}), comparadas com as de "
             f"`{FASE8}` ({REF}: {', '.join(f'{c}: {(f8.combinacao == c).sum()}' for c in combinacoes)}). Limite do net "
             f"({ROTULO}): {', '.join(sorted({a8.num(v, 2) for v in f9['net_max_ms']}))} ms (Fase 8: {a8.num(NET_MAX_FASE8, 2)} ms).", ""]
    tabelas = []

    # Execuções da fila
    fila_arq, rep_arq = RES / f"{FASE9.name}_fila.csv", RES / f"{FASE9.name}_reparos.csv"
    if fila_arq.exists():
        fila = pd.read_csv(fila_arq)
        reparos = pd.read_csv(rep_arq) if rep_arq.exists() else pd.DataFrame(columns=["antes_de"])
        linhas = []
        for c in combinacoes:
            f = fila[fila["combinacao"] == c]
            d = f9[f9["combinacao"] == c]
            linhas.append({"Combinação": " ".join(a8.COMBINACOES[c]), "Válidas": len(d),
                           "Com aviso": int((d["veredito"] == "aviso").sum()), "Tentativas": len(f),
                           "Descartadas": int((f["status"] == "falhou").sum()),
                           # consertos feitos antes de uma tentativa desta combinação (pelo nome dela na fila)
                           "Consertos antes dela": int(reparos["antes_de"].astype(str).isin(f["nome"]).sum())})
        tabelas.append(("execucoes", "Execuções da fila", f"Tentativas, descartes e consertos automáticos "
                        f"({fila_arq.name} e {rep_arq.name}).", pd.DataFrame(linhas)))

    for c in combinacoes:
        cenario = a8.COMBINACOES[c][0]
        tabelas.append((c, f"{' '.join(a8.COMBINACOES[c])}: {REF} × {ROTULO}",
                        f"Média ± IC de 95% entre as execuções; a faixa é o mínimo e o máximo das execuções ({REF}). "
                        f"Cada um julga a VR pela sua calibração; a 2ª linha julga as duas com o limite do net da Fase 8.",
                        comparar(f8, f9, c, METRICAS_VR + video(cenario) + METRICAS_PFCP)))

    rend = [("render_cortados_pct", "Quadros cortados (%)", 2), ("render_sem_pose_pct", "Quadros sem a pose da sessão (%)", 2),
            ("render_atrasados_pct", "Quadros atrasados no servidor (%)", 3), ("render_reancorados", "Reancoragens do envio", 1),
            ("render_idade_p95_ms", "Idade do quadro, p95 (ms)", 1), ("render_enviados", "Quadros enviados", 0)]
    corpo = []
    for col, rotulo, casas in rend:
        corpo.append([rotulo] + [a8.celula(f9.loc[f9["combinacao"] == c, col], casas, (f9["combinacao"] == c).sum())
                                 if col in f9 else "—" for c in combinacoes])
    if "render_enviados" in f9:                                     # o controle sintético não tem render_timeseries.csv
        tabelas.append(("render", f"O render nas execuções ({ROTULO})", "Média ± IC de 95%; contadores do photon_server no "
                        "Prometheus (render_timeseries.csv), do começo ao fim de cada execução.",
                        pd.DataFrame(corpo, columns=["Métrica"] + [" ".join(a8.COMBINACOES[c]) for c in combinacoes])))

    for nome, titulo, nota, t in tabelas:
        t.to_csv(SAIDA / f"tabela_{nome}.csv", index=False)
        texto += [f"## {titulo}", "", nota, "", a8.markdown(t), ""]
    (SAIDA / "tabelas.md").write_text("\n".join(texto))
    print(f"[OK] {SAIDA / 'tabelas.md'} e {len(tabelas)} tabela_*.csv")


if __name__ == "__main__":
    main()
