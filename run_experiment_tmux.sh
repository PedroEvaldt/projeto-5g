#!/bin/bash
# =========================================================================
# Roda UMA execução do experimento numa sessão tmux (funciona por SSH, sem
# interface gráfica). Mesma sequência e mesmos atrasos do run_experiment.sh.
#
# Uso (da raiz do repositório):
#   ./run_experiment_tmux.sh tests_configs/test_config.yaml exec01
#   ./run_experiment_tmux.sh tests_configs/test_config.yaml exec01 --completo
#
# O segundo argumento é o nome da execução. Os CSVs vão para
#   metricas_scripts/resultados/<cenario do YAML>/<nome da execução>/
# e cada execução ganha a sua pasta, sem sobrescrever as anteriores. O YAML usado vai junto (config.yaml).
#
# Antes de começar os eventos, confere os clientes (Fase 8): cada cliente de vídeo tem de mostrar um
# [STATUS 5s] com bitrate > 0 e o UE de VR tem de ter a uesimtun0, em até CONFERENCIA_S (180 s). Se não,
# fecha a sessão, grava o motivo em <pasta>/conferencia.txt e sai com 3.
#
# Sem --completo, o script sobe tudo e volta (a execução segue no tmux). Com --completo, espera o fim,
# guarda as janelas do tmux em <pasta>/janelas/, fecha a sessão, roda a pós-execução e o
# conferir_execucao.py (<pasta>/veredito.txt). É o que a fila da Fase 8 (run_fase8.sh) usa.
# Saída: 0 = ok (ou aviso), 3 = conferência dos clientes falhou, 4 = não terminou no tempo,
#        5 = conferir_execucao.py mandou descartar; outro código = erro ao subir.
# =========================================================================
set -euo pipefail

USO="uso: $0 <arquivo de config .yaml> <nome da execução> [--completo]"
BASE_CFG=${1:?"$USO"}
RUN=${2:?"$USO"}
COMPLETO=0
[ "${3:-}" = "--completo" ] && COMPLETO=1
[ -z "${3:-}" ] || [ $COMPLETO -eq 1 ] || { echo "$USO"; exit 2; }
CONFERENCIA_S=${CONFERENCIA_S:-180}
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
# Só os clientes de vídeo são reiniciados: reiniciar o UE de VR registraria o IMSI dele duas vezes (Fase 5)
PODS=$("$PY" -c "import yaml,sys; c=yaml.safe_load(open(sys.argv[1])); print(' '.join('deployment/'+s['client_pod'] for s in c['slices'].values() if s.get('tipo', 'video') != 'vr'))" "$CFG")
# O UE de VR, se o cenário tiver (tipo: vr): o log dele é copiado durante a execução e analisado no fim
VR_POD=$("$PY" -c "import yaml,sys; c=yaml.safe_load(open(sys.argv[1])); print(next((s['client_pod'] for s in c['slices'].values() if s.get('tipo') == 'vr'), ''))" "$CFG")
VR_FATIA=$("$PY" -c "import yaml,sys; c=yaml.safe_load(open(sys.argv[1])); print(next((n for n, s in c['slices'].items() if s.get('tipo') == 'vr'), ''))" "$CFG")
DURACAO=$("$PY" -c "import yaml,sys; print(yaml.safe_load(open(sys.argv[1]))['test_metadata']['duration_seconds'])" "$CFG")

PASTA_LOCAL="metricas_scripts/${OUT_DIR#../metricas_scripts/}"
if [ -n "$(ls -A "$PASTA_LOCAL" 2>/dev/null)" ]; then
    rm -f "$CFG"
    echo "[ERRO] $PASTA_LOCAL já tem arquivos de outra execução. Escolha outro nome."; exit 1
fi

mkdir -p "$REPO/$PASTA_LOCAL"
cp "$CFG" "$REPO/$PASTA_LOCAL/config.yaml"     # a pasta da execução leva o YAML exato que rodou

echo "Configuração desta execução: $CFG"
echo "Resultados em: $PASTA_LOCAL"
echo "Clientes que serão reiniciados: $PODS"
[ -n "$VR_POD" ] && echo "VR: fatia $VR_FATIA, $VR_POD (não é reiniciado; o log vai para $PASTA_LOCAL/vr_log.txt)"

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
REINICIO=$SECONDS
echo "4/6 Clientes reiniciados; conferindo se tocam (até $CONFERENCIA_S s)"

# Conferência (Fase 8): o vídeo tocando e o túnel da VR de pé, antes dos eventos. Um cliente de vídeo
# pode não subir depois do restart (espera a uesimtun0 para sempre, sem avisar), e o UE de VR fica sem
# túnel quando a upf4 passa um tempo no mínimo do leilão e perde o PFCP (volta ~20 s depois do reset).
pod_novo() {    # o pod do restart: o antigo segue de pé (e com o log da execução anterior) até o novo ficar pronto
    local quando
    quando=$(kubectl get deployment "$1" -n "$NS" -o jsonpath='{.spec.template.metadata.annotations.kubectl\.kubernetes\.io/restartedAt}')
    kubectl get pods -n "$NS" -l "app=$1" -o json | "$PY" -c "
import json, sys
p = [i for i in json.load(sys.stdin)['items'] if not i['metadata'].get('deletionTimestamp')
     and (i['metadata'].get('annotations') or {}).get('kubectl.kubernetes.io/restartedAt') == sys.argv[1]]
print(p[0]['metadata']['name'] if p else '')" "$quando"
}
faltando() {    # imprime o que ainda não está pronto; nada = tudo pronto
    local d pod
    for d in $PODS; do
        d=${d#deployment/}
        pod=$(pod_novo "$d")
        # grep sem -q: com o pipefail, o -q sai no 1º achado e o SIGPIPE do kubectl viraria falha
        if [ -z "$pod" ] || ! kubectl logs -n "$NS" "$pod" -c dash-client 2>/dev/null |
                grep -E '\[STATUS 5s\].*Bitrate: [1-9][0-9]* kbps' > /dev/null; then
            echo -n "$d (vídeo sem [STATUS 5s] com bitrate > 0) "
        fi
    done
    if [ -n "$VR_POD" ] && ! kubectl exec -n "$NS" "deploy/$VR_POD" -c ue -- ip -br addr show uesimtun0 >/dev/null 2>&1; then
        echo -n "$VR_POD (VR sem uesimtun0) "
    fi
}
while :; do
    sleep 5
    FALTA=$(faltando)
    [ -z "$FALTA" ] && [ $((SECONDS - REINICIO)) -ge 20 ] && break      # pelo menos os 20 s de antes
    if [ $((SECONDS - REINICIO)) -ge "$CONFERENCIA_S" ]; then
        echo "conferência dos clientes falhou em $CONFERENCIA_S s: $FALTA" | tee "$REPO/$PASTA_LOCAL/conferencia.txt"
        tmux kill-session -t "$SESSAO" 2>/dev/null || true
        echo "[ERRO] sessão $SESSAO fechada; nenhum evento rodou. Rode de novo com outro nome."; exit 3
    fi
done
echo "   Clientes prontos em $((SECONDS - REINICIO)) s"

# VR (Fase 5): copia o log do exporter durante a execução, para a análise não depender do pod depois
DEPOIS_EVENTOS=""
if [ -n "$VR_POD" ]; then
    tmux new-window -t "$SESSAO" -n vrlog "kubectl logs -f --timestamps --since=1s deploy/$VR_POD -c vr-client -n $NS > '$REPO/$PASTA_LOCAL/vr_log.txt'"
    echo "   Cópia do log da VR (janela vrlog)"
    # Quando o deploy_test.py termina (TEST_END gravado), o coletor da VR gera o qoe_vr_timeseries.csv
    DEPOIS_EVENTOS="python3 analise_metricas_vr.py '$OUT_DIR' --pod $VR_POD --fatia $VR_FATIA;"
fi

# 6. Orquestrador de eventos e, 40 s depois, o coletor de QoE
tmux new-window -t "$SESSAO" -n eventos "$ENV; cd '$REPO/metricas_scripts' && python3 deploy_test.py; $DEPOIS_EVENTOS exec bash"
echo "5/6 Orquestrador de eventos (janela eventos); aguardando 40 s"; sleep 40
tmux new-window -t "$SESSAO" -n qoe "$ENV; cd '$REPO/metricas_scripts' && python3 analise_metricas_timeseries.py 5 $((DURACAO - 40)); exec bash"
echo "6/6 Coletor de QoE (janela qoe)"

if [ $COMPLETO -eq 1 ]; then
    # Espera o fim: o [SUCESSO] do coletor de QoE e, com VR, o qoe_vr_timeseries.csv. Limite: duração + 10 min
    echo "Esperando o fim (~$((DURACAO / 60)) min; tmux attach -t $SESSAO para acompanhar)"
    PASTA="$REPO/$PASTA_LOCAL"
    LIMITE=$((SECONDS + DURACAO + 600)); PRONTO=0
    while [ $SECONDS -lt $LIMITE ]; do
        sleep 30
        if tmux capture-pane -p -S - -t "$SESSAO:qoe" 2>/dev/null | grep "\[SUCESSO\]" > /dev/null &&
           { [ -z "$VR_POD" ] || [ -f "$PASTA/qoe_vr_timeseries.csv" ]; }; then
            PRONTO=1; break
        fi
    done
    # Guarda o que cada janela mostrou, para conferir depois sem a sessão aberta
    mkdir -p "$PASTA/janelas"
    for JANELA in upf slices llm leilao eventos qoe; do
        tmux capture-pane -p -S - -t "$SESSAO:$JANELA" > "$PASTA/janelas/$JANELA.txt" 2>/dev/null || true
    done
    tmux kill-session -t "$SESSAO" 2>/dev/null || true
    if [ $PRONTO -eq 0 ]; then
        echo "[ERRO] não terminou em $((DURACAO / 60 + 10)) min; sessão fechada, janelas em $PASTA_LOCAL/janelas/"; exit 4
    fi

    # Pós-execução (docs/coleta-vr.md); um passo que falha não para os outros
    echo "Terminou; pós-execução"
    cd "$REPO/metricas_scripts"
    R="${PASTA_LOCAL#metricas_scripts/}"
    TEST_FILE="$CFG" "$PY" fetch_prometheus.py "$R" || echo "[FALHOU] fetch_prometheus.py"
    "$PY" resumo_execucao.py "$R" > "$R/resumo.txt" 2>&1 || echo "[FALHOU] resumo_execucao.py"
    "$PY" merge_auction_and_qoe.py "$R" "$R/analise/" > /dev/null || echo "[FALHOU] merge_auction_and_qoe.py"
    "$PY" conferir_execucao.py "$R" || exit 5
    exit 0
fi

cat <<EOF

Tudo iniciado. O teste dura ~$((DURACAO / 60)) min a partir de agora.
  Acompanhar:  tmux attach -t $SESSAO     (Ctrl+b e um número troca de janela; Ctrl+b d sai sem parar nada)
  Encerrar:    tmux kill-session -t $SESSAO   (só quando a janela qoe mostrar "[SUCESSO] Coleta finalizada")
  Depois:      veja o guia COMO-RODAR.md, parte "Depois de cada execução"
EOF
if [ -n "$VR_POD" ]; then
    echo "  VR:          antes de encerrar, espere também o [CSV] do coletor da VR na janela eventos"
fi
