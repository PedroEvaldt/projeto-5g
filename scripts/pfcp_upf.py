"""
Associação PFCP das UPFs com o SMF (Fase 8). Uma UPF cortada para o mínimo do leilão com pacotes na fila perde a
associação; quase sempre ela volta em segundos, mas às vezes o Open5GS fica preso: a UPF desassociada e o SMF
achando que não, até o pod ser recriado (docs/experimentos.md, incidente de 02/11/2026). Do log da UPF só contam
duas linhas: "PFCP associated" e "PFCP de-associated" (os heartbeats que dão certo não aparecem).

Recriar a UPF não basta para a VR: o UE dela (que as execuções não reiniciam) fica com o túnel de pé e a sessão
numa UPF que não existe mais (ensaio de 03/11/2026: loss 100% com health=1). Por isso o túnel é testado com ping
até o vr-app-01 e, morto, o UE é reiniciado pelo procedimento do CLAUDE.md.

Uso (da raiz do repositório):
    venv/bin/python scripts/pfcp_upf.py estado <yaml>             # cada UPF do YAML: associada, voltando ou presa
    venv/bin/python scripts/pfcp_upf.py capturar <pasta>          # grava <pasta>/upf_pfcp.txt (run_experiment_tmux.sh)
    venv/bin/python scripts/pfcp_upf.py reparar <upfN|udm> <yaml> # recria o pod (a fila da Fase 8 faz isso sozinha)
    venv/bin/python scripts/pfcp_upf.py tunel <ue>                # o túnel do UE leva até o vr-app-01?
    venv/bin/python scripts/pfcp_upf.py reiniciar-ue <ue>         # recria o UE de VR (delete + vrclients-script.sh)

O upf_pfcp.txt (lido pelo conferir_execucao.py) tem os eventos no relógio do cluster e a diferença para esta máquina:
    # diferenca_s=189.885 erro_s=0.117
    upf4 2026-11-02T20:41:57.371000Z de-associated
"""
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

NS = "pfsevaldt"
REPO = Path(__file__).resolve().parent.parent
PY = str(REPO / "venv" / "bin" / "python")
PRESA_S = 30            # sem "associated" por mais que isso depois de um "de-associated": presa (as quedas normais voltam antes)
VOLTA_S = 60            # depois de recriar o pod, o tempo para o SMF reassociar
UDM_CHEIO = "Maximum number of SDM Subscriptions"
VR_SERVIDOR = "vr-app-01"   # o PP_SERVER do vr_exporter.py; o ClusterIP não responde a ping, então vai ao pod

ANSI = re.compile(r"\x1b\[[0-9;]*m")
EVENTO = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?Z .*PFCP (associated|de-associated) ")


def eventos(texto):
    """[(hora UTC, 'associated'|'de-associated')] das linhas do `kubectl logs --timestamps` de uma UPF."""
    saida = []
    for linha in texto.splitlines():
        m = EVENTO.match(ANSI.sub("", linha))
        if m:
            base, frac, estado = m.groups()
            t = datetime.strptime(base, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
            saida.append((t + timedelta(microseconds=int((frac or "0")[:6].ljust(6, "0"))), estado))
    return saida


def upfs_do_yaml(cfg):
    return sorted({s["upf"] for s in cfg["slices"].values() if s.get("upf")})


def kubectl(*args, timeout=60):
    return subprocess.run(["kubectl", "-n", NS, *args], capture_output=True, text=True, timeout=timeout)   # antes do "--" do exec


def ler_log(alvo, desde_s=None):
    r = kubectl("logs", f"deploy/{alvo}", "--timestamps", *([f"--since={int(desde_s)}s"] if desde_s else []))
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or f"kubectl logs {alvo} falhou")
    return r.stdout


def presa(upf):
    """'' se a UPF está associada (ou voltou em até PRESA_S); senão, a hora (cluster) em que ela se desassociou."""
    evs = eventos(ler_log(upf))
    if not evs or evs[-1][1] == "associated":
        return ""
    desde = evs[-1][0]
    fim = time.monotonic() + PRESA_S
    while time.monotonic() < fim:
        time.sleep(5)
        evs = eventos(ler_log(upf))
        if evs and evs[-1][1] == "associated":
            return ""
    return f"{desde:%Y-%m-%d %H:%M:%S}"


def udm_cheio():
    return UDM_CHEIO in ler_log("udm", desde_s=600)


def medir_relogio(alvo):
    """(relógio do cluster - o desta máquina, erro), pelo date de dentro do pod. Os nós do cluster têm o mesmo relógio."""
    t0 = time.time()
    r = kubectl("exec", f"deploy/{alvo}", "--", "date", "+%s.%N", timeout=30)
    t1 = time.time()
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "kubectl exec date falhou")
    return float(r.stdout.strip()) - (t0 + t1) / 2, (t1 - t0) / 2


def capturar(pasta):
    """Grava <pasta>/upf_pfcp.txt com os eventos de PFCP de cada UPF da execução, desde um pouco antes do teste."""
    pasta = Path(pasta)
    upfs = upfs_do_yaml(yaml.safe_load(open(pasta / "config.yaml")))
    desde_s = 3600
    ev = pasta / "test_events.csv"
    if ev.exists():
        m = re.search(r"inicio=([0-9T:.+-]+)", ev.read_text())
        if m:
            desde_s = (datetime.now(timezone.utc) - datetime.fromisoformat(m.group(1))).total_seconds() + 300
    linhas = ["# Eventos de PFCP das UPFs (scripts/pfcp_upf.py capturar), no relógio do cluster"]
    try:
        dif, erro = medir_relogio(upfs[0])
        linhas.append(f"# diferenca_s={dif:.3f} erro_s={erro:.3f}")
    except Exception as e:
        linhas.append(f"# erro ao medir o relógio: {e}")
    for upf in upfs:
        try:
            linhas += [f"{upf} {t:%Y-%m-%dT%H:%M:%S.%f}Z {estado}" for t, estado in eventos(ler_log(upf, desde_s))]
        except Exception as e:
            linhas.append(f"# {upf}: erro ao ler o log: {e}")
    (pasta / "upf_pfcp.txt").write_text("\n".join(linhas) + "\n")
    print(f"[PFCP] {pasta / 'upf_pfcp.txt'}: {sum(1 for l in linhas if not l.startswith('#'))} eventos de {', '.join(upfs)}")


def reparar(alvo, arq_yaml):
    """Recria o pod do alvo (upfN ou udm). Numa UPF, devolve os limites do início (upf_set_resources.py) e espera
    o SMF reassociar. Devolve (deu certo, o que aconteceu)."""
    t0 = time.monotonic()
    r = kubectl("get", "pod", "-l", f"app={alvo}", "-o", "jsonpath={.items[*].metadata.name}")
    pods = r.stdout.split()
    if r.returncode != 0 or not pods:
        return False, f"pod de {alvo} não encontrado ({r.stderr.strip()})"
    r = kubectl("delete", "pod", *pods, "--wait=true", "--timeout=120s", timeout=150)
    if r.returncode != 0:
        return False, f"kubectl delete pod falhou: {r.stderr.strip()}"
    r = kubectl("wait", "--for=condition=Ready", "pod", "-l", f"app={alvo}", "--timeout=180s", timeout=210)
    if r.returncode != 0:
        return False, f"o pod novo de {alvo} não ficou pronto em 180 s: {r.stderr.strip()}"
    if not alvo.startswith("upf"):
        time.sleep(10)                                  # o UDM novo se registra no NRF
        return True, f"pod recriado em {time.monotonic() - t0:.0f} s"
    # O pod novo nasce com o template (150m, sem a anotação de banda): os mesmos limites do início de cada execução
    subprocess.run([PY, "upf_set_resources.py"], cwd=REPO / "spade_agents", capture_output=True,
                   env={**os.environ, "TEST_FILE": str(arq_yaml)}, timeout=120)
    fim = time.monotonic() + VOLTA_S
    while time.monotonic() < fim:
        evs = eventos(ler_log(alvo))
        if evs and evs[-1][1] == "associated":
            return True, f"pod recriado; PFCP de volta em {time.monotonic() - t0:.0f} s"
        time.sleep(5)
    return False, f"pod recriado, mas o SMF não reassociou em {VOLTA_S} s"


def tunel_ok(ue, espera_s=0):
    """O túnel do UE leva até o servidor da VR (uma resposta basta), tentando por até espera_s segundos."""
    r = kubectl("get", "pod", "-l", f"app={VR_SERVIDOR}", "-o", "jsonpath={.items[0].status.podIP}")
    destino = r.stdout.strip()
    if r.returncode != 0 or not destino:
        raise RuntimeError(f"IP do {VR_SERVIDOR} não encontrado ({r.stderr.strip()})")
    fim = time.monotonic() + espera_s
    while True:
        r = kubectl("exec", f"deploy/{ue}", "-c", "ue", "--", "ping", "-I", "uesimtun0", "-c", "3", "-W", "2", destino,
                    timeout=30)
        if r.returncode == 0:
            return True
        if time.monotonic() >= fim:
            return False
        time.sleep(5)


def reiniciar_ue_vr(ue):
    """Recria o UE de VR: apaga o deployment, espera o pod sumir (o IMSI não pode ficar registrado duas vezes; o
    rollout restart sobe o novo antes) e roda o vrclients-script.sh. Devolve (deu certo, o que aconteceu)."""
    t0 = time.monotonic()
    r = kubectl("delete", "deployment", ue, "--ignore-not-found", timeout=60)
    if r.returncode != 0:
        return False, f"kubectl delete deployment falhou: {r.stderr.strip()}"
    kubectl("wait", "--for=delete", "pod", "-l", f"app={ue}", "--timeout=180s", timeout=210)   # sem pod, já saiu
    if kubectl("get", "pod", "-l", f"app={ue}", "-o", "name").stdout.strip():
        return False, "o pod antigo do UE não sumiu em 180 s"
    r = subprocess.run(["bash", str(REPO / "client_scripts" / "vrclients-script.sh")], cwd=REPO,
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        return False, f"vrclients-script.sh falhou: {r.stderr.strip()[-200:]}"
    r = kubectl("wait", "--for=condition=Ready", "pod", "-l", f"app={ue}", "--timeout=180s", timeout=210)
    if r.returncode != 0:
        return False, f"o pod novo do UE não ficou pronto em 180 s: {r.stderr.strip()}"
    if not tunel_ok(ue, espera_s=120):
        return False, "UE recriado, mas o túnel não chegou ao vr-app-01 em 120 s"
    return True, f"UE recriado; túnel de volta em {time.monotonic() - t0:.0f} s"


if __name__ == "__main__":
    uso = "uso: pfcp_upf.py estado <yaml> | capturar <pasta> | reparar <upfN|udm> <yaml> | tunel <ue> | reiniciar-ue <ue>"
    if len(sys.argv) == 3 and sys.argv[1] == "estado":
        for upf in upfs_do_yaml(yaml.safe_load(open(sys.argv[2]))):
            desde = presa(upf)
            print(f"{upf}: " + (f"PRESA sem PFCP desde {desde} (relógio do cluster)" if desde else "associada"))
        print("udm: " + ("CHEIO (limite de assinaturas SDM)" if udm_cheio() else "ok"))
    elif len(sys.argv) == 3 and sys.argv[1] == "capturar":
        capturar(sys.argv[2])
    elif len(sys.argv) == 4 and sys.argv[1] == "reparar":
        ok, msg = reparar(sys.argv[2], Path(sys.argv[3]).resolve())
        print(f"{sys.argv[2]}: {msg}")
        sys.exit(0 if ok else 1)
    elif len(sys.argv) == 3 and sys.argv[1] == "tunel":
        ok = tunel_ok(sys.argv[2])
        print(f"{sys.argv[2]}: túnel " + ("ok" if ok else "SEM RESPOSTA do vr-app-01"))
        sys.exit(0 if ok else 1)
    elif len(sys.argv) == 3 and sys.argv[1] == "reiniciar-ue":
        ok, msg = reiniciar_ue_vr(sys.argv[2])
        print(f"{sys.argv[2]}: {msg}")
        sys.exit(0 if ok else 1)
    else:
        sys.exit(uso)
