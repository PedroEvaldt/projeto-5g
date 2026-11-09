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
              cenário com intenções e nenhuma resposta do LLM (servidor do LLM fora do ar);
              uma UPF sem PFCP no TEST_END há mais de 30 s (o Open5GS travou e só volta recriando o pod) e o
              cliente dela sem sessão no fim (regra de 03/11/2026, depois do incidente da hvrl0_02; desde
              04/11/2026 só descarta se o travamento chegou ao cliente, decisão do Pedro);
              render real (vr.render: real no YAML, Fase 9) sem render_timeseries.csv, com render_source em 0 em algum
              instante do teste, com frames_late_total subindo ou com mais de 5% dos quadros sem a pose da sessão
              (o Unity não seguiu o cliente).
  - aviso:   janelas com health=0 (até 10%; ficam fora da conta do SLA); a VR sem janela por mais de 60 s enquanto
              ativa; um vídeo com os quadros parados por 60 s ou mais sem estar pausado; a VR vencendo com lance 0
              (conferência pendente da Fase 6); menos respostas do LLM que intenções; uma UPF sem PFCP por mais
              de 60 s durante o teste, mesmo voltando sozinha; uma UPF presa no fim com a sessão do cliente de pé
              (a sessão aberta passa dados sem o PFCP; a fila recria a UPF antes da execução seguinte).
O PFCP vem do upf_pfcp.txt (scripts/pfcp_upf.py capturar, no fim de cada execução); sem ele, não é conferido.
Só contam os eventos do nosso SMF (# smf= no arquivo): um SMF de fora também se associa à upf4 (03/11/2026).
A sessão do cliente vem das linhas "sessao" do mesmo arquivo; sem elas (arquivos de antes de 04/11/2026 ou erro ao
ler o log do SMF), a UPF presa no fim descarta, como antes.
"""
import re
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
MAX_SEM_POSE = 0.05       # render real: os primeiros quadros de cada janela da VR saem sem a pose dela; acima disso, o Unity não seguiu o cliente
PFCP_PRESA_S = 30         # UPF sem PFCP no TEST_END há mais que isso: presa (as quedas normais voltam antes)
PFCP_QUEDA_S = 60         # UPF sem PFCP por mais que isso durante o teste, mesmo voltando
SESSAO_MIN_S = 10         # sessão que durou menos que isso foi recusada (com a UPF presa, o UE tenta a cada ~7,5 s)


def dnn_da_upf(upf):
    return f"slice{int(upf[3:]):02d}"                  # no configmap do smf, a upfN atende o DNN sliceNN


def sessao_no_fim(evs, t_fim):
    """O cliente de um DNN tinha sessão de pé no TEST_END? evs: [(t, 'criada'|'restaurada'|'removida', IP)] na
    régua do teste, na ordem do log, inclusive depois do fim (para saber quanto durou a última sessão).
    Cada DNN da execução tem um UE só, com uma sessão por vez: uma sessão nova quer dizer que a anterior acabou.
    A sessão de antes da janela (a da VR, cujo UE as execuções não reiniciam) conta como de pé até ser removida."""
    regs = [[None, None, None]]                        # [IP, criada, acabou]; o 1º é a sessão de antes da janela
    for t, tipo, ip in evs:
        abertas = [r for r in regs if r[2] is None]
        mesma = next((r for r in abertas if r[0] == ip), None)
        anterior = next((r for r in abertas if r[0] is None), None)
        if tipo == "removida":
            if mesma or anterior:
                (mesma or anterior)[2] = t
        elif tipo == "restaurada":                     # o SMF reinstala uma sessão que já existia
            if not mesma and anterior:
                anterior[0] = ip
            elif not mesma:
                regs.append([ip, t, None])
        elif not mesma:                                # criada
            for r in abertas:
                r[2] = t
            regs.append([ip, t, None])

    def de_pe(r):
        _, criada, acabou = r
        if criada is not None and criada > t_fim:
            return False
        if acabou is not None and (acabou <= t_fim or (criada is not None and acabou - criada < SESSAO_MIN_S)):
            return False
        return True
    return any(de_pe(r) for r in regs)


def conferir_pfcp(texto, ev, dur):
    """Quedas do PFCP de cada UPF, na régua do teste (segundos desde o TEST_START). Devolve (descartar, aviso)."""
    descartar, aviso = [], []
    dif = re.search(r"diferenca_s=(-?[\d.]+)", texto)
    inicio = re.search(r"inicio=(\S+)", " ".join(ev.loc[ev["Event_Type"] == "TEST_START", "Details"].astype(str)))
    if not (dif and inicio):
        return [], ["upf_pfcp.txt sem a diferença de relógio ou test_events.csv sem o início: PFCP não conferido"]
    t0 = pd.Timestamp(inicio.group(1)).tz_convert("UTC") + pd.Timedelta(seconds=float(dif.group(1)))  # no relógio do cluster
    fim = ev.loc[ev["Event_Type"] == "TEST_END", "Timestamp_Offset_Seconds"]
    t_fim = float(fim.iloc[0]) if len(fim) else dur
    aviso += [f"PFCP: {l.lstrip('# ').strip()}" for l in texto.splitlines() if "erro ao ler o log" in l]
    smf = re.search(r"^# smf=(\S+)", texto, re.M)
    if not smf and re.search(r"^upf\S* \S+ \S+ \S+$", texto, re.M):
        aviso.append("upf_pfcp.txt sem o IP do nosso SMF: contando os eventos de todos os SMFs")
    com_sessoes = re.search(r"^# sessoes_smf=ok", texto, re.M)
    sessoes = {}
    por_upf = {}
    for linha in texto.splitlines():
        partes = linha.split()
        if partes[:1] == ["sessao"] and len(partes) == 6:
            t = (pd.Timestamp(partes[1]) - t0).total_seconds()
            sessoes.setdefault(partes[3], []).append((t, partes[2], partes[4]))
            continue
        if linha.startswith("#") or len(partes) not in (3, 4):
            continue
        if smf and len(partes) == 4 and partes[3] != smf.group(1):
            continue                                    # outro SMF (os arquivos de antes de 04/11 não têm a coluna)
        por_upf.setdefault(partes[0], []).append(((pd.Timestamp(partes[1]) - t0).total_seconds(), partes[2]))
    for upf, evs in sorted(por_upf.items()):
        desde = None                                    # início da queda em curso
        for t, estado in sorted(evs):
            if t > t_fim:
                break
            if estado == "de-associated":
                desde = t if desde is None else desde
            elif desde is not None:
                ini = max(desde, 0.0)
                if t - ini > PFCP_QUEDA_S:
                    aviso.append(f"{upf} sem PFCP por {t - ini:.0f} s (T+{ini:.0f} s a T+{t:.0f} s)")
                desde = None
        if desde is not None and t_fim - desde > PFCP_PRESA_S:
            queda = f"{upf} sem PFCP de T+{max(desde, 0.0):.0f} s até o fim do teste"
            if not com_sessoes:                         # arquivo antigo ou erro ao ler o log do SMF: a regra antiga
                descartar.append(f"{queda} (Open5GS preso)")
            elif sessao_no_fim(sessoes.get(dnn_da_upf(upf), []), t_fim):
                aviso.append(f"{queda} (a sessão do cliente seguiu de pé)")
            else:
                descartar.append(f"{queda}, e o cliente dela sem sessão no fim (Open5GS preso)")
    return descartar, aviso


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

    # Render real (Fase 9): só nos YAMLs com vr.render: real
    if (cfg.get("vr") or {}).get("render") == "real":
        render_arq = pasta / "render_timeseries.csv"
        if not render_arq.exists():
            descartar.append("sem render_timeseries.csv (métricas do render real)")
        else:
            r = pd.read_csv(render_arq)
            if (r["Render_Source"] < 1).any():
                descartar.append("render real: render_source em 0 durante o teste (o Unity parou)")
            atrasados = r["Frames_Late_Total"].max() - r["Frames_Late_Total"].min()
            if atrasados > 0:
                descartar.append(f"render real: {atrasados:.0f} quadros atrasados no servidor")
            enviados = r["Frames_Sent_Total"].max() - r["Frames_Sent_Total"].min()
            sem_pose = r["Frames_Pose_Fallback_Total"].max() - r["Frames_Pose_Fallback_Total"].min()
            if enviados > 0 and sem_pose / enviados > MAX_SEM_POSE:
                descartar.append(f"render real: {100 * sem_pose / enviados:.1f}% dos quadros sem a pose da sessão (o Unity não seguiu o cliente)")

    # LLM: só nos cenários com intenções
    intencoes =sum(1 for e in cfg.get("events", []) if e.get("type") == "priority_change")
    if intencoes:
        llm_arq = pasta / "llm_decisoes.csv"
        respostas = pd.read_csv(llm_arq)["Timestamp"].nunique() if llm_arq.exists() else 0
        if respostas == 0:
            descartar.append(f"nenhuma resposta do LLM para {intencoes} intenções")
        elif respostas < intencoes:
            aviso.append(f"LLM respondeu {respostas} de {intencoes} intenções")

    # PFCP das UPFs: as execuções de antes de 03/11/2026 não têm o arquivo
    pfcp_arq = pasta / "upf_pfcp.txt"
    if pfcp_arq.exists():
        d, a = conferir_pfcp(pfcp_arq.read_text(), ev, dur)
        descartar += d
        aviso += a
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
