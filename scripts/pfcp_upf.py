"""
Associação PFCP das UPFs com o SMF (Fase 8). Uma UPF cortada para o mínimo do leilão com pacotes na fila perde a
associação; quase sempre ela volta em segundos, mas às vezes o Open5GS fica preso: a UPF desassociada e o SMF
achando que não, até o pod ser recriado (docs/experimentos.md, incidente de 02/11/2026). Do log da UPF só contam
duas linhas: "PFCP associated" e "PFCP de-associated" (os heartbeats que dão certo não aparecem).

Só vale o nosso SMF (o pod app=smf, que atende a upf1–7). Desde 03/11/2026, um SMF de fora do pfsevaldt
(10.251.46.199) também se associa à upf4 e cai e volta sozinho; contá-lo esconderia uma queda real ou inventaria uma.
Os eventos dele ficam no upf_pfcp.txt, mas não decidem nada.

Recriar a UPF não basta para a VR: o UE dela (que as execuções não reiniciam) fica com o túnel de pé e a sessão
numa UPF que não existe mais (ensaio de 03/11/2026: loss 100% com health=1). Por isso o túnel é testado com ping
até o vr-app-01 e, morto, o UE é reiniciado pelo procedimento do CLAUDE.md.

Uso (da raiz do repositório):
    venv/bin/python scripts/pfcp_upf.py estado <yaml>             # cada UPF do YAML: associada, voltando ou presa
    venv/bin/python scripts/pfcp_upf.py capturar <pasta>          # grava <pasta>/upf_pfcp.txt (run_experiment_tmux.sh)
    venv/bin/python scripts/pfcp_upf.py reparar <upfN|udm> <yaml> # recria o pod (a fila da Fase 8 faz isso sozinha)
    venv/bin/python scripts/pfcp_upf.py tunel <ue>                # o túnel do UE leva até o vr-app-01?
    venv/bin/python scripts/pfcp_upf.py reiniciar-ue <ue>         # recria o UE de VR (delete + vrclients-script.sh)

Uma UPF presa nem sempre estraga a execução: a sessão já aberta continua passando dados (03/11/2026, upf2 presa e o
silver tocando). Por isso o arquivo também traz as sessões que o nosso SMF criou, restaurou e removeu nos DNNs da
execução (a upfN atende o DNN sliceNN), para o verificador saber se o cliente da UPF presa ainda tinha sessão no fim.

O upf_pfcp.txt (lido pelo conferir_execucao.py) tem os eventos no relógio do cluster, com o SMF de cada um, a
diferença de relógio para esta máquina e o IP do nosso SMF (os arquivos de antes de 04/11/2026 não têm o SMF nem as
sessões):
    # diferenca_s=189.885 erro_s=0.117
    # smf=10.251.46.210
    # sessoes_smf=ok
    upf4 2026-11-02T20:41:57.371000Z de-associated 10.251.46.210
    sessao 2026-11-02T20:41:57.512000Z removida slice04 172.20.21.40 999700000000007
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
SMF = "smf"                 # o nosso SMF (rótulo app=smf); o smf2 só atende a upf8–10, que as execuções não usam

ANSI = re.compile(r"\x1b\[[0-9;]*m")
CARIMBO = r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?)Z "
EVENTO = re.compile(CARIMBO + r".*PFCP (associated|de-associated) \[([\d.]+)\]")
# No log do SMF: "UE SUPI[...]" sai na criação (npcf-handler.c) e na restauração (pfcp-sm.c); a remoção, do context.c
SESSAO = re.compile(CARIMBO + r".*UE SUPI\[imsi-(\d+)\] DNN\[(\w+)\] IPv4\[([\d.]+)\].*/src/smf/([\w-]+)\.c:")
REMOVIDA = re.compile(CARIMBO + r".*Removed Session: UE IMSI:\[imsi-(\d+)\] DNN:\[(\w+):\d+\] IPv4:\[([\d.]+)\]")


def hora(carimbo):
    """Carimbo do kubectl --timestamps (sem o Z), com ou sem fração, em UTC."""
    base, _, frac = carimbo.partition(".")
    t = datetime.strptime(base, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    return t + timedelta(microseconds=int(frac[:6].ljust(6, "0")))


def eventos(texto, smf=None):
    """[(hora UTC, 'associated'|'de-associated', IP do SMF)] das linhas do `kubectl logs --timestamps` de uma UPF.
    Com smf, só os eventos daquele SMF."""
    saida = []
    for linha in texto.splitlines():
        m = EVENTO.match(ANSI.sub("", linha))
        if m and (smf is None or m.group(3) == smf):
            saida.append((hora(m.group(1)), m.group(2), m.group(3)))
    return saida


def dnn_da_upf(upf):
    return f"slice{int(upf[3:]):02d}"                  # no configmap do smf, a upfN atende o DNN sliceNN


def sessoes(texto, dnns=None):
    """[(hora UTC, 'criada'|'restaurada'|'removida', DNN, IP do UE, IMSI)] do log do SMF, na ordem do log."""
    saida = []
    for linha in texto.splitlines():
        linha = ANSI.sub("", linha)
        if m := SESSAO.match(linha):
            carimbo, imsi, dnn, ip, fonte = m.groups()
            tipo = "restaurada" if fonte == "pfcp-sm" else "criada"
        elif m := REMOVIDA.match(linha):
            carimbo, imsi, dnn, ip = m.groups()
            tipo = "removida"
        else:
            continue
        if dnns is None or dnn in dnns:
            saida.append((hora(carimbo), tipo, dnn, ip, imsi))
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


def ip_smf():
    """IP do pod do nosso SMF: é o endereço que aparece no log da UPF (o SMF chega à UPF pelo Service dela)."""
    r = kubectl("get", "pod", "-l", f"app={SMF}", "-o", "jsonpath={.items[*].status.podIP}")
    ips = r.stdout.split()
    if r.returncode != 0 or len(ips) != 1:
        raise RuntimeError(f"IP do pod {SMF} não encontrado ou ambíguo: {ips} {r.stderr.strip()}")
    return ips[0]


def presa(upf, smf=None):
    """'' se a UPF está associada ao nosso SMF (ou voltou em até PRESA_S); senão, a hora (cluster) da queda."""
    smf = smf or ip_smf()
    evs = eventos(ler_log(upf), smf)
    if not evs or evs[-1][1] == "associated":
        return ""
    desde = evs[-1][0]
    fim = time.monotonic() + PRESA_S
    while time.monotonic() < fim:
        time.sleep(5)
        evs = eventos(ler_log(upf), smf)
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
    try:
        linhas.append(f"# smf={ip_smf()}")
    except Exception as e:
        linhas.append(f"# erro ao ler o IP do smf: {e}")
    for upf in upfs:
        try:
            linhas += [f"{upf} {t:%Y-%m-%dT%H:%M:%S.%f}Z {estado} {par}"
                       for t, estado, par in eventos(ler_log(upf, desde_s))]
        except Exception as e:
            linhas.append(f"# {upf}: erro ao ler o log: {e}")
    try:
        sess = [f"sessao {t:%Y-%m-%dT%H:%M:%S.%f}Z {tipo} {dnn} {ip} {imsi}"
                for t, tipo, dnn, ip, imsi in sessoes(ler_log(SMF, desde_s), {dnn_da_upf(u) for u in upfs})]
        linhas += ["# sessoes_smf=ok"] + sess
    except Exception as e:
        sess = []
        linhas.append(f"# {SMF}: erro ao ler o log: {e}")
    (pasta / "upf_pfcp.txt").write_text("\n".join(linhas) + "\n")
    n_pfcp = sum(1 for l in linhas if l.startswith("upf"))
    print(f"[PFCP] {pasta / 'upf_pfcp.txt'}: {n_pfcp} eventos de {', '.join(upfs)} e {len(sess)} de sessão do {SMF}")


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
    smf = ip_smf()
    fim = time.monotonic() + VOLTA_S
    while time.monotonic() < fim:
        evs = eventos(ler_log(alvo), smf)
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
