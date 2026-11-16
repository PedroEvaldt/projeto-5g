#!/bin/bash
# =========================================================================
# Sondas de latência para os controles da Fase 9: a hvr_l2 sai do SLA pelo net quando o gold toca no alto
# (docs/render-real.md). O UE de VR (container ue do ue-vr-07) pinga, 5 vezes por segundo, quatro destinos; cada um
# mede um pedaço do caminho da VR:
#   upf4_rede        nó dos UEs -> upf4, pela rede dos pods (sem o 5G)
#   upf4_tunel       nó dos UEs -> upf4, pelo túnel 5G (uesimtun0: o gNB/UE do UERANSIM e o GTP até a upf4)
#   servidor_rede    nó dos UEs -> vr-app-01, pela rede dos pods
#   servidor_tunel   nó dos UEs -> vr-app-01, pelo túnel 5G (o caminho dos pacotes da VR)
# Cada resposta sai com a hora do pod (ping -D), o mesmo relógio do Timestamp_Pod do qoe_vr_timeseries.csv: o
# metricas_scripts/analise_sondas.py cruza as duas. Os pings são de 10 min (ping -w), para nenhum ficar órfão no pod
# se a sonda morrer; um pod recriado (conserto da fila) só interrompe a sonda por alguns segundos.
#
# Uso (da raiz do repositório, na ledzeppelin), num tmux à parte enquanto a fila roda:
#   tmux new-session -d -s sonda "scripts/sonda_rede.sh metricas_scripts/resultados/fase9m_sonda"   # começa
#   tmux kill-session -t sonda                                                                       # para
# =========================================================================
set -uo pipefail
NS=pfsevaldt
SAIDA=${1:?uso: $0 <pasta de saída>}
mkdir -p "$SAIDA"
trap 'kill 0' INT TERM EXIT

sonda() {   # nome, interface do UE, rótulo app= do pod de destino
    while true; do
        IP=$(kubectl get pod -n $NS -l app="$3" --field-selector=status.phase=Running \
             -o jsonpath='{.items[0].status.podIP}' 2>/dev/null)
        if [ -n "$IP" ]; then
            echo "# $(date -u +%FT%TZ) destino $3 = $IP pela $2" >> "$SAIDA/$1.txt"
            # -O: avisa a resposta que não veio; -W 1: espera 1 s por ela; -w 600: termina em 10 min
            kubectl exec -n $NS deploy/ue-vr-07 -c ue -- \
                ping -D -n -O -W 1 -w 600 -i 0.2 -I "$2" "$IP" >> "$SAIDA/$1.txt" 2>&1
        fi
        sleep 2
    done
}
sonda upf4_rede eth0 upf4 &
sonda upf4_tunel uesimtun0 upf4 &
sonda servidor_rede eth0 vr-app-01 &
sonda servidor_tunel uesimtun0 vr-app-01 &
echo "Sondas em $SAIDA (Ctrl+C, ou tmux kill-session -t sonda, para)"
wait
