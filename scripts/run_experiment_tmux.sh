#!/bin/bash
# =========================================================================
# Roda UMA execução do experimento numa sessão tmux (funciona por SSH, sem
# interface gráfica). Mesma sequência e mesmos atrasos do run_experiment.sh.
#
# Uso (da raiz do repositório):
#   ./run_experiment_tmux.sh tests_configs/test_config.yaml exec01
#
# O segundo argumento é o nome da execução. Os CSVs vão para
#   metricas_scripts/resultados/<cenario do YAML>/<nome da execução>/
# e cada execução ganha a sua pasta, sem sobrescrever as anteriores.
# =========================================================================
set -euo pipefail

BASE_CFG=${1:?"uso: $0 <arquivo de config .yaml> <nome da execução>"}
RUN=${2:?"uso: $0 <arquivo de config .yaml> <nome da execução>"}
NS=pfsevaldt
REPO=$(cd "$(dirname "$0")" && pwd)
SESSAO="exp_${RUN}"
PY="$REPO/venv/bin/python"

cd "$REPO"
[ -x "$PY" ] || { echo "[ERRO] venv não encontrado em $REPO/venv (veja a Fase 0)"; exit 1; }
[ -f .env ] || { echo "[ERRO] falta o .env com API_KEY (copie o .env.example)"; exit 1; }
if tmux has-session -t "$SESSAO" 2>/dev/null; then
    echo "[ERRO] a sessão $SESSAO já existe. Use outro nome ou feche-a: tmux kill-session -t $SESSAO"; exit 1
fi
if tmux ls 2>/dev/null | grep -q '^exp_'; then
    echo "[ERRO] há outra execução aberta (tmux ls). Feche-a antes: os agentes usam a mesma porta do XMPP."; exit 1
fi

# 1. Copia o YAML trocando só a pasta de saída: .../resultados/<cenario>/<RUN>/
CFG="$REPO/tests_configs/_run_${RUN}.yaml"
OUT_DIR=$("$PY" - "$BASE_CFG" "$CFG" "$RUN" <<'PYEOF'
import sys, yaml
base, out, run = sys.argv[1:]
c = yaml.safe_load(open(base))
cenario = c["monitoring"]["csv_output_dir"].rstrip("/").rsplit("/", 1)[0]
c["monitoring"]["csv_output_dir"] = f"{cenario}/{run}/"
yaml.safe_dump(c, open(out, "w"), allow_unicode=True, sort_keys=False)
print(c["monitoring"]["csv_output_dir"])
PYEOF
)
PODS=$("$PY" -c "import yaml,sys; c=yaml.safe_load(open(sys.argv[1])); print(' '.join('deployment/'+s['client_pod'] for s in c['slices'].values()))" "$CFG")
DURACAO=$("$PY" -c "import yaml,sys; print(yaml.safe_load(open(sys.argv[1]))['test_metadata']['duration_seconds'])" "$CFG")

PASTA_LOCAL="metricas_scripts/${OUT_DIR#../metricas_scripts/}"
if [ -n "$(ls -A "$PASTA_LOCAL" 2>/dev/null)" ]; then
    rm -f "$CFG"
    echo "[ERRO] $PASTA_LOCAL já tem arquivos de outra execução. Escolha outro nome."; exit 1
fi

echo "Configuração desta execução: $CFG"
echo "Resultados em: $PASTA_LOCAL"
echo "Clientes que serão reiniciados: $PODS"

# Comando comum a todas as janelas: usa o YAML desta execução e ativa o venv
ENV=". '$REPO/venv/bin/activate'; export TEST_FILE='$CFG'"

# 2. Reseta CPU e banda das UPFs
tmux new-session -d -s "$SESSAO" -n upf "$ENV; cd '$REPO/spade_agents' && python3 upf_set_resources.py; exec bash"
echo "1/6 UPFs resetadas (janela upf)"; sleep 6

# 3. Slice Agents (sobem também o servidor XMPP embutido)
tmux new-window -t "$SESSAO" -n slices "$ENV; cd '$REPO/spade_agents' && python3 slice_agents.py; exec bash"
echo "2/6 Slice Agents (janela slices)"; sleep 6

# 4. Estrategista (LLM) e leiloeiro
tmux new-window -t "$SESSAO" -n llm "$ENV; cd '$REPO/spade_agents' && python3 strategist_agent.py; exec bash"
tmux new-window -t "$SESSAO" -n leilao "$ENV; cd '$REPO/spade_agents' && python3 resource_agent.py; exec bash"
echo "3/6 Estrategista (janela llm) e leiloeiro (janela leilao)"; sleep 1

# 5. Reinicia os clientes de vídeo para começarem do zero
kubectl rollout restart $PODS -n "$NS"
echo "4/6 Clientes reiniciados; aguardando 20 s"; sleep 20

# 6. Orquestrador de eventos e, 40 s depois, o coletor de QoE
tmux new-window -t "$SESSAO" -n eventos "$ENV; cd '$REPO/metricas_scripts' && python3 deploy_test.py; exec bash"
echo "5/6 Orquestrador de eventos (janela eventos); aguardando 40 s"; sleep 40
tmux new-window -t "$SESSAO" -n qoe "$ENV; cd '$REPO/metricas_scripts' && python3 analise_metricas_timeseries.py 5 $((DURACAO - 40)); exec bash"
echo "6/6 Coletor de QoE (janela qoe)"

cat <<EOF

Tudo iniciado. O teste dura ~$((DURACAO / 60)) min a partir de agora.
  Acompanhar:  tmux attach -t $SESSAO     (Ctrl+b e um número troca de janela; Ctrl+b d sai sem parar nada)
  Encerrar:    tmux kill-session -t $SESSAO   (só quando a janela qoe mostrar "[SUCESSO] Coleta finalizada")
  Depois:      veja o guia COMO-RODAR.md, parte "Depois de cada execução"
EOF
