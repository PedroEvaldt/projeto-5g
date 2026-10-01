#!/bin/bash

# ================= CONFIGURAÇÕES =================
NAMESPACE="pfsevaldt"
NODE_NAME="blacksabbath"
# Configurações do Slice de Interferência (Slice 02)
SLICE_SST=1
SLICE_SD="000009"
DNN="slice09"

# Intervalo de IMSIs (Agressores)
# IMPORTANTE: Começa no 7 para não bater com os de vídeo (que presumimos ser 1 a 6)
START_ID=45
COUNT=1

# Usando a MESMA imagem do seu servidor para garantir compatibilidade (iPerf 2)
APP_IMAGE="maikovisky/iperf:latest"
TARGET_SERVICE="iperf09"
TARGET_PORT="5201"
# =================================================

echo "--- Iniciando Deploy de Clientes iPerf3 ---"

for (( i=0; i<$COUNT; i++ ))
do
    CURRENT_ID=$(($START_ID + $i))
    SUFFIX=$(printf "%02d" $CURRENT_ID)
    
    IMSI="9997000000000${SUFFIX}"
    
    POD_NAME="ue-iperf-${SUFFIX}"
    CM_NAME="config-iperf-${SUFFIX}"

    echo ">>> Criando Agressor: $POD_NAME | IMSI: $IMSI | Alvo: $TARGET_SERVICE:$TARGET_PORT"

    cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: ConfigMap
metadata:
  name: ${CM_NAME}
  namespace: ${NAMESPACE}
data:
  gnb.yaml: |
    mcc: '999'
    mnc: '70'
    nci: '0x0000000${SUFFIX}'
    idLength: 32
    tac: 1
    linkIp: '0.0.0.0'
    gtpIp: 'gnb'
    ngapIp: '0.0.0.0'
    name: 'gnb-${SUFFIX}'
    amfConfigs:
      - address: amf
        port: 38412
    slices:
      - sst: ${SLICE_SST}
        sd: '${SLICE_SD}'
    ignoreStreamIds: true
    gnbConnections:
      - name: gnb-n2
        protocol: sctp
        address: 0.0.0.0
        port: 38412
      - name: gnb-n3
        protocol: udp
        address: 0.0.0.0
        port: 2152

  ue.yaml: |
    supi: 'imsi-${IMSI}'
    mcc: '999'
    mnc: '70'
    protectionScheme: 0
    homeNetworkPublicKey: '5a8d38864820197c3394b92613b20b91633cbd897119273bf8e4a6f4eec0a650'
    homeNetworkPublicKeyId: 1
    routingIndicator: '0000'
    key: '465B5CE8B199B49FAA5F0A2EE238A6BC'
    op: 'E8ED289DEBA952E4283B54E88E6183CA'
    opType: 'OPC'
    amf: '8000'
    tunNetmask: '255.255.255.0'
    gnbSearchList:
      - 127.0.0.1
    # UAC Access Identities Configuration
    uacAic:
      mps: false
      mcs: false

    # UAC Access Control Class
    uacAcc:
      normalClass: 0
      class11: false
      class12: false
      class13: false
      class14: false
      class15: false
    sessions:
      - type: 'IPv4'
        apn: '${DNN}'
        dnn: '${DNN}'
        slice:
          sst: ${SLICE_SST}
          sd: ${SLICE_SD}
    configured-nssai:
      - sst: ${SLICE_SST}
        sd: ${SLICE_SD}
    default-nssai:
      - sst: ${SLICE_SST}
        sd: ${SLICE_SD}
    integrity:
      IA1: true
      IA2: true
      IA3: true
    ciphering:
      EA1: true
      EA2: true
      EA3: true
    integrityMaxRate:
      uplink: 'full'
      downlink: 'full'

---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ${POD_NAME}
  namespace: ${NAMESPACE}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: ${POD_NAME}
  template:
    metadata:
      labels:
        app: ${POD_NAME}
      annotations:
        # Sem limite de banda para poder saturar a rede
    spec:
      nodeSelector:
        kubernetes.io/hostname: ${NODE_NAME}
      volumes:
        - name: config-volume
          configMap:
            name: ${CM_NAME}
        - name: dev-net-tun
          hostPath:
            path: /dev/net/tun
      containers:
        # GNB
        - name: gnb
          image: free5gc/ueransim:latest
          securityContext:
            capabilities:
              add: ["NET_ADMIN"]
          env:
            - name: MY_POD_IP
              valueFrom:
                fieldRef:
                  fieldPath: status.podIP
          command: 
            - "/bin/bash"
            - "-c"
            - |
              cp /etc/ueransim/gnb.yaml config/gnb.yaml && \
              sed -i "s/gtpIp: 'gnb'/gtpIp: '\$MY_POD_IP'/g" config/gnb.yaml && \
              ./nr-gnb -c config/gnb.yaml
          volumeMounts:
            - name: config-volume
              mountPath: /etc/ueransim/gnb.yaml
              subPath: gnb.yaml
            - name: dev-net-tun
              mountPath: /dev/net/tun
        
        # UE
        - name: ue
          image: free5gc/ueransim:latest
          securityContext:
            privileged: true
            capabilities:
              add: ["NET_ADMIN"]
          command: ["./nr-ue", "-c", "/etc/ueransim/ue.yaml"]
          volumeMounts:
            - name: config-volume
              mountPath: /etc/ueransim/ue.yaml
              subPath: ue.yaml
            - name: dev-net-tun
              mountPath: /dev/net/tun

        # IPERF CLIENT (Agressor)
        - name: iperf-client
          image: ${APP_IMAGE}
          imagePullPolicy: IfNotPresent
          securityContext:
            capabilities:
              add: ["NET_ADMIN"]
          command: ["/bin/sh", "-c"]
          args:
            - |
              echo "[INIT] Aguardando interface 5G (uesimtun0)..."
              while ! ip link show uesimtun0 > /dev/null 2>&1; do sleep 1; done
              echo "[INIT] Interface uesimtun0 detectada!"
              
              # Configuração de Rota para garantir que o ataque passe pelo 5G
              echo "[INIT] Resolvendo IP do alvo ${TARGET_SERVICE}..."
              SERVER_IP=\$(getent hosts ${TARGET_SERVICE} | awk '{ print \$1 }')
              
              if [ -n "\$SERVER_IP" ]; then
                 echo "[ROTA] Adicionando rota para \$SERVER_IP via uesimtun0"
                 ip route add \$SERVER_IP dev uesimtun0
              else
                 echo "[ERRO] Não foi possível resolver DNS do servidor iperf!"
              fi

              echo "[READY] Pronto para ataque. Entre no pod e rode o comando iperf."
              sleep infinity
EOF

done
echo "--- Agressores criados. ---"