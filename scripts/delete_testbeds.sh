#!/bin/bash
# Apaga tudo que o subir_testbed.sh criou no namespace, na ordem inversa da subida.
#
# Uso:
#   ./derrubar_testbed.sh                 apaga tudo, inclusive o disco do MongoDB (os assinantes se perdem)
#   ./derrubar_testbed.sh --manter-banco  apaga tudo, mas mantém o disco do MongoDB (os assinantes continuam lá na próxima subida)
#   ./derrubar_testbed.sh -y              não pede confirmação (as duas opções podem ser combinadas)

set -euo pipefail

PROJECT_DIR="/home/pfsevaldt/dev/projeto-5g"
NS=pfsevaldt
MANTER_BANCO=false
CONFIRMAR=true

for arg in "$@"; do
    case "$arg" in
        --manter-banco) MANTER_BANCO=true ;;
        -y|--yes)       CONFIRMAR=false ;;
        *) echo "Opção desconhecida: $arg"; exit 1 ;;
    esac
done

# Apaga todos os recursos de um tipo cujo nome começa com um prefixo (ex.: deployments "ue-video-")
delete_by_prefix() {
    local tipo=$1 prefixo=$2 nomes
    nomes=$(kubectl get "$tipo" -n "$NS" -o name | grep "/$prefixo" || true)   # || true: grep sem resultado não pode parar o script
    if [[ -n "$nomes" ]]; then
        # shellcheck disable=SC2086   # aqui queremos mesmo separar os nomes em argumentos
        kubectl delete -n "$NS" $nomes --ignore-not-found
    fi
}

wait_pods_gone() {
    echo "Aguardando os pods terminarem..."
    kubectl wait --for=delete pod --all -n "$NS" --timeout=180s 2>/dev/null || true   # sem pods, o wait reclama; tudo bem
    kubectl get pods -n "$NS"
}

main() {
    if $CONFIRMAR; then
        echo "Isto vai apagar o testbed do namespace $NS."
        $MANTER_BANCO && echo "(o disco do MongoDB será mantido)" || echo "(INCLUSIVE o disco do MongoDB: os assinantes se perdem)"
        read -r -p "Continuar? [s/N] " resp
        [[ "$resp" == "s" || "$resp" == "S" ]] || { echo "Cancelado."; exit 0; }
    fi

    echo "Apagando clientes de vídeo (e de iperf, se houver)"
    delete_by_prefix deployment ue-video-
    delete_by_prefix configmap  config-video-
    delete_by_prefix deployment ue-iperf-
    delete_by_prefix configmap  config-iperf-

    echo "Apagando servidor de vídeo"
    kubectl delete -f "$PROJECT_DIR/client_scripts/dash-server.yaml" --ignore-not-found

    echo "Apagando job e script de registro de assinantes"
    kubectl delete job/register-ues-job configmap/ue-registration-script -n "$NS" --ignore-not-found

    echo "Apagando core 5G e webui"
    kubectl delete -n "$NS" -f "$PROJECT_DIR/5Gcore_files/webui.yaml" --ignore-not-found
    kubectl delete -n "$NS" -f "$PROJECT_DIR/5Gcore_files/recent-op5g.yaml" --ignore-not-found

    echo "Apagando UPFs"
    for i in $(seq 1 10); do
        kubectl delete -n "$NS" deployment/upf$i service/upf$i service/upf$i-gtp configmap/upf$i --ignore-not-found
        kubectl delete -n "$NS" servicemonitor/upf$i --ignore-not-found 2>/dev/null || true   # pode não haver permissão ou CRD; não é motivo para parar
    done

    echo "Apagando banco de assinantes"
    if $MANTER_BANCO; then
        kubectl delete -n "$NS" deployment/mongodb deployment/dbctl service/mongo --ignore-not-found   # tudo menos o PVC
    else
        kubectl delete -n "$NS" -f "$PROJECT_DIR/5Gcore_files/recent-database.yaml" --ignore-not-found   # inclui o PVC: os dados somem
    fi

    wait_pods_gone
    echo
    echo "Sobrou no namespace (deve estar vazio, fora o configmap kube-root-ca.crt, que o Kubernetes recria sozinho):"
}

main
