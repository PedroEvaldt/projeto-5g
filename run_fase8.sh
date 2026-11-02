#!/bin/bash
# =========================================================================
# Fila da Fase 8 (5 combinações x 30 ciclos, intercaladas). A lógica está em scripts/fila_fase8.py.
#
# Uso (da raiz do repositório, na ledzeppelin):
#   ./run_fase8.sh --ciclos 8      # roda os próximos 8 ciclos (~12 h) num tmux "fase8" e volta; pode sair do SSH
#   ./run_fase8.sh --status        # quantas execuções válidas por combinação, falhas e avisos
#   ./run_fase8.sh --seco 2        # mostra o que os próximos 2 ciclos rodariam, sem rodar nada
#   touch metricas_scripts/resultados/fase8_parar   # para a fila depois da execução atual
#
# Acompanhar: tmux attach -t fase8 (Ctrl+b d sai sem parar). Estado: metricas_scripts/resultados/fase8_fila.csv
# =========================================================================
set -euo pipefail
REPO=$(cd "$(dirname "$0")" && pwd)
PY="$REPO/venv/bin/python"
cd "$REPO"
[ -x "$PY" ] || { echo "[ERRO] venv não encontrado em $REPO/venv"; exit 1; }

if [ "${1:-}" = "--ciclos" ] && [ -z "${FASE8_DENTRO:-}" ]; then
    # Confere antes de abrir o tmux, para o erro aparecer aqui e não dentro da sessão
    "$PY" scripts/fila_fase8.py --seco "${2:?uso: $0 --ciclos N}" | grep '^\[ERRO' && exit 1
    if tmux has-session -t fase8 2>/dev/null; then
        echo "[ERRO] a sessão fase8 já existe (tmux attach -t fase8). Feche-a antes: tmux kill-session -t fase8"; exit 1
    fi
    tmux new-session -d -s fase8 "FASE8_DENTRO=1 '$REPO/run_fase8.sh' --ciclos $2; exec bash"
    echo "Fila começou no tmux fase8 ($2 ciclos)."
    echo "  Acompanhar:  tmux attach -t fase8      (Ctrl+b d sai sem parar)"
    echo "  Resumo:      ./run_fase8.sh --status"
    echo "  Parar:       touch metricas_scripts/resultados/fase8_parar   (depois da execução atual)"
    exit 0
fi
exec "$PY" scripts/fila_fase8.py "$@"
