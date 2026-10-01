#!/bin/bash

# =========================================================================
# Script de Automação do Teste de 5G Network Slicing (MAS)
# Abre múltiplos terminais na sequência e com os atrasos corretos.
# =========================================================================

echo "🚀 Iniciando o Ecossistema de Testes..."

# Função auxiliar para abrir um novo terminal e executar um comando
run_in_terminal() {
    local title=$1
    local dir=$2
    local cmd=$3
    
    # Ele abre o terminal, ativa o venv, entra na pasta e roda o script
    gnome-terminal --tab --title="$title" -- bash -c "source venv/bin/activate && cd $dir && $cmd; exec bash"
}

# 1. Resetar recursos das UPFs (Garante que a CPU está limpa antes do teste)
echo "1️⃣ Resetando recursos das UPFs..."
run_in_terminal "UPF Reset" "./spade_agents" "python3 upf_set_resources.py"
sleep 6

# 2. Iniciar os Slice Agents (Os peões do leilão)
echo "2️⃣ Iniciando Slice Agents..."
run_in_terminal "Slice Agents" "./spade_agents" "python3 slice_agents.py"
sleep 6

# 3. Iniciar o Strategist Agent (A Inteligência do LLM)
echo "3️⃣ Iniciando Strategist Agent (LLM)..."
run_in_terminal "Strategist" "./spade_agents" "python3 strategist_agent.py"

# 4. Iniciar o Resource Agent (O Leiloeiro)
echo "4️⃣ Iniciando Resource Agent (Leiloeiro)..."
run_in_terminal "Resource Agent" "./spade_agents" "python3 resource_agent.py"
sleep 1

# 5. Reiniciar os Clientes de Vídeo (Clean State via Rollout)
echo "5️⃣ Executando rollout nos clientes ue-video no Kubernetes..."
# Assumindo que os seus clientes foram criados via Deployment. 
# Se foram criados via StatefulSet ou Job, altere a palavra 'deployment' abaixo.
run_in_terminal "Rollout DASH" "./" "kubectl rollout restart deployment/ue-video-01 deployment/ue-video-03 deployment/ue-video-05 -n pfsevaldt"

echo "⏳ Aguardando 20 segundos para os vídeos inicializarem no cluster..."
sleep 20

# 5. Iniciar a Orquestração e a Coleta (Acontecendo simultaneamente)
echo "5️⃣ Iniciando Orquestrador e Sensores..."
run_in_terminal "Orquestrador" "./metricas_scripts" "python3 deploy_test.py"
sleep 40
run_in_terminal "Coletor QoE" "./metricas_scripts" "python3 analise_metricas_timeseries.py 5 860"



echo "✅ Todos os componentes foram iniciados com sucesso!"
echo "⚠️ Atenção: Quando o teste acabar (15 minutos), feche as janelas dos agentes Python."