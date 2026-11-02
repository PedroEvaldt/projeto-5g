"""
Confere uma execução terminada (Fase 8) e diz se ela vale: as regras de descarte, escritas antes de olhar os resultados.

Uso (de metricas_scripts/):
    python3 conferir_execucao.py resultados/hvr_l2/hvrl2_07

Grava <pasta>/veredito.txt, com uma linha de veredito e, abaixo, um item por achado:
    ok                         a execução vale
    aviso: <motivos>           vale, mas tem algo para olhar (não é descartada)
    descartar: <motivos>       falha do testbed ou do instrumento: a execução não entra na análise
Sai com 0 em ok/aviso e 1 em descartar.

O que descarta é só falha de infraestrutura, nunca o resultado da configuração. A VR travar, sair do SLA ou ficar
sem janelas na L0 é resultado, não falha: vira aviso. As regras:
  - descartar: sem TEST_END, TEST_ERROR ou um evento *_FAIL/*_ERROR/*_FAILED (o roteiro não rodou inteiro);
              um cliente de vídeo que nunca tocou ou com menos de 80% das amostras (coletor ou cliente morto);
              leiloeiro com menos de 80% das rodadas esperadas (agente caiu);
              sem qoe_vr_timeseries.csv, ou mais de 10% das janelas da VR com health=0 (instrumento da VR falhou);
              cenário com intenções e nenhuma resposta do LLM (servidor do LLM fora do ar).
  - aviso:    janelas com health=0 (até 10%; ficam fora da conta do SLA); a VR sem janela por mais de 60 s enquanto
              ativa; um vídeo com os quadros parados por 60 s ou mais sem estar pausado; a VR vencendo com lance 0
              (conferência pendente da Fase 6); menos respostas do LLM que intenções.
"""
import sys
from pathlib import Path

import pandas as pd
import yaml

AMOSTRA_S = 5             # período do coletor de QoE do vídeo
ATRASO_COLETOR_S = 40     # o run_experiment_tmux.sh sobe o coletor de QoE 40 s depois dos eventos
RODADA_S = 30             # período do leilão
MIN_FRACAO = 0.8          # amostras e rodadas: menos que isso é um processo que caiu
MAX_HEALTH0 = 0.10        # fração de janelas da VR com health=0 acima da qual o instrumento não vale
BURACO_VR_S = 60          # VR ativa sem janela por mais que isso
VIDEO_PARADO_S = 60       # quadros sem mudar, com o vídeo tocando, por mais que isso


def conferir(pasta):
    descartar, aviso = [], []
    cfg = yaml.safe_load(open(pasta / "config.yaml"))
    dur = cfg["test_metadata"]["duration_seconds"]
    fatias = cfg["slices"]
    videos = [n for n, s in fatias.items() if s.get("tipo", "video") != "vr"]
    vr = next((n for n, s in fatias.items() if s.get("tipo") == "vr"), None)

    # Roteiro de eventos
    ev_arq = pasta / "test_events.csv"
    if not ev_arq.exists():
        return ["sem test_events.csv"], aviso
    ev = pd.read_csv(ev_arq)
    tipos = ev["Event_Type"].astype(str)
    if "TEST_END" not in set(tipos):
        descartar.append("sem TEST_END (o roteiro não terminou)")
    falhas = sorted(set(t for t in tipos if t == "TEST_ERROR" or t.endswith(("_FAIL", "_ERROR", "_FAILED"))))
    if falhas:
        descartar.append("eventos com falha: " + ", ".join(falhas))

    # Vídeo
    qoe_arq = pasta / "qoe_timeseries.csv"
    if not qoe_arq.exists():
        descartar.append("sem qoe_timeseries.csv")
    else:
        qoe = pd.read_csv(qoe_arq)
        esperadas = (dur - ATRASO_COLETOR_S) / AMOSTRA_S
        for f in videos:
            q = qoe[qoe["Slice"] == f]
            if len(q) < MIN_FRACAO * esperadas:
                descartar.append(f"vídeo {f}: {len(q)} amostras de ~{esperadas:.0f}")
                continue
            if not (pd.to_numeric(q["Bitrate_kbps"], errors="coerce") > 0).any():
                descartar.append(f"vídeo {f} nunca tocou (bitrate 0 o teste inteiro)")
                continue
            # Quadros parados com o vídeo tocando: o maior trecho seguido sem mudança, fora das pausas
            tocando = q[q["Status"] != "PAUSED"]
            mudou = tocando["Frames_Total"].diff().fillna(1) != 0
            trecho = (~mudou).groupby(mudou.cumsum()).sum().max() * AMOSTRA_S
            if trecho >= VIDEO_PARADO_S:
                aviso.append(f"vídeo {f} com os quadros parados por {trecho:.0f} s")

    # Leilão
    leilao_arq = pasta / "auction_history.csv"
    if not leilao_arq.exists():
        descartar.append("sem auction_history.csv")
    else:
        leilao = pd.read_csv(leilao_arq)
        rodadas = leilao["Auction_ID"].nunique()
        if rodadas < MIN_FRACAO * dur / RODADA_S:
            descartar.append(f"leilão com {rodadas} rodadas de ~{dur // RODADA_S}")
        if vr:
            vence0 = leilao[(leilao["Agent"] == f"{vr}_slice") & (leilao["Result"] == "WINNER")
                            & (pd.to_numeric(leilao["Bid_Value"], errors="coerce") == 0)]
            if len(vence0):
                aviso.append(f"a VR venceu {len(vence0)} rodada(s) com lance 0")

    # VR
    if vr:
        vr_arq = pasta / "qoe_vr_timeseries.csv"
        if not vr_arq.exists():
            descartar.append("sem qoe_vr_timeseries.csv (coletor da VR)")
        else:
            j = pd.read_csv(vr_arq)
            janelas = j[~j["Window_Type"].isin(["pausa", "retomada"])]
            h0 = int((pd.to_numeric(janelas["Health"], errors="coerce") == 0).sum())
            if len(janelas) and h0 / len(janelas) > MAX_HEALTH0:
                descartar.append(f"VR: {h0} de {len(janelas)} janelas com health=0")
            elif h0:
                aviso.append(f"VR: {h0} de {len(janelas)} janelas com health=0 (fora da conta)")
            # Buracos com a VR ativa: entre uma retomada e a pausa seguinte (ou o fim)
            ativa, ultimo, maior = False, None, 0.0
            for _, r in j.sort_values("Timestamp_Offset_Seconds").iterrows():
                t = r["Timestamp_Offset_Seconds"]
                if r["Window_Type"] == "retomada":
                    ativa, ultimo = True, t
                elif r["Window_Type"] == "pausa":
                    if ativa and ultimo is not None:
                        maior = max(maior, t - ultimo)
                    ativa = False
                elif ativa:
                    maior = max(maior, t - ultimo)
                    ultimo = t
            if ativa and ultimo is not None:
                maior = max(maior, dur - ultimo)
            if maior > BURACO_VR_S:
                aviso.append(f"VR ativa sem janela por {maior:.0f} s")

    # LLM: só nos cenários com intenções
    intencoes = sum(1 for e in cfg.get("events", []) if e.get("type") == "priority_change")
    if intencoes:
        llm_arq = pasta / "llm_decisoes.csv"
        respostas = pd.read_csv(llm_arq)["Timestamp"].nunique() if llm_arq.exists() else 0
        if respostas == 0:
            descartar.append(f"nenhuma resposta do LLM para {intencoes} intenções")
        elif respostas < intencoes:
            aviso.append(f"LLM respondeu {respostas} de {intencoes} intenções")
    return descartar, aviso


def main():
    if len(sys.argv) != 2:
        sys.exit(f"uso: {sys.argv[0]} <pasta da execução>")
    pasta = Path(sys.argv[1])
    descartar, aviso = conferir(pasta)
    if descartar:
        linha = "descartar: " + "; ".join(descartar)
    elif aviso:
        linha = "aviso: " + "; ".join(aviso)
    else:
        linha = "ok"
    (pasta / "veredito.txt").write_text(linha + "\n" + "".join(f"  - {m}\n" for m in descartar + aviso))
    print(f"[VEREDITO] {linha}")
    sys.exit(1 if descartar else 0)


if __name__ == "__main__":
    main()
