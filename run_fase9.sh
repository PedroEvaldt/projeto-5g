#!/bin/bash
# =========================================================================
# Fila da Fase 9 (render real: 3 combinações x 5 ciclos, intercaladas). É a fila da Fase 8 (scripts/fila_fase8.py)
# com FILA_FASE=9: YAMLs *_render.yaml, saída em metricas_scripts/resultados/fase9/ e estado em fase9_fila.csv.
# Antes: o testbed com scripts/build_testbeds.sh --render-real e o vr/calibracao_render.yaml (a etapa 6).
#
# Uso (da raiz do repositório, na ledzeppelin):
#   ./run_fase9.sh --ciclos 5      # roda os próximos 5 ciclos (~5 h) num tmux "fase9" e volta; pode sair do SSH
#   ./run_fase9.sh --status        # quantas execuções válidas por combinação, falhas e avisos
#   ./run_fase9.sh --seco 1        # mostra o que o próximo ciclo rodaria, sem rodar nada
#   touch metricas_scripts/resultados/fase9_parar   # para a fila depois da execução atual
#
# Controle (resultados/fase9s/): a hvr_l2 com o servidor sintético da Fase 8 no mesmo nó, depois desta fila.
#   scripts/trocar_servidor_vr.sh sintetico         # troca o vr-app-01 pelo sintético no metallica
#   FILA_FASE=9s ./run_fase9.sh --ciclos 5          # 5 x hvr_l2 com ele (~1 h 40 min); --status e --seco também
#   touch metricas_scripts/resultados/fase9s_parar  # para o controle depois da execução atual
#   scripts/trocar_servidor_vr.sh render            # volta ao render real
#
# Acompanhar: tmux attach -t fase9 (Ctrl+b d sai sem parar). Estado: metricas_scripts/resultados/fase9_fila.csv
# =========================================================================
set -euo pipefail
REPO=$(cd "$(dirname "$0")" && pwd)
PY="$REPO/venv/bin/python"
cd "$REPO"
[ -x "$PY" ] || { echo "[ERRO] venv não encontrado em $REPO/venv"; exit 1; }
export FILA_FASE=${FILA_FASE:-9}
[[ "$FILA_FASE" =~ ^9s?$ ]] || { echo "[ERRO] FILA_FASE=$FILA_FASE: este script é da Fase 9 (9 ou 9s)"; exit 1; }

if [ "${1:-}" = "--ciclos" ] && [ -z "${FASE9_DENTRO:-}" ]; then
    # Confere antes de abrir o tmux, para o erro aparecer aqui e não dentro da sessão
    "$PY" scripts/fila_fase8.py --seco "${2:?uso: $0 --ciclos N}" | grep '^\[ERRO' && exit 1
    if tmux has-session -t fase9 2>/dev/null; then
        echo "[ERRO] a sessão fase9 já existe (tmux attach -t fase9). Feche-a antes: tmux kill-session -t fase9"; exit 1
    fi
    tmux new-session -d -s fase9 "FASE9_DENTRO=1 FILA_FASE=$FILA_FASE '$REPO/run_fase9.sh' --ciclos $2; exec bash"
    echo "Fila da Fase $FILA_FASE começou no tmux fase9 ($2 ciclos)."
    echo "  Acompanhar:  tmux attach -t fase9      (Ctrl+b d sai sem parar)"
    echo "  Resumo:      FILA_FASE=$FILA_FASE ./run_fase9.sh --status"
    echo "  Parar:       touch metricas_scripts/resultados/fase${FILA_FASE}_parar   (depois da execução atual)"
    exit 0
fi
exec "$PY" scripts/fila_fase8.py "$@"
