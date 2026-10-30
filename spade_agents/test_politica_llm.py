"""
Testes do politica_llm.py, sem LLM (Fase 7): o prompt do artigo continua o mesmo, a L3 descreve a VR, e a
validação deixa passar só prioridades de fatias conhecidas, dentro de [1, 10].

Uso (de dentro de spade_agents/):
  ../venv/bin/python -m unittest test_politica_llm -v
"""

import copy
import os
import unittest

import yaml

import politica_llm

LLM_YAML = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tests_configs", "test_config_llm.yaml")
with open(LLM_YAML) as f:
    ARTIGO = yaml.safe_load(f)                    # o cenário cidade inteligente do artigo: 3 fatias de vídeo
COM_VR = copy.deepcopy(ARTIGO)
COM_VR['slices']['drone'] = {"initial_priority": 10.0, "tipo": "vr", "upf": "upf4", "client_pod": "ue-vr-07"}
PISOS = {"drone": {"cpu": 0.28, "bw": 9.9}}
FATIAS_L2 = politica_llm.fatias_do_llm(COM_VR, False)
FATIAS_L3 = politica_llm.fatias_do_llm(COM_VR, True)


def original_prompt(config):
    """Cópia do prompt do strategist_agent.py da v1.0-video (linhas 23-27 e 84-117), para o teste de equivalência."""
    slice_names = list(config['slices'].keys())
    if len(slice_names) > 1:
        valid_slices_str = ", ".join([f"'{s}'" for s in slice_names[:-1]]) + f", or '{slice_names[-1]}'"
    else:
        valid_slices_str = f"'{slice_names[0]}'"
    slices_state = ""
    for slice_name, info in config['slices'].items():
        slices_state += f"        - {slice_name} (Default: {info['initial_priority']})\n"

    sys_prompt = f"""
        You are an AI 5G Network Strategist orchestrating a Multi-Agent System (MAS). 
        Your job is to translate high-level human intents into economic policies for network slices competing in a resource allocation auction.

        ### THE NETWORK STATE
        You manage active network slices. Their default priority weights are:
        {slices_state}
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


class Prompt(unittest.TestCase):

    def test_artigo_igual_ao_original(self):
        self.assertEqual(politica_llm.montar_prompt(ARTIGO), original_prompt(ARTIGO))

    def test_artigo_esconde_a_vr(self):
        # L2: o LLM recebe o prompt do artigo, como se a VR não existisse
        self.assertEqual(FATIAS_L2, ["policia", "bombeiros", "ambulancia"])
        self.assertEqual(politica_llm.montar_prompt(COM_VR, False, PISOS), original_prompt(ARTIGO))

    def test_l3_descreve_a_vr(self):
        prompt = politica_llm.montar_prompt(COM_VR, True, PISOS)
        self.assertEqual(FATIAS_L3, ["policia", "bombeiros", "ambulancia", "drone"])
        self.assertIn("- drone (Default: 10.0)", prompt)
        self.assertIn("'ambulancia', or 'drone'", prompt)
        self.assertIn("### SLICE TYPES", prompt)
        self.assertIn("0.28 CPU cores and 9.9 Mbps", prompt)
        # Fora a seção nova, é o texto do artigo
        sem_secao = prompt.replace(politica_llm.secao_tipos(COM_VR, FATIAS_L3, PISOS), "")
        self.assertEqual(sem_secao, original_prompt(COM_VR))

    def test_descricao_do_yaml(self):
        config = copy.deepcopy(COM_VR)
        config['slices']['drone']['descricao'] = "firefighters' drone, teleoperated through a VR stream"
        self.assertIn("- drone: firefighters' drone", politica_llm.montar_prompt(config, True, PISOS))


class Validacao(unittest.TestCase):

    def validar(self, resposta, fatias=FATIAS_L3):
        return politica_llm.validar(resposta, fatias)

    def test_resposta_do_artigo(self):
        validas, descartes = self.validar('```json\n[{"target_slice": "bombeiros", "new_priority": 9.0, '
                                          '"reasoning": "incêndio"}]\n```')
        self.assertEqual(validas, [{"fatia": "bombeiros", "prioridade": 9.0, "motivo": "incêndio", "ajuste": ""}])
        self.assertEqual(descartes, [])

    def test_objeto_sozinho_e_texto_em_volta(self):
        validas, _ = self.validar('Here is the policy: {"target_slice": "policia", "new_priority": "8", '
                                  '"reasoning": "perseguição"} Done.')
        self.assertEqual([(v["fatia"], v["prioridade"]) for v in validas], [("policia", 8.0)])

    def test_limita_a_prioridade(self):
        validas, _ = self.validar('[{"target_slice": "drone", "new_priority": 15, "reasoning": "a"},'
                                  ' {"target_slice": "policia", "new_priority": 0, "reasoning": "b"}]')
        self.assertEqual([(v["fatia"], v["prioridade"], v["ajuste"]) for v in validas],
                         [("drone", 10.0, "15.0 -> 10.0"), ("policia", 1.0, "0.0 -> 1.0")])

    def test_descarta_fatia_desconhecida(self):
        validas, descartes = self.validar('[{"target_slice": "metro", "new_priority": 7, "reasoning": "a"},'
                                          ' {"target_slice": "Bombeiros_slice", "new_priority": 7, "reasoning": "b"}]')
        self.assertEqual([v["fatia"] for v in validas], ["bombeiros"])      # nome normalizado
        self.assertEqual(len(descartes), 1)
        self.assertIn("desconhecida", descartes[0]["motivo"])

    def test_na_l2_a_vr_e_desconhecida(self):
        validas, descartes = self.validar('[{"target_slice": "drone", "new_priority": 10, "reasoning": "a"}]',
                                          FATIAS_L2)
        self.assertEqual(validas, [])
        self.assertIn("desconhecida", descartes[0]["motivo"])

    def test_descarta_formato_errado(self):
        validas, descartes = self.validar('[{"target_slice": "policia", "new_priority": "alta", "reasoning": "a"},'
                                          ' {"target_slice": "bombeiros", "new_priority": 5},'
                                          ' {"target_slice": "ambulancia", "new_priority": NaN, "reasoning": "c"}]')
        self.assertEqual(validas, [])
        self.assertEqual(len(descartes), 3)

    def test_nao_e_json(self):
        validas, descartes = self.validar("Desculpe, não entendi a intenção.")
        self.assertEqual(validas, [])
        self.assertIn("não é JSON", descartes[0]["motivo"])

    def test_repetida_vale_a_ultima(self):
        validas, descartes = self.validar('[{"target_slice": "policia", "new_priority": 9, "reasoning": "a"},'
                                          ' {"target_slice": "policia", "new_priority": 5, "reasoning": "b"}]')
        self.assertEqual([(v["fatia"], v["prioridade"]) for v in validas], [("policia", 5.0)])
        self.assertIn("repetida", descartes[0]["motivo"])


if __name__ == "__main__":
    unittest.main()
