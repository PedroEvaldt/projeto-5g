#!/usr/bin/env python3
"""
Prometheus Infra Fetcher (Otimizado com as Queries Exatas do Grafana)
===============================================================
Conecta ao Prometheus e baixa as métricas exatas de infraestrutura
baseando-se nas queries reais do Grafana (irate + interfaces corretas).
Usa uma janela de 2m para garantir o cálculo do irate e evita falsos zeros.
"""

import pandas as pd
import requests
import os
import sys
import yaml
from datetime import datetime
from dotenv import load_dotenv

# Carrega o arquivo de configuração de teste a partir da variável de ambiente
load_dotenv()
TEST_CONFIG_PATH = os.getenv("TEST_FILE")

if not TEST_CONFIG_PATH or not os.path.exists(TEST_CONFIG_PATH):
    print(f"[ERRO] Variável de ambiente TEST_FILE não definida ou arquivo não encontrado: {TEST_CONFIG_PATH}")
    sys.exit(1)

with open(TEST_CONFIG_PATH, "r") as f:
    test_config = yaml.safe_load(f)

# Extrai os parâmetros dinamicamente do YAML
PROMETHEUS_URL = test_config.get("monitoring", {}).get("prometheus_url", "http://localhost:35235")
if PROMETHEUS_URL.endswith('/'):
    PROMETHEUS_URL = PROMETHEUS_URL[:-1]

DURACAO_TESTE_SEGUNDOS = test_config.get("test_metadata", {}).get("duration_seconds", 900)

# Resolução de exportação dos dados: 30 em 30 segundos como solicitado
STEP = "30s"

# Janela ajustada para 2m no irate. O irate PRECISA de pelo menos 2 coletas. 
# 2m garante matematicamente que o Prometheus tem pontos suficientes para calcular a diferença.
RATE_INTERVAL = "1m" 

# Mapeamento: Precisamos do pod do UE e da UPF para isolar cada slice
SLICE_MAPPING = {}
if "slices" in test_config:
    for slice_name, slice_data in test_config["slices"].items():
        SLICE_MAPPING[slice_name] = {
            "dnn": slice_data.get("dnn"),
            "client_pod": slice_data.get("client_pod"),
            "upf_pod": slice_data.get("upf")
        }

def get_test_time_window(input_dir):
    """Lê o auction_history para descobrir o início e fim exatos do teste em Epoch."""
    auction_file = os.path.join(input_dir, "auction_history.csv")
    if not os.path.exists(auction_file):
        print(f"[ERRO] {auction_file} não encontrado. Execute o script do leilão primeiro.")
        sys.exit(1)
        
    df = pd.read_csv(auction_file)
    df['Timestamp'] = pd.to_datetime(df['Timestamp'])
    
    # --- CORREÇÃO DO FUSO HORÁRIO ---
    # Informa ao Pandas que a hora lida no CSV está no fuso horário local de Portugal
    df['Timestamp'] = df['Timestamp'].dt.tz_localize('Etc/UTC')
    
    start_time_epoch = int(df['Timestamp'].min().timestamp())
    end_time_epoch = start_time_epoch + DURACAO_TESTE_SEGUNDOS
    
    print(f"[INFO] Janela de Teste: {df['Timestamp'].min()} até +{DURACAO_TESTE_SEGUNDOS}s")
    return start_time_epoch, end_time_epoch

def query_prometheus(query, start, end):
    """Faz a requisição HTTP para a API do Prometheus."""
    url = f"{PROMETHEUS_URL}/api/v1/query_range"
    params = {'query': query, 'start': start, 'end': end, 'step': STEP}
    try:
        res = requests.get(url, params=params)
        res.raise_for_status()
        data = res.json()
        if data['status'] == 'success':
            result = data['data']['result']
            if not result:
                print(f"      [AVISO] Query retornou VAZIO: {query}")
            return result
    except Exception as e:
        print(f"      [ERRO] Falha no Prometheus: {e}")
    return []

def extract_timeseries(prom_results, slice_name, metric_name):
    """Converte o formato JSON do Prometheus para uma lista de dicionários padrão."""
    extracted = []
    if not prom_results: 
        return extracted
    
    for timestamp, val in prom_results[0].get('values', []):
        extracted.append({
            'Timestamp_Epoch': float(timestamp),
            'Slice': slice_name,
            metric_name: float(val)
        })
    return extracted

def collect_infra_data(input_dir):
    start_epoch, end_epoch = get_test_time_window(input_dir)
    print(f"\n[PROCESSANDO] Consultando o Prometheus em {PROMETHEUS_URL}...")
    print(f"Usando step de {STEP} e rate_interval de {RATE_INTERVAL}\n")
    
    all_dfs = []
    
    for slice_name, pods in SLICE_MAPPING.items():
        client_pod = pods.get('client_pod')
        upf_pod = pods.get('upf_pod')
        print(f"  -> Extraindo Slice {slice_name.upper()} (UE: {client_pod}, UPF: {upf_pod})")
        
        # ==========================================
        # MÉTRICAS DA UPF (Core 5G) - Baseado no Grafana
        # ==========================================
        if upf_pod:
            # UPF CPU (Cores)
            q_cpu = f'sum(irate(container_cpu_usage_seconds_total{{namespace="pfsevaldt", container=~"{upf_pod}.*"}}[{RATE_INTERVAL}]))'
            cpu_data = extract_timeseries(query_prometheus(q_cpu, start_epoch, end_epoch), slice_name, 'UPF_CPU_Cores')
            if cpu_data: all_dfs.append(pd.DataFrame(cpu_data))
            
            # UPF Pacotes Recebidos (pps)
            q_pktrx = f'sum(irate(container_network_receive_packets_total{{namespace="pfsevaldt", interface=~"{pods.get("dnn")}"}}[{RATE_INTERVAL}]))'
            pktrx_data = extract_timeseries(query_prometheus(q_pktrx, start_epoch, end_epoch), slice_name, 'UPF_Packets_RX')
            if pktrx_data: all_dfs.append(pd.DataFrame(pktrx_data))

            # UPF Pacotes Enviados (pps)
            q_pkttx = f'sum(irate(container_network_transmit_packets_total{{namespace="pfsevaldt", interface=~"{pods.get("dnn")}"}}[{RATE_INTERVAL}]))'
            pkttx_data = extract_timeseries(query_prometheus(q_pkttx, start_epoch, end_epoch), slice_name, 'UPF_Packets_TX')
            if pkttx_data: all_dfs.append(pd.DataFrame(pkttx_data))
            
            # UPF Tráfego Recebido (Kbps)
            q_netrx = f'sum(irate(container_network_receive_bytes_total{{namespace="pfsevaldt", interface=~"{pods.get("dnn")}"}}[{RATE_INTERVAL}])) * 8 / 1000'
            netrx_data = extract_timeseries(query_prometheus(q_netrx, start_epoch, end_epoch), slice_name, 'UPF_RX_Kbps')
            if netrx_data: all_dfs.append(pd.DataFrame(netrx_data))
            
            # UPF Tráfego Enviado (Kbps)
            q_nettx = f'sum(irate(container_network_transmit_bytes_total{{namespace="pfsevaldt", interface=~"{pods.get("dnn")}"}}[{RATE_INTERVAL}])) * 8 / 1000'
            nettx_data = extract_timeseries(query_prometheus(q_nettx, start_epoch, end_epoch), slice_name, 'UPF_TX_Kbps')
            if nettx_data: all_dfs.append(pd.DataFrame(nettx_data))
            
        # ==========================================
        # MÉTRICAS DO UE (Cliente Mobile) - Baseado no Grafana
        # ==========================================
        if client_pod:
            # UE Tráfego Recebido (uesimtun0 - Kbps)
            q_uerx = f'sum(irate(container_network_receive_bytes_total{{namespace="pfsevaldt", pod=~"{client_pod}.*", interface="uesimtun0"}}[{RATE_INTERVAL}])) * 8 / 1000'
            uerx_data = extract_timeseries(query_prometheus(q_uerx, start_epoch, end_epoch), slice_name, 'UE_RX_Kbps')
            if uerx_data: all_dfs.append(pd.DataFrame(uerx_data))
            
            # UE Tráfego Enviado (uesimtun0 - Kbps)
            q_uetx = f'sum(irate(container_network_transmit_bytes_total{{namespace="pfsevaldt", pod=~"{client_pod}.*", interface="uesimtun0"}}[{RATE_INTERVAL}])) * 8 / 1000'
            uetx_data = extract_timeseries(query_prometheus(q_uetx, start_epoch, end_epoch), slice_name, 'UE_TX_Kbps')
            if uetx_data: all_dfs.append(pd.DataFrame(uetx_data))

    if not all_dfs:
        print("\n[AVISO CRÍTICO] Nenhuma métrica foi retornada do Prometheus!")
        print("Certifique-se de que o teste foi rodado recentemente e os dados não expiraram.")
        return

    # Junta tudo e agrupa
    final_df = pd.concat(all_dfs, ignore_index=True)
    final_df = final_df.groupby(['Timestamp_Epoch', 'Slice']).max().reset_index()
    
    # Preenche eventuais buracos de dados com zero para manter a linha do gráfico contínua
    final_df = final_df.fillna(0.0)
    
    # Adiciona a coluna Offset_Seconds para o Google Sheets (0, 30, 60...)
    final_df['Timestamp_Offset_Seconds'] = final_df['Timestamp_Epoch'] - start_epoch
    
    # Reorganiza as colunas
    cols_order = ['Timestamp_Epoch', 'Timestamp_Offset_Seconds', 'Slice']
    metric_cols = [c for c in final_df.columns if c not in cols_order]
    final_df = final_df[cols_order + metric_cols]
    
    output_file = os.path.join(input_dir, "infra_timeseries.csv")
    final_df.to_csv(output_file, index=False)
    print(f"\n[SUCESSO] Dados salvos e perfeitamente agrupados em: {output_file}")

if __name__ == "__main__":
    
    pasta_resultados = sys.argv[1] if len(sys.argv) > 1 else "resultados/teste/"
    
    if len(sys.argv) > 2:
        PROMETHEUS_URL = sys.argv[2]
        if PROMETHEUS_URL.endswith('/'): 
            PROMETHEUS_URL = PROMETHEUS_URL[:-1]
            
    collect_infra_data(pasta_resultados)