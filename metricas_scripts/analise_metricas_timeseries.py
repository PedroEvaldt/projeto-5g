#!/usr/bin/env python3
"""
Coleta de Métricas em Time-Series para 5G Network Slicing
==========================================================
Coleta bitrate, buffer, stalls a cada 5s durante o teste e salva em CSV.
Permite correlação com eventos de orquestração (mudanças de prioridade, pausa/resumo).
"""

import subprocess
import re
import pandas as pd
import json
import sys
import time
import threading
import os
import yaml
from datetime import datetime
from pathlib import Path
from collections import defaultdict
from dotenv import load_dotenv

load_dotenv()
TEST_CONFIG_PATH = os.getenv("TEST_FILE")
with open(TEST_CONFIG_PATH, "r") as f:
    test_config = yaml.safe_load(f)

class QoETimeSeries:
    def __init__(self, namespace="pfsevaldt", output_dir=test_config["monitoring"]["csv_output_dir"]):
        self.namespace = namespace
        self.output_dir = output_dir
        self.pod_prefix = "ue-video-"
        self.container_name = "dash-client"
        self.timeseries_file = f"{output_dir}{test_config['monitoring']['timeseries_log_file']}"
        self.test_start_time = None
        self.pod_data = defaultdict(dict)
        self.latest_positions = defaultdict(int)  # Rastreia última linha lida de cada pod
        
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        self.setup_timeseries_file()
    
    def setup_timeseries_file(self):
        """Cria o CSV de time-series com cabeçalho."""
        with open(self.timeseries_file, 'w') as f:
            f.write("Timestamp_Offset_Seconds,Pod_Name,Slice,Bitrate_kbps,Buffer_Seconds,Frames_Total,Quality_Switches,Stalls,Status\n")
        print(f"[INIT] Time-series file criado em {self.timeseries_file}")
    
    def get_pod_list(self):
        """Busca lista de pods de vídeo."""
        try:
            cmd = ["kubectl", "get", "pods", "-n", self.namespace, "-o", "name"]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            pods = [p.strip().replace("pod/", "") for p in result.stdout.splitlines()]
            return [p for p in pods if p.startswith(self.pod_prefix)]
        except Exception as e:
            print(f"[ERRO] Falha ao listar pods: {e}")
            return []
    
    def parse_pod_logs_incremental(self, pod_name):
        """
        Lê apenas as novas linhas do log desde a última leitura.
        Retorna lista de dicts com dados extraídos.
        """
        try:
            cmd = ["kubectl", "logs", pod_name, "-n", self.namespace, "-c", self.container_name]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            logs = result.stdout
        except Exception as e:
            return []
        
        lines = logs.splitlines()
        new_data = []
        last_known_pos = self.latest_positions.get(pod_name, 0)
        
        regex_status = r"\[T\+([\d\.]+)s\] \[STATUS 5s\] Frames: (\d+) \| Buffer: ([\d\.]+)s \| Bitrate: (.*)"
        
        # Inicializa acumuladores
        if pod_name not in self.pod_data:
            self.pod_data[pod_name] = {
                "switches": 0,
                "stalls": 0,
                "last_bitrate": 0,
                "last_buffer": 0,
                "last_frames": 0,
                "errors": 0
            }
        
        # Processa apenas linhas novas
        for i, line in enumerate(lines[last_known_pos:], start=last_known_pos):
            # Contar trocas de qualidade (acumulativo)
            if "Qualidade alterada para indice" in line:
                self.pod_data[pod_name]["switches"] += 1
            
            # Contar erros
            if "ERRO PLAYER" in line:
                self.pod_data[pod_name]["errors"] += 1
            
            # Extrair status
            if "[STATUS 5s]" in line:
                match = re.search(regex_status, line)
                if match:
                    time_str, frames, buffer, bitrate_str = match.groups()
                    
                    data_point = {
                        "time": float(time_str),
                        "frames": int(frames),
                        "buffer": float(buffer),
                        "bitrate": 0,
                        "switches": self.pod_data[pod_name]["switches"],
                        "stalls": self.pod_data[pod_name]["stalls"],
                        "errors": self.pod_data[pod_name]["errors"]
                    }
                    
                    # Parse bitrate
                    if "kbps" in bitrate_str:
                        try:
                            data_point["bitrate"] = float(bitrate_str.replace("kbps", "").strip())
                        except:
                            pass
                    
                    # Detectar stall: Buffer muito baixo E os frames pararam de subir
                    frames_estagnados = (data_point["frames"] == self.pod_data[pod_name]["last_frames"])
                    
                    if data_point["buffer"] <= 0.5 and frames_estagnados and data_point["time"] > 10.0 and data_point["bitrate"] > 0:
                        self.pod_data[pod_name]["stalls"] += 1
                        data_point["stalls"] += 1
                    
                    new_data.append(data_point)
                    self.pod_data[pod_name]["last_bitrate"] = data_point["bitrate"]
                    self.pod_data[pod_name]["last_buffer"] = data_point["buffer"]
                    self.pod_data[pod_name]["last_frames"] = data_point["frames"]
        
        self.latest_positions[pod_name] = len(lines)
        return new_data
    
    def collect_and_save(self):
        """Coleta dados de todos os pods e salva no CSV."""
        if not self.test_start_time:
            self.test_start_time = datetime.now()
        
        pods = self.get_pod_list()
        
        if not pods:
            print("[WARN] Nenhum pod DASH encontrado")
            return
        
        rows = []
        
        for pod in pods:
            # Extrai o nome do slice do pod (ue-video-XX)
            pod_parts = pod.split('-')
            slice_suffix = pod_parts[-3]  # "01", "02", "03"
            # build slice map from config
            slice_map = {v['client_pod'].split('-')[-1]: k for k, v in test_config.get('slices', {}).items()}
            slice_name = slice_map.get(slice_suffix, f"unknown-{slice_suffix}")
            
            # Coleta dados novos
            data_points = self.parse_pod_logs_incremental(pod)
            
            for dp in data_points:
                offset = dp['time']
                if dp['bitrate'] == 0:
                    status = "PAUSED"
                else:
                    status = "OK" if self.pod_data[pod]["errors"] == 0 else "ERROR"
                
                rows.append({
                    "Timestamp_Offset_Seconds": f"{offset:.1f}",
                    "Pod_Name": pod,
                    "Slice": slice_name,
                    "Bitrate_kbps": f"{dp['bitrate']:.0f}",
                    "Buffer_Seconds": f"{dp['buffer']:.1f}",
                    "Frames_Total": dp["frames"],
                    "Quality_Switches": dp["switches"],
                    "Stalls": dp["stalls"],
                    "Status": status
                })
        
        # Append ao CSV
        if rows:
            with open(self.timeseries_file, 'a') as f:
                for row in rows:
                    f.write(f"{row['Timestamp_Offset_Seconds']},{row['Pod_Name']},{row['Slice']},{row['Bitrate_kbps']},{row['Buffer_Seconds']},{row['Frames_Total']},{row['Quality_Switches']},{row['Stalls']},{row['Status']}\n")
    
    def run_continuous(self, interval_seconds=5, duration_seconds=900):
        """Coleta contínua de métricas durante o teste."""
        print(f"\n[INIT] Iniciando coleta contínua a cada {interval_seconds}s...")
        print(f"[INIT] Duração do teste: {duration_seconds}s\n")
        
        self.test_start_time = datetime.now()
        elapsed = 0
        
        try:
            while elapsed < duration_seconds:
                self.collect_and_save()
                time.sleep(interval_seconds)
                elapsed = (datetime.now() - self.test_start_time).total_seconds()
        
        except KeyboardInterrupt:
            print("\n[INTERROMPIDO] Coleta finalizada pelo usuário")
        
        print(f"\n[SUCESSO] Coleta finalizada. Dados salvos em {self.timeseries_file}")

if __name__ == "__main__":
    interval = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    duration = int(sys.argv[2]) if len(sys.argv) > 2 else 900
    
    collector = QoETimeSeries()
    collector.run_continuous(interval_seconds=interval, duration_seconds=duration)
