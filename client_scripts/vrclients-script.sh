#!/bin/bash
# Clientes de VR (PhotonPath) na slice04 (upf4). Cópia do videoclients-script.sh:
# os containers gnb e ue são os mesmos; o terceiro container é o vr-client, que roda o vr/vr_exporter.py.
# Precisa dos ConfigMaps "photonpath" e "vr-launcher" (ver client_scripts/vr-server.yaml),
# do ConfigMap "vr-exporter" e do assinante registrado na slice04:
#   kubectl create configmap vr-exporter -n pfsevaldt --from-file=vr/vr_exporter.py   # cria o ConfigMap com o exporter

# ================= CONFIGURAÇÕES =================
# Namespace do Kubernetes
NAMESPACE="pfsevaldt"
NODE_NAME="blacksabbath"
# Configurações do Slice de VR (Slice 04)
SLICE_SST=1
SLICE_SD="000004"
DNN="slice04"

# Intervalo de IMSIs
# IMSI 07 = VR (01-06 são do vídeo, 08 é o UE de iperf de teste)
START_ID=7
# Quantidade de clientes a criar
COUNT=1

# Imagem do cliente de VR: os scripts vêm dos ConfigMaps, não precisa de imagem própria
APP_IMAGE="python:3.12-slim"
# =================================================

echo "--- Iniciando Deploy de Clientes de VR ${DNN} ---"

for (( i=0; i<$COUNT; i++ ))
do
    # Calcula o ID atual e o sufixo (ex: 3 -> "03")
    CURRENT_ID=$(($START_ID + $i))
    SUFFIX=$(printf "%02d" $CURRENT_ID)
    
    # Monta o IMSI completo
    IMSI="9997000000000${SUFFIX}"
    
    # Nomes dos recursos K8s
    POD_NAME="ue-vr-${SUFFIX}"
    CM_NAME="config-vr-${SUFFIX}"

    echo ">>> Criando Cliente: $POD_NAME | IMSI: $IMSI | Slice: $SLICE_SD"

    # Gera o YAML e aplica diretamente no Cluster
    cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: ConfigMap
metadata:
  name: ${CM_NAME}
  namespace: ${NAMESPACE}
data:
  # Configuração da Torre (gNB)
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

  # Configuração do Chip (UE)
  ue.yaml: |
    supi: 'imsi-${IMSI}'
    mcc: '999'
    mnc: '70'
    # SUCI Protection Scheme : 0 for Null-scheme, 1 for Profile A and 2 for Profile B
    protectionScheme: 0
    # Home Network Public Key for protecting with SUCI Profile A
    homeNetworkPublicKey: '5a8d38864820197c3394b92613b20b91633cbd897119273bf8e4a6f4eec0a650'
    # Home Network Public Key ID for protecting with SUCI Profile A
    homeNetworkPublicKeyId: 1
    # Routing Indicator
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
        # Scripts do PhotonPath e o lançador do buffer forçado (os mesmos do vr-app-01)
        - name: photonpath
          configMap:
            name: photonpath
        - name: vr-launcher
          configMap:
            name: vr-launcher
        # O exporter, que roda o cliente em janelas e publica as métricas
        - name: vr-exporter
          configMap:
            name: vr-exporter
      containers:
        # 1. Container da Torre (gNB)
        - name: gnb
          image: free5gc/ueransim:latest
          imagePullPolicy: IfNotPresent
          securityContext:
            capabilities:
              add: ["NET_ADMIN"]
          env:
            - name: MY_POD_IP
              valueFrom:
                fieldRef:
                  fieldPath: status.podIP
          # Configura o IP do gNB dinamicamente na inicialização
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
        
        # 2. Container do Celular (UE)
        - name: ue
          image: free5gc/ueransim:latest
          imagePullPolicy: IfNotPresent
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

        # 3. Container do Cliente de VR (Aplicação)
        # O vr_exporter.py roda o photon_client em janelas de 12 s, escreve uma linha por
        # janela no log e publica a última em :9200/metrics. Pausa e retomada com
        # kubectl exec ... -c vr-client -- sh -c 'kill -USR1 1' (pausa) / 'kill -USR2 1' (retoma);
        # o kill é o do sh, porque a imagem slim não tem o programa kill.
        # Sem rota manual: o cliente usa --bind com o IP do uesimtun0, e a regra
        # de origem que o UERANSIM cria manda esse tráfego pelo túnel.
        - name: vr-client
          image: ${APP_IMAGE}
          imagePullPolicy: IfNotPresent
          # NET_ADMIN libera o SO_RCVBUFFORCE do lançador (buffer acima do rmem_max do nó)
          securityContext:
            capabilities:
              add: ["NET_ADMIN"]
          # Forma de lista, sem shell: o python é o PID 1 e recebe os sinais direto
          command: ["python3", "-u", "/opt/vr-exporter/vr_exporter.py"]
          env:
            # Rótulo das métricas: a fatia deste script
            - name: VR_SLICE
              value: "${DNN}"
          ports:
            - name: metrics
              containerPort: 9200
          volumeMounts:
            - name: photonpath
              mountPath: /opt/photonpath
              readOnly: true
            - name: vr-launcher
              mountPath: /opt/vr
              readOnly: true
            - name: vr-exporter
              mountPath: /opt/vr-exporter
              readOnly: true
EOF

done

echo "--- Concluído. Verifique os pods com: kubectl get pods -n ${NAMESPACE} ---"
