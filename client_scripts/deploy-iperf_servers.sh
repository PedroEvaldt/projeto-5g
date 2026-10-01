#!/bin/bash

# ================= CONFIGURAÇÕES =================
NAMESPACE="pfsevaldt"
NODE_NAME="blacksabbath"

# A partir de qual número de servidor começar?
START_ID=1
# Quantos servidores iperf criar no total?
COUNT=8
# =================================================

echo "--- Iniciando o Deploy Automático de Servidores iPerf ---"

for (( i=0; i<$COUNT; i++ ))
do
    # Calcula o número atual e formata com dois dígitos (ex: 1 vira "01")
    CURRENT_ID=$(($START_ID + $i))
    SUFFIX=$(printf "%02d" $CURRENT_ID)
    
    # Nome padrão do app (ex: iperf01)
    APP_NAME="iperf${SUFFIX}"
    
    echo ">>> Criando Servidor: $APP_NAME no nó $NODE_NAME"

    # Gera o YAML e aplica diretamente no cluster
    cat <<EOF | kubectl apply -f -
---
apiVersion: v1
kind: Service
metadata:
  name: ${APP_NAME}
  namespace: ${NAMESPACE}
  labels:
    app: ${APP_NAME}
spec:
  selector:
    app: ${APP_NAME}
  ports:
    - name: tcp-5201
      protocol: TCP
      port: 5201
      targetPort: 5201
    - name: udp-5201
      protocol: UDP
      port: 5201
      targetPort: 5201
---    
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ${APP_NAME}
  namespace: ${NAMESPACE}
  labels:
    app: ${APP_NAME}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: ${APP_NAME}
  template:
    metadata:   
      labels:
        app: ${APP_NAME}
    spec:        
      nodeSelector:
        kubernetes.io/hostname: ${NODE_NAME}   
      containers:       
        - name: iperf-tcp
          image: maikovisky/iperf:latest
          ports:
            - containerPort: 5201
              protocol: TCP 
            - containerPort: 5201
              protocol: UDP
          command: ["/bin/iperf3", "-s"]
EOF

done

echo "--- Deploy de Servidores iPerf concluído! ---"