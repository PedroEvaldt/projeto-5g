"""
Fase 7: o prompt do estrategista (LLM) e a validação das respostas, em funções puras. O strategist_agent.py monta o
prompt na subida e passa cada resposta do LLM pelo validar() antes de mandar as prioridades às fatias.
Testes sem LLM em test_politica_llm.py.

Duas versões do prompt (chave vr.llm no YAML):
  - artigo (padrão; L2): o texto do artigo, só com as fatias de vídeo. O LLM não sabe que a VR existe e não pode
    mudar a prioridade dela. Sem VR no YAML, sai o mesmo texto da v1.0-video;
  - vr (L3): o mesmo texto, com todas as fatias e uma seção sobre o tipo de cada uma e o piso da VR.
"""

import json
import math

from pydantic import BaseModel, Field, ValidationError

PRIORIDADE_MIN, PRIORIDADE_MAX = 1.0, 10.0

# O que cada tipo de fatia é, para o prompt da L3 (a fatia pode trocar pelo campo descricao no YAML)
TIPOS = {
    "video": "video streaming (DASH). Elastic: with fewer resources the video quality drops gradually, but it keeps playing.",
    "vr": "virtual reality (VR) stream for real-time teleoperation. Inelastic: below a minimum of resources the stream "
          "freezes and the teleoperation stops.",
}


class SlicePriorityUpdate(BaseModel):
    # Um item da resposta do LLM (vinha do strategist_agent.py, onde estava definido mas não era usado)
    target_slice: str = Field(description="The exact name of one of the slices listed in the prompt")
    new_priority: float = Field(description="The new priority weight for the slice. Use a scale of 1.0 to 10.0.")
    reasoning: str = Field(description="A brief explanation of why this weight was assigned based on the network intent.")


def fatias_do_llm(config, conhece_vr):
    """As fatias que o LLM vê e pode mudar: todas na L3; sem as de VR no prompt do artigo."""
    return [nome for nome, s in config['slices'].items() if conhece_vr or s.get('tipo', 'video') != 'vr']


def secao_tipos(config, fatias, pisos):
    """A seção da L3: o que cada fatia carrega e, para a VR, o piso e como o agente dela dá lance."""
    linhas = ["", "        ### SLICE TYPES", "        Not every slice carries the same kind of traffic:"]
    for nome in fatias:
        s = config['slices'][nome]
        tipo = s.get('tipo', 'video')
        linhas.append(f"        - {nome}: {s.get('descricao', TIPOS[tipo])}")
    for nome in fatias:
        if config['slices'][nome].get('tipo') != 'vr':
            continue
        piso = (pisos or {}).get(nome)
        if piso:
            linhas.append(f"        The {nome} slice has a protected floor of {piso['cpu']} CPU cores and {piso['bw']} Mbps on its "
                          f"UPF, valid only while the VR is active: the auction never takes it below that. When the VR "
                          f"stops, the floor is released and the resources go back to the auction.")
        linhas.append(f"        The {nome} agent bids by its service level (SLA): it bids nothing while the VR is fine and "
                      f"bids high only when the VR is about to leave its SLA. Its priority weight scales those bids, so it "
                      f"changes how strongly the {nome} slice competes for resources above its floor.")
    return "\n".join(linhas) + "\n"


def montar_prompt(config, conhece_vr=False, pisos=None):
    """O system prompt do estrategista. conhece_vr: False = o do artigo (L2), True = o da L3.
    pisos: {fatia: {"cpu", "bw"}} das fatias de VR, para a L3 dizer o piso ao LLM."""
    fatias = fatias_do_llm(config, conhece_vr)
    # ['gold', 'silver', 'bronze'] -> "'gold', 'silver', or 'bronze'"
    if len(fatias) > 1:
        valid_slices_str = ", ".join([f"'{s}'" for s in fatias[:-1]]) + f", or '{fatias[-1]}'"
    else:
        valid_slices_str = f"'{fatias[0]}'"
    secao_vr = secao_tipos(config, fatias, pisos) if conhece_vr else ""

    # Daqui até o fim, o texto do artigo (strategist_agent.py da v1.0-video), só com o {secao_vr} acrescentado
    slices_state = ""
    for slice_name in fatias:
        slices_state += f"        - {slice_name} (Default: {config['slices'][slice_name]['initial_priority']})\n"

    sys_prompt = f"""
        You are an AI 5G Network Strategist orchestrating a Multi-Agent System (MAS). 
        Your job is to translate high-level human intents into economic policies for network slices competing in a resource allocation auction.

        ### THE NETWORK STATE
        You manage active network slices. Their default priority weights are:
        {slices_state}{secao_vr}
        ### THE ECONOMIC ENGINE
        You do not allocate CPU or Bandwidth directly. Instead, you control the economic parameters of the MAS. The priority weight (1.0 to 10.0) you assign directly scales a slice's purchasing power in the Vickrey auction using the following formulas:
        - Budget (Wallet Capacity) = {config['bidding']['base_budget']} * new_weight
        - Income (Wealth Regeneration) = {config['bidding']['base_income']} * new_weight
        - Base Bid (Auction Aggressiveness) = {config['bidding']['base_bid']} * new_weight

        ### Requested Output
        When you receive a network intent, you must respond with a JSON array containing one or more objects. Include an object for EVERY slice that needs its priority adjusted.
        [
            {{
                "target_slice": "one of {valid_slices_str}",
                "new_priority": "a float between 1.0 and 10.0",
                "reasoning": "a brief explanation of your decision"
            }}
        ]

        ### YOUR INSTRUCTIONS
        1. Analyze the incoming network intent (e.g., changes in traffic, VIP events, maintenance).
        2. Determine WHICH slices require a priority adjustment to fulfill this intent. You may adjust one, two, or all slices at once.
        3. Calculate a new weight (between 1.0 and 10.0) for each targeted slice. Higher weights grant massive financial dominance; lower weights financially starve the slice.
        4. Provide a brief, logical reasoning for each decision.
        5. You must output ONLY a valid JSON array matching the requested schema. Do not include markdown formatting or conversational filler.
    """
    return sys_prompt


def extrair_json(resposta):
    """O JSON da resposta: sem as cercas de markdown e, se vier texto em volta, do primeiro [ ou { ao último ] ou }."""
    texto = resposta.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        inicio = min((i for i in (texto.find("["), texto.find("{")) if i >= 0), default=-1)
        fim = max(texto.rfind("]"), texto.rfind("}"))
        if inicio < 0 or fim <= inicio:
            raise
        return json.loads(texto[inicio:fim + 1])


def validar(resposta, fatias):
    """Confere a resposta do LLM antes de ela chegar às fatias.

    resposta: o texto do LLM; fatias: os nomes que ele pode mudar (fatias_do_llm).
    Devolve (validas, descartes):
      validas: [{"fatia", "prioridade", "motivo", "ajuste"}], uma por fatia (se o LLM repetir uma, vale a última);
               a prioridade fica em [1, 10], e "ajuste" diz o valor original quando foi limitada (senão, "");
      descartes: [{"item", "motivo"}]: o que não é JSON, não tem o formato, tem prioridade sem número ou fatia
                 desconhecida (na L2, a VR é desconhecida).
    """
    try:
        dados = extrair_json(resposta)
    except json.JSONDecodeError as e:
        return [], [{"item": resposta.strip()[:200], "motivo": f"não é JSON ({e.msg})"}]
    if isinstance(dados, dict):
        dados = [dados]
    if not isinstance(dados, list):
        return [], [{"item": dados, "motivo": "não é objeto nem lista"}]

    validas, descartes = {}, []
    for item in dados:
        try:
            u = SlicePriorityUpdate.model_validate(item)
        except ValidationError as e:
            erro = e.errors()[0]
            descartes.append({"item": item, "motivo": f"formato: {'.'.join(map(str, erro['loc']))} {erro['msg']}"})
            continue
        fatia = u.target_slice.strip().lower().removesuffix("_slice")
        if fatia not in fatias:
            descartes.append({"item": item, "motivo": f"fatia desconhecida: {u.target_slice}"})
            continue
        if not math.isfinite(u.new_priority):
            descartes.append({"item": item, "motivo": f"prioridade sem número: {u.new_priority}"})
            continue
        prioridade = min(max(u.new_priority, PRIORIDADE_MIN), PRIORIDADE_MAX)
        ajuste = f"{u.new_priority} -> {prioridade}" if prioridade != u.new_priority else ""
        if fatia in validas:
            anterior = validas.pop(fatia)
            descartes.append({"item": {"target_slice": fatia, "new_priority": anterior["prioridade"],
                                       "reasoning": anterior["motivo"]}, "motivo": f"repetida: vale a última de {fatia}"})
        validas[fatia] = {"fatia": fatia, "prioridade": prioridade, "motivo": u.reasoning, "ajuste": ajuste}
    return list(validas.values()), descartes
