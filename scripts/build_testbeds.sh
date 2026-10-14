#!/bin/bash

set -euo pipefail


PROJECT_DIR="/home/pfsevaldt/dev/projeto-5g"
NS=pfsevaldt
TMP_DIR="$PROJECT_DIR/scripts/tmp"

check_pods() {
    sleep 2
    while true; do
        pending=$(kubectl get pods -n "$NS" --no-headers | awk '$3 != "Running" {n=$1; sub(/-.*/, "", n); printf "%s (%s)\n", n, $3}')
        [[ -z "$pending" ]] && break
        echo "---------------------------------------------------------------------------"
        while read -r line; do
            echo "waiting for pod $line..."
        done <<< "$pending"
        echo "---------------------------------------------------------------------------"
        sleep 5
    done 
}

check_subscribers() {
    local -A WANTED=([01]=000001 [02]=000001 [03]=000002 [04]=000002 [05]=000003 [06]=000003)
    local status data format sufix expected

    data=$(kubectl exec -n "$NS" deploy/mongodb -- mongosh open5gs --quiet --eval '
    db.subscribers.find({}, {_id: 0, imsi: 1, slice: 1}).sort({imsi: 1}).forEach(s => {
    const f = (s.slice && s.slice[0]) || {};
    const dnn = (f.session && f.session[0]) ? f.session[0].name : "-";
    print(`${s.imsi} ${f.sd || "-"} ${dnn}`);
    })')

    if [[ -z "$data" ]]; then
        echo "Unable to find subscribers, or could not access MongoDB"
        exit 1
    fi

    format="%-17s %-8s %-9s %s\n"
    printf "$format" "IMSI" "SD" "DNN" "CONFERE"
    printf "$format" "---------------" "------" "-------" "-------"

    while read -r imsi sd dnn; do
        sufix=${imsi: -2}
        expected=${WANTED[$sufix]:-}
        if [[ -z expected ]]; then 
            status="-"
        elif [[ "$sd" != "$expected" ]]; then
            status="WRONG (expected $expected)"
        else
            status="ok"
        fi
        printf "$format" "$imsi" "$sd" "$dnn" "$status"
    done <<< "$data"

    echo
    echo "Total $(wc -l <<< $data) subscribers"
}

main() {
    mkdir -p "$TMP_DIR"


    echo Subindo banco de assinantes
    kubectl apply -n $NS -f "$PROJECT_DIR/5Gcore_files/recent-database.yaml"        # MongoDB (onde ficam os assinantes) e o dbctl
    check_pods

    echo Subindo UPFs
    bash "$PROJECT_DIR/5Gcore_files/deploy-upf.sh"                           # cria upf1 a upf9 no nó wb-fibre-01, com resize sem reinício e banda do Calico
    check_pods

    echo Subindo Core 5G
    kubectl apply -n $NS -f  "$PROJECT_DIR/5Gcore_files/recent-op5g.yaml"            # funções do core: NRF, AMF, SMF, SMF2, AUSF, UDM, UDR, NSSF, PCF, BSF
    kubectl apply -n $NS -f  "$PROJECT_DIR/5Gcore_files/webui.yaml"                  # (opcional) interface web para ver os assinantes
    check_pods


    echo Subindo assinantes
    for f in "1 2 slice01 000001" "3 4 slice02 000002" "5 6 slice03 000003"; do
      set -- $f                                                # separa: início, fim, DNN e SD da fatia
      arq_registro="$TMP_DIR/registro-$3.yaml"

      sed -e "s/var start_imsi_suffix = .*/var start_imsi_suffix = $1;/" \
          -e "s/var end_imsi_suffix = .*/var end_imsi_suffix = $2;/" \
          -e "s/var slice_dnn = .*/var slice_dnn = \"$3\";/" \
          -e "s/var slice_sd = .*/var slice_sd = \"$4\";/" \
          "$PROJECT_DIR/5Gcore_files/register-ues-job.yaml" >  "$arq_registro"    # cria uma cópia do job com os valores desta fatia

      kubectl apply -f "$arq_registro"                   # grava os assinantes no MongoDB
      kubectl wait --for=delete job/register-ues-job -n "$NS" --timeout=180s

    done
    check_subscribers

    echo Subindo servidor de video
    kubectl apply -f "$PROJECT_DIR/client_scripts/dash-server.yaml"          # nginx com o Big Buck Bunny, no nó blacksabbath
    check_pods
    
    for f in "1 000001 slice01" "3 000002 slice02" "5 000003 slice03"; do
      set -- $f                                                # separa: número do UE, SD e DNN
      arq_video="$TMP_DIR/video-$3.sh"
      sed -e "s/^START_ID=.*/START_ID=$1/" -e "s/^SLICE_SD=.*/SLICE_SD=\"$2\"/" -e "s/^DNN=.*/DNN=\"$3\"/" \
          "$PROJECT_DIR/client_scripts/videoclients-script.sh" > "$arq_video"   # cria uma cópia do script com os valores deste UE
      bash "$arq_video"                                    # cria o ConfigMap e o Deployment do UE
    done
    check_pods             # os três têm de ficar Running, com 3/3 containers prontos
}

main "$@"


