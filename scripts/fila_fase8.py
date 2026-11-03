"""
Fila de execuções da Fase 8: 5 combinações x 30 ciclos, intercaladas. Chamada pelo run_fase8.sh (da raiz):

    ./run_fase8.sh --ciclos 8      # roda os próximos 8 ciclos, num tmux "fase8"
    ./run_fase8.sh --status        # quantas execuções válidas por combinação
    ./run_fase8.sh --seco 2        # mostra o que os próximos 2 ciclos rodariam, sem rodar nada

Cada ciclo roda 1 execução de cada combinação, numa ordem sorteada com o número do ciclo como semente
(a mesma ordem se a fila for retomada). Nome: <prefixo>_<ciclo>, ex. hvrl2_07, na pasta resultados/<combinação>/.

Cada execução é o run_experiment_tmux.sh --completo (conferência dos clientes, execução, pós-execução e o
conferir_execucao.py). Uma tentativa que falha vai para resultados/descartadas/<combinação>/<nome>-tentativaN e
é repetida. A fila para sozinha com 3 tentativas falhando em seguida (o testbed deve estar com problema) ou
quando existir o arquivo resultados/fase8_parar (parar depois da execução atual: touch nele).

Antes de cada tentativa, a fila recria o pod de uma UPF presa sem PFCP ou do UDM cheio de assinaturas
(scripts/pfcp_upf.py; decisão do Pedro, 03/11/2026). Se o conserto falhar, ela para e a recuperação é à mão.

Estado: resultados/fase8_fila.csv, uma linha por tentativa; é ele que diz onde retomar. Log: resultados/fase8_fila.log.
Consertos automáticos: resultados/fase8_reparos.csv.
"""
import csv
import os
import random
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

import pfcp_upf

REPO = Path(__file__).resolve().parent.parent
RES = REPO / "metricas_scripts" / "resultados"
FILA = RES / "fase8_fila.csv"
LOG = RES / "fase8_fila.log"
REPAROS = RES / "fase8_reparos.csv"
PARAR = RES / "fase8_parar"
PY = str(REPO / "venv" / "bin" / "python")
CICLOS = 30
FALHAS_SEGUIDAS = 3     # tentativas que falharam em seguida (de uma execução ou de várias): para a fila
PAUSA_S = int(os.environ.get("FASE8_PAUSA_S", 60))   # entre execuções: o XMPP solta a porta e a upf4 reassocia o PFCP depois do reset
CAMPOS = ["ciclo", "ordem", "combinacao", "nome", "tentativa", "inicio", "fim", "status", "motivo", "commit"]
CAMPOS_REPARO = ["hora", "antes_de", "alvo", "motivo", "deu_certo", "resultado"]
ESPERA_SAIDA_S = 5.0    # vr.espera_saida_s: a saída da VR só libera a upf4 depois disto (incidente de 02/11/2026)

# combinação (= pasta em resultados/), prefixo do nome, YAML e o que o YAML tem de ter (vr.floor, vr.agent, vr.llm)
COMBINACOES = [
    ("hvr_l0", "hvrl0", "tests_configs/test_config_hvr_l0.yaml", (False, "utilizacao", "artigo")),
    ("hvr_l1", "hvrl1", "tests_configs/test_config_hvr.yaml", (True, "utilizacao", "artigo")),
    ("hvr_l2", "hvrl2", "tests_configs/test_config_hvr_l2.yaml", (True, "sla", "artigo")),
    ("scvr_l2", "scvrl2", "tests_configs/test_config_scvr_l2.yaml", (True, "sla", "artigo")),
    ("scvr_l3", "scvrl3", "tests_configs/test_config_scvr_l3.yaml", (True, "sla", "vr")),
]
DRONE_PRIORIDADE = 5.0  # SC-VR (decisão da Fase 7)


def log(msg):
    linha = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(linha, flush=True)
    with open(LOG, "a") as f:
        f.write(linha + "\n")


def ler_fila():
    if not FILA.exists():
        return []
    with open(FILA) as f:
        return list(csv.DictReader(f))


def gravar(linha):
    novo = not FILA.exists()
    with open(FILA, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS)
        if novo:
            w.writeheader()
        w.writerow(linha)


def validas(linhas):
    return {l["nome"] for l in linhas if l["status"] in ("ok", "aviso")}


def ordem_do_ciclo(ciclo):
    ordem = list(COMBINACOES)
    random.Random(ciclo).shuffle(ordem)
    return ordem


def pendentes(linhas):
    """[(ciclo, posição na ordem, combinação)] do que falta, na ordem em que vai rodar."""
    feitas = validas(linhas)
    return [(c, i, comb) for c in range(1, CICLOS + 1)
            for i, comb in enumerate(ordem_do_ciclo(c), 1) if f"{comb[1]}_{c:02d}" not in feitas]


def conferir_preparo():
    """Antes de começar: o código commitado (todas as execuções no mesmo commit) e os YAMLs certos."""
    erros = []
    if subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=REPO,
                      capture_output=True, text=True).stdout.strip():
        erros.append("há mudanças não commitadas nos arquivos do git (git status): commite antes, para todas as "
                     "execuções rodarem no mesmo código")
    for comb, _, arq, (piso, agente, llm) in COMBINACOES:
        c = yaml.safe_load(open(REPO / arq))
        v = c.get("vr") or {}
        tem = (str(v.get("floor", True)).lower() in ("true", "on", "1"), v.get("agent", "utilizacao"), v.get("llm", "artigo"))
        if tem != (piso, agente, llm):
            erros.append(f"{arq}: vr.floor/agent/llm = {tem}, a {comb} pede {(piso, agente, llm)}")
        if float(v.get("espera_saida_s", 0)) != ESPERA_SAIDA_S:
            erros.append(f"{arq}: vr.espera_saida_s = {v.get('espera_saida_s', 0)}, a Fase 8 usa {ESPERA_SAIDA_S:g}")
        if (c.get("bidding") or {}).get("inform_updates_priority", False):
            erros.append(f"{arq}: bidding.inform_updates_priority tem de ficar false na Fase 8")
        if f"/resultados/{comb}/" not in c["monitoring"]["csv_output_dir"]:
            erros.append(f"{arq}: csv_output_dir não aponta para resultados/{comb}/")
        for nome, s in c["slices"].items():
            if comb.startswith("scvr") and s.get("tipo") == "vr" and float(s["initial_priority"]) != DRONE_PRIORIDADE:
                erros.append(f"{arq}: {nome} com prioridade inicial {s['initial_priority']}, a Fase 8 usa {DRONE_PRIORIDADE}")
    # Os clientes de vídeo com RollingUpdate ficam com o IMSI duplicado no restart, e o AMF às vezes perde o UE novo
    videos = sorted({s["client_pod"] for _, _, arq, _ in COMBINACOES
                     for s in yaml.safe_load(open(REPO / arq))["slices"].values() if s.get("tipo", "video") != "vr"})
    r = subprocess.run(["kubectl", "get", "deployment", *videos, "-n", "pfsevaldt", "-o",
                        "jsonpath={range .items[*]}{.metadata.name}={.spec.strategy.type} {end}"],
                       capture_output=True, text=True)
    errados = [d for d in r.stdout.split() if not d.endswith("=Recreate")]
    if r.returncode != 0:
        erros.append(f"não consegui ler os deployments de vídeo: {r.stderr.strip()}")
    elif errados:
        erros.append(f"clientes de vídeo sem strategy Recreate ({' '.join(errados)}): ver docs/experimentos.md")
    sessoes = subprocess.run(["tmux", "ls"], capture_output=True, text=True).stdout
    if any(l.startswith("exp_") for l in sessoes.splitlines()):
        erros.append("há uma execução aberta (tmux ls mostra exp_...): feche-a antes")
    return erros


def commit():
    return subprocess.run(["git", "describe", "--tags", "--always"], cwd=REPO,
                          capture_output=True, text=True).stdout.strip()


def motivo_da_falha(pasta, rc):
    for arq in ("conferencia.txt", "veredito.txt"):
        if (pasta / arq).exists():
            return (pasta / arq).read_text().splitlines()[0].strip()
    nomes = {3: "conferência dos clientes falhou", 4: "não terminou no tempo", 5: "descartada pelo conferir_execucao.py"}
    return nomes.get(rc, f"run_experiment_tmux.sh saiu com {rc} (ver {LOG.name})")


def descartar(pasta, comb, nome, tentativa):
    if not pasta.exists():
        return
    destino = RES / "descartadas" / comb / f"{nome}-tentativa{tentativa}"
    destino.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(pasta), str(destino))
    log(f"   pasta movida para {destino.relative_to(RES)}")


def gravar_reparo(linha):
    novo = not REPAROS.exists()
    with open(REPAROS, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS_REPARO)
        if novo:
            w.writeheader()
        w.writerow(linha)


def consertar_testbed(arq, nome):
    """Antes de uma tentativa: recria a UPF presa sem PFCP e o UDM cheio e, se o túnel da VR não chega ao
    vr-app-01 (o UE dela não é reiniciado pelas execuções), recria o UE. Devolve False se um conserto falhou."""
    cfg = yaml.safe_load(open(REPO / arq))
    ue_vr = next((s["client_pod"] for s in cfg["slices"].values() if s.get("tipo") == "vr"), None)

    def registrar(alvo, motivo, ok, resultado):
        gravar_reparo(dict(hora=f"{datetime.now():%Y-%m-%d %H:%M:%S}", antes_de=nome, alvo=alvo, motivo=motivo,
                           deu_certo="sim" if ok else "não", resultado=resultado))
        log(f"   [REPARO] {alvo}: {resultado}")
        return ok

    try:
        problemas = [(upf, f"sem PFCP desde {desde} (relógio do cluster)")
                     for upf in pfcp_upf.upfs_do_yaml(cfg) if (desde := pfcp_upf.presa(upf))]
        if pfcp_upf.udm_cheio():
            problemas.append(("udm", "limite de assinaturas SDM atingido"))
        for alvo, motivo in problemas:
            log(f"   [REPARO] {alvo}: {motivo}; recriando o pod")
            if not registrar(alvo, motivo, *pfcp_upf.reparar(alvo, REPO / arq)):
                return False
        # O UE costuma refazer a sessão sozinho em segundos (depois de um conserto ou de uma queda curta): 30 s
        if ue_vr and not pfcp_upf.tunel_ok(ue_vr, espera_s=30):
            motivo = "túnel da VR sem resposta do vr-app-01"
            log(f"   [REPARO] {ue_vr}: {motivo}; recriando o UE")
            if not registrar(ue_vr, motivo, *pfcp_upf.reiniciar_ue_vr(ue_vr)):
                return False
    except Exception as e:
        log(f"   [REPARO] falhou ao conferir ou consertar o testbed: {e}")
        return False
    return True


def resetar_upfs(arq):
    """Devolve as UPFs ao estado inicial entre execuções: a upf4 no mínimo do leilão perde o PFCP (Fase 7)."""
    subprocess.run([PY, "upf_set_resources.py"], cwd=REPO / "spade_agents", env={**os.environ, "TEST_FILE": str(REPO / arq)},
                   capture_output=True)


def rodar(ciclo, ordem, comb, cod):
    """Uma tentativa de uma execução. Devolve True se ela ficou válida."""
    nome_comb, prefixo, arq, _ = comb
    nome = f"{prefixo}_{ciclo:02d}"
    pasta = RES / nome_comb / nome
    tentativa = sum(1 for l in ler_fila() if l["nome"] == nome) + 1     # numera as pastas em descartadas/
    if pasta.exists() and any(pasta.iterdir()):
        # Sobra de uma fila interrompida no meio da execução: registrada como tentativa perdida
        descartar(pasta, nome_comb, nome, tentativa)
        gravar(dict(ciclo=ciclo, ordem=ordem, combinacao=nome_comb, nome=nome, tentativa=tentativa, inicio="",
                    fim=f"{datetime.now():%Y-%m-%d %H:%M:%S}", status="falhou",
                    motivo="interrompida (pasta encontrada ao retomar a fila)", commit=cod))
        tentativa += 1
    inicio = f"{datetime.now():%Y-%m-%d %H:%M:%S}"
    log(f"=== ciclo {ciclo}, {ordem}/5: {nome} ({arq}), tentativa {tentativa}")
    with open(LOG, "a") as f:
        rc = subprocess.run(["./run_experiment_tmux.sh", arq, nome, "--completo"], cwd=REPO,
                            stdout=f, stderr=subprocess.STDOUT).returncode
    fim = f"{datetime.now():%Y-%m-%d %H:%M:%S}"
    if rc == 0:
        veredito = (pasta / "veredito.txt").read_text().splitlines()[0].strip()
        status = "aviso" if veredito.startswith("aviso") else "ok"
        motivo = veredito.removeprefix("aviso: ") if status == "aviso" else ""
    else:
        status, motivo = "falhou", motivo_da_falha(pasta, rc)
    gravar(dict(ciclo=ciclo, ordem=ordem, combinacao=nome_comb, nome=nome, tentativa=tentativa,
                inicio=inicio, fim=fim, status=status, motivo=motivo, commit=cod))
    log(f"   {nome}: {status}{(' - ' + motivo) if motivo else ''}")
    resetar_upfs(arq)
    if status != "falhou":
        return True
    descartar(pasta, nome_comb, nome, tentativa)
    return False


def fila(n_ciclos):
    erros = conferir_preparo()
    if erros:
        for e in erros:
            print(f"[ERRO] {e}")
        sys.exit(1)
    RES.mkdir(parents=True, exist_ok=True)
    PARAR.unlink(missing_ok=True)
    cod = commit()
    falta = pendentes(ler_fila())
    ciclos = sorted({c for c, _, _ in falta})[:n_ciclos]
    if not ciclos:
        log("Fase 8 completa: nada a rodar.")
        return
    log(f"Fila começou: ciclos {ciclos[0]} a {ciclos[-1]} ({sum(1 for c, _, _ in falta if c in ciclos)} execuções), código {cod}")
    seguidas = 0
    for ciclo, ordem, comb in [p for p in falta if p[0] in ciclos]:
        if PARAR.exists():
            log(f"Parada pedida ({PARAR.name}); a fila termina aqui. Retome com ./run_fase8.sh --ciclos N")
            PARAR.unlink()
            return
        # Repete até valer; 3 falhas seguidas (desta execução ou de várias) param a fila
        while True:
            if not consertar_testbed(comb[2], f"{comb[1]}_{ciclo:02d}"):
                log("[PAROU] um conserto automático falhou: recupere à mão (docs/experimentos.md) e retome com "
                    "./run_fase8.sh --ciclos N")
                return
            if rodar(ciclo, ordem, comb, cod):
                break
            seguidas += 1
            if seguidas >= FALHAS_SEGUIDAS:
                log(f"[PAROU] {FALHAS_SEGUIDAS} falhas seguidas: o testbed deve estar com problema. Veja o "
                    f"{FILA.name} e as pastas em descartadas/; depois, retome com ./run_fase8.sh --ciclos N")
                return
            log(f"   pausa de {PAUSA_S} s antes de tentar de novo")
            time.sleep(PAUSA_S)
        seguidas = 0
        log(f"   pausa de {PAUSA_S} s")
        time.sleep(PAUSA_S)
    log(f"Fila terminou: ciclos {ciclos[0]} a {ciclos[-1]}. ./run_fase8.sh --status para o resumo")


def status():
    linhas = ler_fila()
    feitas = validas(linhas)
    print(f"Fase 8: {len(feitas)} de {CICLOS * len(COMBINACOES)} execuções válidas\n")
    print(f"{'combinação':<10} {'válidas':>8} {'com aviso':>10} {'tentativas que falharam':>24}")
    for comb, prefixo, _, _ in COMBINACOES:
        d = [l for l in linhas if l["combinacao"] == comb]
        ok = len({l["nome"] for l in d if l["status"] in ("ok", "aviso")})
        av = sum(1 for l in d if l["status"] == "aviso")
        fa = sum(1 for l in d if l["status"] == "falhou")
        print(f"{comb:<10} {ok:>5}/{CICLOS} {av:>10} {fa:>24}")
    falhas = [l for l in linhas if l["status"] == "falhou"]
    if falhas:
        print("\nÚltimas falhas:")
        for l in falhas[-5:]:
            print(f"  {l['fim']}  {l['nome']} tentativa {l['tentativa']}: {l['motivo']}")
    avisos = [l for l in linhas if l["status"] == "aviso"]
    if avisos:
        print("\nÚltimos avisos:")
        for l in avisos[-5:]:
            print(f"  {l['fim']}  {l['nome']}: {l['motivo']}")
    if REPAROS.exists():
        with open(REPAROS) as f:
            reparos = list(csv.DictReader(f))
        print(f"\nConsertos automáticos: {len(reparos)}")
        for r in reparos[-5:]:
            print(f"  {r['hora']}  {r['alvo']} antes da {r['antes_de']} ({r['motivo']}): {r['resultado']}")
    if linhas:
        u = linhas[-1]
        print(f"\nÚltima tentativa: {u['nome']} ({u['status']}) às {u['fim']}")
    sessoes = subprocess.run(["tmux", "ls"], capture_output=True, text=True).stdout.splitlines()
    rodando = [s.split(":")[0] for s in sessoes if s.startswith(("fase8:", "exp_"))]
    print("Agora: " + (", ".join(rodando) + " (tmux attach -t fase8)" if rodando else "nenhuma fila rodando"))


def seco(n_ciclos):
    falta = pendentes(ler_fila())
    ciclos = sorted({c for c, _, _ in falta})[:n_ciclos]
    for erro in conferir_preparo():
        print(f"[ERRO, a fila não começaria] {erro}")
    for c, i, comb in falta:
        if c in ciclos:
            print(f"ciclo {c:>2}, {i}/5: {comb[1]}_{c:02d}  ({comb[2]})  -> resultados/{comb[0]}/")
    n = sum(1 for c, _, _ in falta if c in ciclos)
    print(f"\n{n} execuções, ~{n * 19 / 60:.1f} h")


if __name__ == "__main__":
    uso = "uso: fila_fase8.py --ciclos N | --status | --seco N"
    if len(sys.argv) == 2 and sys.argv[1] == "--status":
        status()
    elif len(sys.argv) == 3 and sys.argv[1] in ("--ciclos", "--seco") and sys.argv[2].isdigit() and int(sys.argv[2]) > 0:
        (fila if sys.argv[1] == "--ciclos" else seco)(int(sys.argv[2]))
    else:
        sys.exit(uso)
