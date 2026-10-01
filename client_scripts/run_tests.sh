#!/bin/bash
# Script para rodar os testes de desempenho (iperf) e vídeo (dash-client)
# Configurações
NAMESPACE="pfsevaldt"
TIMEOUT=450 # Tempo máximo para os pods ficarem prontos (em segundos)
# Pega nome do teste da entrada do usuário
read -p "Digite o nome do teste (ex: teste1): " TEST_NAME
echo "Iniciando o teste: $TEST_NAME"
# Roda o teste 5 vezes
for i in {1..5}
do
    echo "=== Iniciando Teste $i ==="
    # Lança os ataques de UDP Flood usando iPerf
    ./run-iperfclients.sh
    # Esperar 60 segundos para o ataque se estabilizar
    echo "Aguardando 60 segundos para estabilizar o ataque..."
    sleep 120
    # Reinicia os clientes de vídeo para coletar métricas durante o ataque (ue-video01, ue-video02, ...)
    kubectl delete pods -n $NAMESPACE $(kubectl get pods -n $NAMESPACE -o name | grep ue-video)
    echo "Clientes de vídeo reiniciados para coletar métricas durante o ataque!"
    # Aguarda os pods de vídeo ficarem prontos
    echo "Aguardando os clientes de vídeo ficarem prontos... (Timeout: ${TIMEOUT}s)"
    sleep $TIMEOUT
    # Coleta as métricas dos clientes de vídeo
    # Junta o TEST_NAME e o número do teste para criar um nome único para os arquivos de métricas
    METRICS_FILE="${TEST_NAME}_${i}"
    echo "Coletando métricas dos clientes de vídeo para o teste $i..."
    python3 analise_metricas.py $METRICS_FILE
    echo "Métricas coletadas e salvas em: ${METRICS_FILE}.csv"
    echo "=== Teste $i Finalizado ==="
    sleep 50
done
echo "Todos os testes finalizados! Métricas salvas com o prefixo: ${TEST_NAME}_*.csv"


