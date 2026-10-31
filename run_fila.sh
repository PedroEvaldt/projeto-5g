#!/bin/bash
# =========================================================================
# Roda várias execuções em sequência, sem ninguém olhando. Para cada uma:
# run_experiment_tmux.sh, espera o fim (coletor de QoE e, com VR, o da VR),
# pós-execução (fetch_prometheus, resumo, merge), fecha a sessão e passa à próxima.
#
# Uso (da raiz do repositório, de dentro de um tmux para sobreviver ao SSH):
#   tmux new-session -d -s fila './run_fila.sh tests_configs/a.yaml exec01 tests_configs/b.yaml:inform exec02'
#
# Os argumentos vão em pares <yaml> <nome da execução>. Com ":inform" no fim do yaml, a execução
# roda com bidding.inform_updates_priority ligada, numa pasta própria (<cenario>_inform/).
# Uma execução que falha ou passa do tempo é registrada e a fila segue.
# O log de tudo fica em metricas_scripts/resultados/fila_<data>.log.
# =========================================================================
set -uo pipefail

[ $# -ge 2 ] && [ $(($# % 2)) -eq 0 ] || { echo "uso: $0 <yaml>[:inform] <nome> [<yaml>[:inform] <nome> ...]"; exit 1; }
REPO=$(cd "$(dirname "$0")" && pwd)
PY="$REPO/venv/bin/python"
PAUSA=60                    # entre execuções: o XMPP solta a porta e os clientes assentam
cd "$REPO"
mkdir -p metricas_scripts/resultados
LOG="$REPO/metricas_scripts/resultados/fila_$(date +%Y%m%d_%H%M%S).log"
log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG"; }

# Confere os pares antes de começar, para não descobrir um erro de digitação na 3ª execução
ARGS=("$@")
for ((i = 0; i < $#; i += 2)); do
    yaml=${ARGS[i]%:inform}
    [ -f "$yaml" ] || { echo "[ERRO] não existe: $yaml"; exit 1; }
done

roda() {    # $1 = yaml[:inform], $2 = nome da execução
    local base=${1%:inform} run=$2 cfg
    cfg=$base
    if [ "$1" != "$base" ]; then
        # Cópia com a chave ligada e a saída em <cenario>_inform/ (o run_experiment só troca o último nível da pasta)
        cfg="tests_configs/_run_${run}_base.yaml"
        "$PY" - "$base" "$cfg" <<'PYEOF'
import sys, yaml
base, out = sys.argv[1:]
c = yaml.safe_load(open(base))
c["bidding"]["inform_updates_priority"] = True
cenario, ultimo = c["monitoring"]["csv_output_dir"].rstrip("/").rsplit("/", 1)
c["monitoring"]["csv_output_dir"] = f"{cenario}_inform/{ultimo}/"
yaml.safe_dump(c, open(out, "w"), allow_unicode=True, sort_keys=False)
PYEOF
    fi

    log "=== $run ($1) ==="
    if ! ./run_experiment_tmux.sh "$cfg" "$run" >> "$LOG" 2>&1; then
        log "[FALHOU] $run: o run_experiment_tmux.sh não subiu (veja acima no log)"; return
    fi

    local sessao="exp_${run}" final dur pasta tem_vr
    final="$REPO/tests_configs/_run_${run}.yaml"
    dur=$("$PY" -c "import yaml,sys; print(yaml.safe_load(open(sys.argv[1]))['test_metadata']['duration_seconds'])" "$final")
    pasta="$REPO/metricas_scripts/$("$PY" -c "import yaml,sys; print(yaml.safe_load(open(sys.argv[1]))['monitoring']['csv_output_dir'])" "$final" | sed 's#^\.\./metricas_scripts/##')"
    tem_vr=$("$PY" -c "import yaml,sys; c=yaml.safe_load(open(sys.argv[1])); print(any(s.get('tipo') == 'vr' for s in c['slices'].values()))" "$final")

    # Espera o fim: o "[SUCESSO]" do coletor de QoE e, com VR, o qoe_vr_timeseries.csv. Limite: duração + 10 min
    local limite=$((SECONDS + dur + 600)) pronto=0
    while [ $SECONDS -lt $limite ]; do
        sleep 30
        if tmux capture-pane -p -S - -t "$sessao:qoe" 2>/dev/null | grep -q "\[SUCESSO\]" &&
           { [ "$tem_vr" = False ] || [ -f "$pasta/qoe_vr_timeseries.csv" ]; }; then
            pronto=1; break
        fi
    done
    # Guarda o que cada janela mostrou, para conferir depois sem a sessão aberta
    mkdir -p "$pasta/janelas"
    for janela in upf slices llm leilao eventos qoe; do
        tmux capture-pane -p -S - -t "$sessao:$janela" > "$pasta/janelas/$janela.txt" 2>/dev/null || true
    done
    tmux kill-session -t "$sessao" 2>/dev/null || true
    if [ $pronto -eq 0 ]; then
        log "[FALHOU] $run: não terminou em $((dur / 60 + 10)) min; sessão fechada, janelas em $pasta/janelas/"; return
    fi
    log "$run terminou; pós-execução"

    # Pós-execução (docs/coleta-vr.md); um passo que falha não para os outros
    (
        cd metricas_scripts
        R="${pasta#$REPO/metricas_scripts/}"
        TEST_FILE="$final" "$PY" fetch_prometheus.py "$R" || echo "[FALHOU] fetch_prometheus.py"
        "$PY" resumo_execucao.py "$R" > "$R/resumo.txt" 2>&1 || echo "[FALHOU] resumo_execucao.py"
        "$PY" merge_auction_and_qoe.py "$R" "$R/analise/" || echo "[FALHOU] merge_auction_and_qoe.py"
    ) >> "$LOG" 2>&1
    log "$run pronto: $pasta"
}

for ((i = 0; i < $#; i += 2)); do
    [ $i -gt 0 ] && { log "pausa de $PAUSA s"; sleep $PAUSA; }
    roda "${ARGS[i]}" "${ARGS[i + 1]}"
done
log "=== fila terminada ($(($# / 2)) execuções); resumo: grep -E 'FALHOU|pronto' $LOG ==="
