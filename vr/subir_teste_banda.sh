#!/bin/bash
# =========================================================================
# Fase 1.2: sobe o que o teste de banda precisa, na slice04 (upf4), sem
# tocar nas fatias de vídeo. Reaproveita os scripts do repositório com sed,
# do mesmo jeito que o scripts/build_testbeds.sh.
#   - assinante IMSI 08 na slice04 (SD 000004)
#   - servidor iperf01 no blacksabbath (mesmo nó do dash-server)
#   - UE de iperf ue-iperf-08 na slice04, com rota para o iperf01 pelo túnel
#
# Uso (da raiz do repositório):
#   bash vr/subir_teste_banda.sh             sobe tudo
#   bash vr/subir_teste_banda.sh --remover   apaga o UE e o servidor (o assinante fica)
# =========================================================================
set -euo pipefail

NS=pfsevaldt
REPO=$(cd "$(dirname "$0")/.." && pwd)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

IMSI_SUFIXO=8
SD=000004
DNN=slice04
UE=ue-iperf-08

if [[ "${1:-}" == "--remover" ]]; then
    kubectl delete -n $NS deployment/$UE configmap/config-iperf-08 --ignore-not-found   # UE de iperf
    kubectl delete -n $NS deployment/iperf01 service/iperf01 --ignore-not-found         # servidor iperf
    exit 0
fi

echo "1/3 Assinante IMSI 0$IMSI_SUFIXO na $DNN"
sed -e "s/var start_imsi_suffix = .*/var start_imsi_suffix = $IMSI_SUFIXO;/" \
    -e "s/var end_imsi_suffix = .*/var end_imsi_suffix = $IMSI_SUFIXO;/" \
    -e "s/var slice_dnn = .*/var slice_dnn = \"$DNN\";/" \
    -e "s/var slice_sd = .*/var slice_sd = \"$SD\";/" \
    "$REPO/5Gcore_files/register-ues-job.yaml" > "$TMP/registro.yaml"      # cópia do job só com este IMSI
kubectl apply -f "$TMP/registro.yaml"                                      # grava o assinante no MongoDB
kubectl wait --for=delete job/register-ues-job -n $NS --timeout=180s       # o job se apaga 5 s depois de terminar

echo "2/3 Servidor iperf01"
sed -e "s/^COUNT=.*/COUNT=1/" "$REPO/client_scripts/deploy-iperf_servers.sh" > "$TMP/iperf-servidor.sh"   # só o iperf01
bash "$TMP/iperf-servidor.sh"                                              # cria o Service e o Deployment
kubectl rollout status deployment/iperf01 -n $NS --timeout=180s            # espera o servidor ficar pronto

echo "3/3 UE de iperf na $DNN"
sed -e "s/^SLICE_SD=.*/SLICE_SD=\"$SD\"/" -e "s/^DNN=.*/DNN=\"$DNN\"/" \
    -e "s/^START_ID=.*/START_ID=$IMSI_SUFIXO/" -e "s/^TARGET_SERVICE=.*/TARGET_SERVICE=\"iperf01\"/" \
    "$REPO/client_scripts/iperfclients-script.sh" > "$TMP/iperf-ue.sh"     # cópia com a fatia e o alvo deste teste
bash "$TMP/iperf-ue.sh"                                                    # cria o ConfigMap e o Deployment do UE
kubectl rollout status deployment/$UE -n $NS --timeout=180s                # espera os 3 containers ficarem prontos

echo "Aguardando o túnel uesimtun0 (até 60 s)"
for _ in $(seq 60); do
    UE_IP=$(kubectl exec -n $NS deploy/$UE -c ue -- ip -4 -o addr show uesimtun0 2>/dev/null | awk '{print $4}' | cut -d/ -f1) || true   # IP do UE, sem o "/16"
    [[ -n "$UE_IP" ]] && break
    sleep 1
done
[[ -n "${UE_IP:-}" ]] || { echo "[ERRO] o túnel não subiu: veja kubectl logs -n $NS deploy/$UE -c ue"; exit 1; }

IPERF_IP=$(kubectl get svc iperf01 -n $NS -o jsonpath='{.spec.clusterIP}')                                  # IP do servidor
echo "UE com IP $UE_IP. Rota até o iperf01 ($IPERF_IP):"
kubectl exec -n $NS deploy/$UE -c ue -- ip route get "$IPERF_IP" from "$UE_IP"                              # tem de dizer "dev uesimtun0"
