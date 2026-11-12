"""
A calibração da VR (SLA e pisos) de uma execução: o vr/calibracao.yaml, das Fases 3 a 8, ou o arquivo que o YAML
dela pede em vr.calibracao (Fase 9: vr/calibracao_render.yaml, com o servidor de VR no nó de GPU). É a mesma
chave que o leiloeiro e os agentes leem, então a análise julga a VR pelo mesmo SLA que o agente usou.
"""
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
PADRAO = "vr/calibracao.yaml"


def caminho(pasta):
    """pasta: a de uma execução (com o config.yaml que rodou) ou a de uma configuração (com as execuções dentro)."""
    pasta = Path(pasta)
    configs = [pasta / "config.yaml"] if (pasta / "config.yaml").exists() else sorted(pasta.glob("*/config.yaml"))
    vr = (yaml.safe_load(open(configs[0])).get("vr") or {}) if configs else {}
    return REPO / vr.get("calibracao", PADRAO)


def ler(pasta):
    with open(caminho(pasta)) as f:
        return yaml.safe_load(f)
