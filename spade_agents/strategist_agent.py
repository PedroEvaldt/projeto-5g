import csv
import json
import time
import yaml
import asyncio
import spade
import os
from dotenv import load_dotenv
from spade.agent import Agent
from spade.message import Message
from spade.behaviour import CyclicBehaviour
from spade_llm import LLMAgent, LLMProvider, ChatAgent
from spade_llm.context import create_user_message, create_system_message, ContextManager
from litellm import acompletion

import politica_llm

load_dotenv()
MODEL = "openai/vllm.gpt-oss-20b"
BASE_URL = "https://ollama.k8s.inf.ufrgs.br/api"
TEMPERATURE = 0.01
API_KEY = os.getenv("API_KEY")
TEST_CONFIG_PATH = os.getenv("TEST_FILE")
with open(TEST_CONFIG_PATH, "r") as f:
    config = yaml.safe_load(f)

# Phase 7: the prompt and the validation of the answers are in politica_llm.py (SlicePriorityUpdate moved there).
# vr.llm: artigo = the paper's prompt, without the VR slices (L2); vr = the prompt describes the VR and its floor (L3)
VR_LLM = str((config.get('vr') or {}).get('llm', 'artigo'))
if VR_LLM not in ('artigo', 'vr'):
    raise SystemExit(f"[ERROR] vr.llm: {VR_LLM} is not valid. Use vr.llm: artigo or vr.")
KNOWS_VR = VR_LLM == 'vr'
LLM_SLICES = politica_llm.fatias_do_llm(config, KNOWS_VR)     # the slices the LLM sees and may change
CALIBRACAO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vr", "calibracao.yaml")
DECISIONS_CSV = config['monitoring']['csv_output_dir'] + "llm_decisoes.csv"


def vr_floors():
    """The floor of each VR slice, as the auctioneer applies it (vr/calibracao.yaml, unless the slice defines
    floor_cpu/floor_bw); none with vr.floor off."""
    if str((config.get('vr') or {}).get('floor', True)).lower() not in ("true", "on", "1"):   # YAML reads `on` as True
        return {}
    with open(CALIBRACAO) as f:
        calib = yaml.safe_load(f)['piso']
    return {name: {"cpu": float(s.get('floor_cpu', calib['cpu'])), "bw": float(s.get('floor_bw', calib['banda_mbps']))}
            for name, s in config['slices'].items() if s.get('tipo') == 'vr'}


def log_decisions(valid, discarded):
    """One row per item of each LLM answer in llm_decisoes.csv, next to the auction_history.csv: what the LLM decided,
    what was clamped to [1, 10] and what was discarded (and why)."""
    now = time.strftime('%Y-%m-%d %H:%M:%S')
    new = not os.path.exists(DECISIONS_CSV)
    os.makedirs(os.path.dirname(DECISIONS_CSV), exist_ok=True)
    with open(DECISIONS_CSV, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["Timestamp", "Status", "Fatia", "Prioridade", "Ajuste", "Raciocinio", "Motivo_descarte"])
        for v in valid:
            w.writerow([now, "valida", v["fatia"], v["prioridade"], v["ajuste"], v["motivo"], ""])
        for d in discarded:
            item = d["item"] if isinstance(d["item"], dict) else {"reasoning": d["item"]}
            w.writerow([now, "descartada", item.get("target_slice", ""), item.get("new_priority", ""), "",
                        item.get("reasoning", ""), d["motivo"]])

class DispatcherAgent(Agent):
    class RouteMessagesBehaviour(CyclicBehaviour):
        async def on_start(self):
            print("🚦 Dispatcher Agent is routing messages...")
        async def run(self):
            msg = await self.receive(timeout=10)
            if msg:
                try:
                    # Phase 7: only known slices, with the priority clamped to [1, 10] (politica_llm.validar)
                    policy_updates, discarded = politica_llm.validar(msg.body, LLM_SLICES)
                    log_decisions(policy_updates, discarded)
                    for d in discarded:
                        print(f"⚠️ [Dispatcher] Discarded {d['item']}: {d['motivo']}")

                    print("🔄 [Dispatcher] Sending new policies to slice agents...")

                    for update in policy_updates:
                        target_slice = update["fatia"] + "_slice@localhost"
                        msg = Message(to=str(target_slice))
                        msg.set_metadata("performative", "inform")
                        msg.body = json.dumps({
                            "new_priority": update["prioridade"]
                        })
                        await self.send(msg)
                        clamped = f" (the LLM asked {update['ajuste']})" if update["ajuste"] else ""
                        print(f"📤 Dispatched new priority {update['prioridade']}{clamped} to {update['fatia']} with reasoning: {update['motivo']}")
                    print("✅ [Dispatcher] All slices updated!\n")
                except Exception as e:
                    print(f"❌ [Dispatcher] Error while sending policies: {e}")

    async def setup(self):
        print("📡 Starting Dispatcher Agent...")
        self.add_behaviour(self.RouteMessagesBehaviour())

        return await super().setup()



async def main():
    print("🚀 Starting Strategist Agent...")
    provider = LLMProvider(
        model=MODEL,
        base_url=BASE_URL,
        api_key=API_KEY,
        temperature=TEMPERATURE,
    )

    # Phase 7: the paper's prompt (vr.llm: artigo, L2) or the one that knows the VR and its floor (vr.llm: vr, L3)
    sys_prompt = politica_llm.montar_prompt(config, KNOWS_VR, vr_floors())
    print(f"🧭 Prompt: vr.llm = {VR_LLM}. Slices the LLM may change: {', '.join(LLM_SLICES)}")
    dispatcher_agent = DispatcherAgent(
        jid="dispatcher_agent@localhost",
        password="password",
    )
    strategist_agent = LLMAgent(
        jid="strategist_agent@localhost",
        password="password",
        provider=provider,
        system_prompt=sys_prompt,
        reply_to="dispatcher_agent@localhost",
    )

    def display_response(message: str, sender: str):
        print(f"\n🤖 Assistant: {message}")
        print("-" * 50)
    user_agent = ChatAgent(
        jid="user_agent@localhost",
        password="password",
        target_agent_jid="strategist_agent@localhost",
    )

    try:
        # Start both agents
        await dispatcher_agent.start()
        await strategist_agent.start()
        await user_agent.start()

        print("✅ Agents started successfully!")
        print("💬 You can now chat with your AI assistant")
        print("Type 'exit' to quit\n")

        # Run interactive chat
        await user_agent.run_interactive(response_timeout=15.0)

    except KeyboardInterrupt:
        print("\n👋 Shutting down...")
    finally:
        # Clean up
        await dispatcher_agent.stop()
        await user_agent.stop()
        await strategist_agent.stop()
        print("✅ Agents stopped successfully!")

if __name__ == "__main__":
    spade.run(main())
