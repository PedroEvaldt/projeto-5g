#!/bin/bash

# ================= CONFIGURAÇÕES =================
NAMESPACE="pfsevaldt"
# array contendo o start_id para cada slice
START_ID=(25 29 33 37 41 45)
COUNT=1
TARGET_number=(4 5 6 7 8 9) # Alvos: iperf01, iperf02, ..., iperf08
PORT="5201"
BANDWIDTH="10M"
DURATION="600"
# =================================================

echo "--- Iniciando Ataque Distribuído (UDP Flood) ---"

for (( slice=0; slice<${#START_ID[@]}; slice++ ))
do
    for (( i=0; i<$COUNT; i++ ))
    do
        CURRENT_ID=$((${START_ID[slice]} + $i))
        SUFFIX=$(printf "%02d" $CURRENT_ID)
        
        DEPLOY_NAME="ue-iperf-${SUFFIX}"
        TARGET="iperf$(printf "%02d" ${TARGET_number[slice]})"
        # Pega o nome completo do pod
        POD_NAME=$(kubectl get pods -n ${NAMESPACE} -l app=${DEPLOY_NAME} -o jsonpath="{.items[0].metadata.name}")

        if [ -z "$POD_NAME" ]; then
            echo "[ERRO] Pod não encontrado para ${DEPLOY_NAME}"
            continue
        fi

        echo ">>> Disparando iPerf no container 'iperf-client' do pod: $POD_NAME"

        # kubectl exec -n ${NAMESPACE} ${POD_NAME} -c iperf-client -- \
        #     sh -c "nohup iperf -c ${TARGET} -p ${PORT} -u -b ${BANDWIDTH} -t ${DURATION} -i 10 > /tmp/iperf.log 2>&1 &" &
        kubectl exec -n ${NAMESPACE} ${POD_NAME} -c iperf-client -- \
            sh -c "nohup iperf3 -c ${TARGET} -t ${DURATION} -i 10 -R > /tmp/iperf.log 2>&1 &" &
    done
done
echo "--- Ataque iniciado! ---"
echo "Verifique com: kubectl exec -it ${POD_NAME} -n ${NAMESPACE} -c iperf-client -- ps aux"