#!/usr/bin/env python3
"""
Fase 4: coletor da VR. Lê as janelas que o exporter do ue-vr-07 escreveu no log (linhas [VR]) durante uma
execução e grava o qoe_vr_timeseries.csv na pasta dela: uma linha por janela, com a coluna SLA_OK.

De onde vêm as linhas:
  - <pasta>/vr_log.txt, se existir: o log bruto copiado durante a execução, com
      kubectl logs -f --timestamps --since=1s deploy/ue-vr-07 -c vr-client -n pfsevaldt > <pasta>/vr_log.txt
  - senão, o kubectl logs do pod, a partir do início da execução. Com a execução terminada, o que vier é
    salvo em vr_log.txt, para a pasta poder ser reanalisada depois que o pod sumir.

A régua do tempo é a do test_events.csv: segundos desde o TEST_START. O log traz a hora do relógio do nó do
pod (blacksabbath), que difere em minutos do da ledzeppelin, onde roda o deploy_test.py. O coletor mede a
diferença com um kubectl exec no pod e a guarda em <pasta>/vr_relogio.txt; numa reanálise, usa a guardada.

As marcas [COMANDO] pausa/retomada entram como linhas sem métricas. Assim, o tempo da retomada até a 1ª
janela no SLA pode ser medido todo no relógio do pod, sem depender da conversão.

SLA por janela, com os números do vr/calibracao.yaml (a mesma regra do vr/graficos_calibracao.py):
SLA_OK = 1 se net_p95 ≤ net_ref + folga, perda ≤ máx, continuidade ≥ mín e vazão ≥ mín; 0 se falhar algum
critério ou se a janela não tiver quadros; vazio com health=0 (falha do instrumento, não entra na conta).

Uso (de dentro de metricas_scripts/, depois da execução):
  ../venv/bin/python analise_metricas_vr.py resultados/slices_hierarquicos/exec01/
"""

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import yaml

NAMESPACE = "pfsevaldt"
CALIBRACAO = Path(__file__).resolve().parent.parent / "vr" / "calibracao.yaml"
LOG = "vr_log.txt"
RELOGIO = "vr_relogio.txt"
SAIDA = "qoe_vr_timeseries.csv"
MARGEM_S = 60                                              # o kubectl logs começa um pouco antes do início
CAMPO = re.compile(r'(\w+)=("[^"]*"|\S+)')                 # chave=valor da linha [VR] (o mesmo do vr/calibrar.py)
MARCAS = ("pausa", "retomada")

# Campo da linha [VR] -> coluna do CSV
CAMPOS = {"m2p_p95_ms": "M2P_p95_ms", "net_p95_ms": "Net_p95_ms", "hold_p95_ms": "Hold_p95_ms",
          "loss_pct": "Loss_pct", "cont_pct": "Continuity_pct", "thr_mbps": "Throughput_Mbps", "health": "Health"}
COLUNAS = ["Timestamp_Offset_Seconds", "Timestamp_Pod", "Pod_Name", "Slice", "Window_Type",
           *CAMPOS.values(), "SLA_OK"]


def kubectl(*args):
    return subprocess.run(["kubectl", "-n", NAMESPACE, *args], capture_output=True, text=True,
                          timeout=60, check=True).stdout


def motivo(e):
    """A última linha do erro do kubectl, em vez do comando inteiro."""
    return (getattr(e, "stderr", None) or str(e)).strip().splitlines()[-1]


def ler_execucao(pasta, inicio_manual):
    """Hora real do TEST_START (em UTC) e segundos até o fim do teste (None se o fim não foi registrado)."""
    ev = pd.read_csv(pasta / "test_events.csv")
    if inicio_manual:
        inicio = pd.Timestamp(inicio_manual)
    else:
        start = ev.loc[ev["Event_Type"] == "TEST_START", "Details"]
        m = re.search(r"inicio=(\S+)", str(start.iloc[0])) if len(start) else None
        if not m:
            sys.exit("[ERRO] O test_events.csv não tem a hora do início (execução de antes da Fase 4). "
                     "Passe --inicio com a hora e o fuso, ex.: --inicio 2026-10-29T14:00:00-03:00")
        inicio = pd.Timestamp(m.group(1))
    if inicio.tzinfo is None:
        sys.exit("[ERRO] A hora do início precisa do fuso, ex.: 2026-10-29T14:00:00-03:00")
    fim = ev.loc[ev["Event_Type"].isin(["TEST_END", "TEST_INTERRUPTED", "TEST_ERROR"]), "Timestamp_Offset_Seconds"]
    return inicio.tz_convert("UTC"), (float(fim.iloc[0]) if len(fim) else None)


def medir_relogio(pod):
    """Relógio do pod − relógio local, em s. Três tentativas; vale a de ida e volta mais curta, cujo erro é a metade dela."""
    melhor = None
    for _ in range(3):
        t0 = time.time()
        remoto = float(kubectl("exec", f"deploy/{pod}", "-c", "vr-client", "--",
                               "python3", "-c", "import time; print(time.time())"))
        t1 = time.time()
        if melhor is None or (t1 - t0) / 2 < melhor[1]:
            melhor = (remoto - (t0 + t1) / 2, (t1 - t0) / 2)
    return melhor


def diferenca_relogio(pasta, pod):
    """A diferença guardada na pasta ou, na primeira análise, uma medida agora (e guardada)."""
    arq = pasta / RELOGIO
    if arq.exists():
        d = dict(CAMPO.findall(arq.read_text()))
        print(f"[RELOGIO] diferença guardada: pod {float(d['diferenca_s']):+.2f} s (± {d['erro_s']} s), "
              f"medida em {d['medido_em']}")
        return float(d["diferenca_s"])
    try:
        dif, erro = medir_relogio(pod)
    except (subprocess.SubprocessError, OSError, ValueError) as e:
        sys.exit(f"[ERRO] Não deu para medir o relógio do pod {pod} ({motivo(e)}). Sem a diferença, as janelas não "
                 f"entram na régua dos eventos. O pod está de pé? Na reanálise, o {RELOGIO} da pasta evita a medida.")
    agora = datetime.now().astimezone().isoformat(timespec="seconds")
    arq.write_text(f"diferenca_s={dif:.3f} erro_s={erro:.3f} medido_em={agora}\n")
    print(f"[RELOGIO] medida agora: pod {dif:+.2f} s (± {erro:.2f} s) em relação a esta máquina; guardada em {arq}")
    return dif


def ler_log(pasta, pod, inicio_pod, fim):
    """O texto do log: do vr_log.txt ou do kubectl logs (salvo em vr_log.txt se a execução já terminou)."""
    arq = pasta / LOG
    if arq.exists():
        return arq.read_text(), str(arq)
    desde = (inicio_pod - pd.Timedelta(seconds=MARGEM_S)).strftime("%Y-%m-%dT%H:%M:%SZ")
    texto = kubectl("logs", f"deploy/{pod}", "-c", "vr-client", "--timestamps", f"--since-time={desde}")
    if fim is None:                                        # execução em andamento: um vr_log.txt parcial enganaria a reanálise
        return texto, "kubectl logs (não salvo: o test_events.csv ainda não tem o fim do teste)"
    arq.write_text(texto)
    return texto, f"kubectl logs (salvo em {arq})"


def parsear(texto):
    """Uma linha por janela [VR] ou marca de pausa/retomada, com a hora do pod que o --timestamps põe na frente."""
    linhas = []
    for linha in texto.splitlines():
        hora, _, msg = linha.partition(" ")
        if "[COMANDO] " in msg:
            marca = msg.split("[COMANDO] ", 1)[1].strip()
            if marca in MARCAS:
                linhas.append({"Timestamp_Pod": hora, "Window_Type": marca})
        elif "] [VR] " in msg:
            campos = msg.split("] [VR] ", 1)[1]
            primeiro = campos.split(" ", 1)[0]
            tipo = "normal" if "=" in primeiro else primeiro   # sem_quadros, falha_cliente, servidor_fora_do_ar
            valores = dict(CAMPO.findall(campos))
            linhas.append({"Timestamp_Pod": hora, "Window_Type": tipo,
                           **{col: valores.get(campo) for campo, col in CAMPOS.items()}})
    return pd.DataFrame(linhas, columns=["Timestamp_Pod", "Window_Type", *CAMPOS.values()])


def marcar_sla(df, sla):
    """SLA_OK por janela: 1, 0 ou vazio (health=0 e marcas)."""
    for col in CAMPOS.values():
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["Health"] = df["Health"].astype("Int64")            # 1/0, vazio nas marcas (sem isso, vira 1.0)
    ok =((df["Window_Type"] == "normal")
          & (df["Net_p95_ms"] <= sla["net_ref_ms"] + sla["folga_net_ms"])
          & (df["Loss_pct"] <= sla["perda_max_pct"])
          & (df["Continuity_pct"] >= sla["cont_min_pct"])
          & (df["Throughput_Mbps"] >= sla["vazao_min_mbps"]))
    conta = ~df["Window_Type"].isin(MARCAS) & (df["Health"] == 1)
    df["SLA_OK"] = pd.Series(pd.NA, index=df.index, dtype="Int64")
    df.loc[conta, "SLA_OK"] = ok[conta].astype(int)


def checar_reinicio(pod, inicio_pod, fim):
    """Avisa se o container vr-client começou no meio da execução: o log de antes disso pode faltar."""
    try:
        itens = json.loads(kubectl("get", "pod", "-l", f"app={pod}", "-o", "json"))["items"]
    except (subprocess.SubprocessError, OSError, ValueError) as e:
        print(f"[AVISO] Não deu para conferir se o vr-client reiniciou: {motivo(e)}")
        return
    for item in itens:
        for cs in item["status"].get("containerStatuses", []):
            comecou = cs["state"].get("running", {}).get("startedAt")
            if cs["name"] != "vr-client" or not comecou:
                continue
            seg = (pd.Timestamp(comecou) - inicio_pod).total_seconds()
            if 0 < seg and (fim is None or seg < fim):
                print(f"[AVISO] O vr-client de {item['metadata']['name']} começou {seg:.0f} s depois do início "
                      f"(reinícios: {cs['restartCount']}): as janelas de antes disso podem faltar.")


def main():
    ap = argparse.ArgumentParser(description="Fase 4: janelas da VR de uma execução, com SLA_OK")
    ap.add_argument("pasta", help="pasta da execução (a do test_events.csv)")
    ap.add_argument("--pod", default="ue-vr-07", help="Deployment do UE de VR")
    ap.add_argument("--fatia", default="vr", help="nome da fatia de VR na coluna Slice")
    ap.add_argument("--inicio", help="hora do início com fuso, para execuções sem ela no test_events.csv")
    args = ap.parse_args()
    pasta = Path(args.pasta)

    inicio, fim = ler_execucao(pasta, args.inicio)
    with open(CALIBRACAO) as f:
        sla = yaml.safe_load(f)["sla"]
    dif = diferenca_relogio(pasta, args.pod)
    inicio_pod = inicio + pd.Timedelta(seconds=dif)       # o TEST_START no relógio do pod
    texto, origem = ler_log(pasta, args.pod, inicio_pod, fim)

    df = parsear(texto)
    df["Timestamp_Offset_Seconds"] = (pd.to_datetime(df["Timestamp_Pod"], utc=True) - inicio_pod).dt.total_seconds().round(1)
    dentro = df["Timestamp_Offset_Seconds"] >= 0
    if fim is not None:
        dentro &= df["Timestamp_Offset_Seconds"] <= fim
    df = df[dentro].copy()
    if df.empty:
        sys.exit(f"[ERRO] Nenhuma janela da VR entre o início e o fim da execução ({origem}).")
    df["Pod_Name"] = args.pod
    df["Slice"] = args.fatia
    marcar_sla(df, sla)
    df[COLUNAS].to_csv(pasta / SAIDA, index=False)

    janelas = df[~df["Window_Type"].isin(MARCAS)]
    contadas = janelas["SLA_OK"].dropna()
    tipos = ", ".join(f"{n} {t}" for t, n in janelas["Window_Type"].value_counts().items())
    print(f"[LOG] {origem}")
    print(f"[VR] {len(janelas)} janelas ({tipos}) e {len(df) - len(janelas)} marcas de pausa/retomada, "
          f"de {df['Timestamp_Offset_Seconds'].min():.0f} a {df['Timestamp_Offset_Seconds'].max():.0f} s")
    if len(contadas):
        print(f"[SLA] {int(contadas.sum())} de {len(contadas)} janelas no SLA ({100 * contadas.mean():.1f}%); "
              f"fora da conta (health=0): {len(janelas) - len(contadas)}")
    checar_reinicio(args.pod, inicio_pod, fim)
    print(f"[CSV] {pasta / SAIDA}")


if __name__ == "__main__":
    main()
