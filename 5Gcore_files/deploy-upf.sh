#!/bin/bash

# ================= CONFIGURAÇÕES =================
NAMESPACE="pfsevaldt"
NODE_NAME="wb-fibre-01"

# A partir de qual número de UPF começar?
START_ID=1
# Quantos UPFs criar no total?
COUNT=10
# =================================================

echo "--- Iniciando o Deploy Automático de UPFs ---"

for (( i=0; i<$COUNT; i++ ))
do
    # Calcula o número atual do UPF
    UPF_NUM=$(($START_ID + $i))
    
    # Formata o número do slice para ter sempre 2 dígitos (ex: 4 vira 04)
    SLICE_ID=$(printf "%02d" $UPF_NUM)
    
    # Calcula o terceiro octeto da sub-rede (16 + número do UPF)
    SUBNET_OCTET=$((16 + $UPF_NUM))
    
    # Define os nomes que serão usados no YAML
    UPF_NAME="upf${UPF_NUM}"
    SLICE_NAME="slice${SLICE_ID}"
    SUBNET_BASE="172.${SUBNET_OCTET}.0"
    
    echo ">>> Criando $UPF_NAME | Interface: $SLICE_NAME | Sub-rede: $SUBNET_BASE.0/16"

    # Gera o YAML e aplica diretamente no cluster
    cat <<EOF | kubectl apply -f -
---
apiVersion: v1
kind: Service
metadata:
  name: ${UPF_NAME}-gtp
  namespace: ${NAMESPACE}
  labels:
    app: ${UPF_NAME}
spec:
  clusterIP: None
  selector:
    app: ${UPF_NAME}
  ports:
    - name: udp-2152
      protocol: UDP
      port: 2152
      targetPort: 2152
---
apiVersion: v1
kind: Service
metadata:
  name: ${UPF_NAME}
  namespace: ${NAMESPACE}
  labels:
    app: ${UPF_NAME}
spec:
  selector:
    app: ${UPF_NAME}
  ports:
    - name: http2-8080
      protocol: TCP
      port: 8080
      targetPort: 8080
    - name: udp-2152
      protocol: UDP
      port: 2152
      targetPort: 2152
    - name: udp-8805
      protocol: UDP
      port: 8805
      targetPort: 8805
    - name: tcp-9090
      protocol: TCP
      port: 9090
      targetPort: 9090
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ${UPF_NAME}
  namespace: ${NAMESPACE}
  labels:
    app: ${UPF_NAME}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: ${UPF_NAME}
  template:
    metadata:
      labels:
        app: ${UPF_NAME}
        version: 2.7.6
      annotations:
         qos.projectcalico.org/ingressBandwidth: "15M"
         qos.projectcalico.org/egressBandwidth: "15M"
    spec:
      nodeSelector:
        kubernetes.io/hostname: ${NODE_NAME}
      containers:
        - name: ${UPF_NAME}
          image: gradiant/open5gs:2.7.6
          resizePolicy:
          - resourceName: cpu
            restartPolicy: NotRequired # Default, but explicit here
          - resourceName: memory
            restartPolicy: NotRequired
          resources:
            limits:
              cpu: "150m"
              memory: "256Mi"
            requests:
              cpu: "100m"
              memory: "128Mi"
          env:
            - name: POD_IP
              valueFrom:
                fieldRef:
                  fieldPath: status.podIP
          ports:
            - name: http2-8080
              protocol: TCP
              containerPort: 8080
            - name: udp-2152
              protocol: UDP
              containerPort: 2152
            - name: udp-8805
              protocol: UDP
              containerPort: 8805
            - name: tcp-9090
              protocol: TCP
              containerPort: 9090
          securityContext:
            privileged: true
            runAsUser: 0
            capabilities:
              add: ["NET_ADMIN", "SYS_MODULE"]
          volumeMounts:
            - name: ${UPF_NAME}-config
              mountPath: /open5gs/config-map/upf.yaml
              subPath: "upf.yaml"
            - name: "dev-net-tun"
              mountPath: "/dev/net/tun"
            - name: ${UPF_NAME}-config
              mountPath: /bin/entrypoint.sh
              subPath: entrypoint.sh
          command: ["/bin/entrypoint.sh"]
      volumes:
        - name: ${UPF_NAME}-config
          configMap:
            name: ${UPF_NAME}
            defaultMode: 0777
        - name: dev-net-tun
          hostPath:
            path: /dev/net/tun
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: ${UPF_NAME}
  namespace: ${NAMESPACE}
  labels:
    app: ${UPF_NAME}
data:
  upf.yaml: |-
    logger:
      level: info
      path:
        file: /opt/open5gs/var/log/open5gs/upf.log

    global:
      max:
        ue: 5000

    sbi:
      server:
        no_tls: true
      client:
        no_tls: true
    upf:
      pfcp:
        server:
          - dev: eth0
            advertise: ${UPF_NAME}
        client:
      gtpu:
        server:
          - dev: eth0
            advertise: ${UPF_NAME}
      metrics:
        server:
          - dev: eth0
            port: 9090
      session:
        - subnet: 172.16.0.0/16
          gateway: 172.16.0.1
          dnn: internet
          dev: ogstun
        - subnet: ${SUBNET_BASE}.0/16
          gateway: ${SUBNET_BASE}.1
          dnn: ${SLICE_NAME}
          dev: ${SLICE_NAME}

  entrypoint.sh: |-
    #!/bin/bash
    set -e

    echo "Executing k8s customized entrypoint.sh"
    if grep "ogstun" /proc/net/dev > /dev/null; then
      echo "[WARNING] Net device ogstun already exists!"
      ip addr add 172.16.0.1/16 dev ogstun || true
      ip link set ogstun up
    else 
      echo "[INFO] Create device ogstun!"
      ip tuntap add name ogstun mode tun
      ip addr add 172.16.0.1/16 dev ogstun || true
      ip link set ogstun up
    fi

    if grep "${SLICE_NAME}" /proc/net/dev > /dev/null; then
      echo "[WARNING] Net device ${SLICE_NAME} already exists!"
      ip addr add ${SUBNET_BASE}.1/16 dev ${SLICE_NAME} || true
      ip link set ${SLICE_NAME} up
    else 
      echo "[INFO] Create device ${SLICE_NAME} !"
      ip tuntap add name ${SLICE_NAME} mode tun
      ip addr add ${SUBNET_BASE}.1/16 dev ${SLICE_NAME} || true
      ip link set ${SLICE_NAME} up
    fi

    echo "[INFO] Config sysctl"
    sysctl -w net.ipv4.ip_forward=1
    sysctl -w net.ipv6.conf.all.forwarding=1

    echo "Enable NAT"
    iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE

    open5gs-upfd -c /open5gs/config-map/upf.yaml

---
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: ${UPF_NAME}
  namespace: ${NAMESPACE}
  labels:
    app: ${UPF_NAME}
spec:
  selector:
    matchLabels:
      app: ${UPF_NAME}
  endpoints:
  - interval: 5s
    port: tcp-9090
EOF

done
echo "--- Deploy concluído! ---"
