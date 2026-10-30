#!/usr/bin/env python3
"""
Orquestrador de Testes Dinâmicos para 5G Network Slicing
=========================================================
Lê a configuração do teste, executa eventos programados (mudanças de prioridade,
pausa/resumo de tráfego) e registra tudo num event log.

ALTERAÇÃO: os eventos de "priority_change" agora são enviados diretamente para o
Strategist Agent via XMPP (SPADE), eliminando a necessidade de colar o texto do
intent manualmente no terminal do ChatAgent. Para isso, o script inteiro passou
a rodar dentro do loop assíncrono do SPADE (spade.run), mantendo toda a lógica
de agendamento/log original.
"""

import yaml
import json
import time
import asyncio
import subprocess
import sys
import os
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv

import spade
from spade.agent import Agent
from spade.behaviour import OneShotBehaviour
from spade.message import Message
from spade.template import Template

load_dotenv()
TEST_CONFIG_PATH = os.getenv("TEST_FILE")


class IntentNotifierAgent(Agent):
    """
    Agente minimalista cujo único papel é encaminhar intents em linguagem
    natural para o Strategist Agent, no lugar do ChatAgent interativo.
    O LLMAgent do spade_llm processa qualquer mensagem XMPP recebida da
    mesma forma, então não é necessário nada além de um Message comum.
    """

    class SendIntent(OneShotBehaviour):
        def __init__(self, target_jid, intent_text, thread=None):
            super().__init__()
            self.target_jid = target_jid
            self.intent_text = intent_text
            self.thread = thread

        async def run(self):
            msg = Message(to=self.target_jid)
            msg.set_metadata("message_type", "llm")  # exigido pelo Template do LLMBehaviour (spade_llm/agent/llm_agent.py:172)
            msg.body = self.intent_text
            if self.thread:
                msg.thread = self.thread  # mantém as mensagens do teste na mesma conversa
            print(f"[{self.agent.name}] Enviando: {msg}")
            await self.send(msg)

    async def send_intent(self, target_jid, intent_text, thread=None):
        """Envia um intent e aguarda a conclusão do envio (fire-and-wait)."""
        behaviour = self.SendIntent(target_jid, intent_text, thread)
        self.add_behaviour(behaviour)
        await behaviour.join()

    class SendPerformative(OneShotBehaviour):
        """Mensagem com performative (Fase 5): o aviso de VR_JOIN/VR_LEAVE para o leiloeiro."""
        def __init__(self, target_jid, performative, body):
            super().__init__()
            self.target_jid, self.performative, self.body = target_jid, performative, body

        async def run(self):
            msg = Message(to=self.target_jid)
            msg.set_metadata("performative", self.performative)
            msg.body = self.body
            await self.send(msg)

    async def send_performative(self, target_jid, performative, body):
        behaviour = self.SendPerformative(target_jid, performative, body)
        self.add_behaviour(behaviour)
        await behaviour.join()

    class AskAndWait(OneShotBehaviour):
        """Manda uma mensagem e espera a resposta (a reserva do piso da VR, Fase 5)."""
        def __init__(self, target_jid, performative, body, timeout):
            super().__init__()
            self.target_jid, self.performative, self.body, self.timeout = target_jid, performative, body, timeout
            self.reply = None

        async def run(self):
            msg = Message(to=self.target_jid)
            msg.set_metadata("performative", self.performative)
            msg.body = self.body
            await self.send(msg)
            self.reply = await self.receive(timeout=self.timeout)

    async def ask_and_wait(self, target_jid, performative, body, reply_performative, timeout):
        """Devolve a resposta, ou None se ela não chegar no tempo."""
        behaviour = self.AskAndWait(target_jid, performative, body, timeout)
        self.add_behaviour(behaviour, Template(metadata={"performative": reply_performative}))
        await behaviour.join()
        return behaviour.reply

    async def setup(self):
        print(f"[{self.name}] Intent Notifier pronto.")


class TestOrchestrator:
    def __init__(self, notifier, config_file=TEST_CONFIG_PATH,
                 strategist_jid="strategist_agent@localhost"):
        self.notifier = notifier
        self.strategist_jid = strategist_jid
        self.config_file = config_file
        self.config = self.load_config()
        self.start_time = None
        self.event_log_file = self.config["monitoring"]["event_log_file"]
        # thread estável por execução de teste, pra manter o contexto da
        # conversa do LLM consistente ao longo dos eventos do mesmo teste
        self.conversation_thread = self.config["test_metadata"]["name"]
        self.setup_event_log()

    def load_config(self):
        """Carrega a configuração YAML."""
        try:
            with open(self.config_file, 'r') as f:
                return yaml.safe_load(f)
        except Exception as e:
            print(f"[ERRO] Falha ao carregar {self.config_file}: {e}")
            sys.exit(1)

    def setup_event_log(self):
        """Cria o arquivo de log de eventos com cabeçalho."""
        Path(self.config["monitoring"]["csv_output_dir"]).mkdir(parents=True, exist_ok=True)
        log_path = f"{self.config['monitoring']['csv_output_dir']}{self.event_log_file}"

        with open(log_path, 'w') as f:
            f.write("Timestamp_Offset_Seconds,Event_Type,Description,Details\n")

        print(f"[INIT] Event log criado em {log_path}")

    def log_event(self, event_type, description, details=""):
        """Registra um evento no log."""
        offset = (datetime.now() - self.start_time).total_seconds()
        log_path = f"{self.config['monitoring']['csv_output_dir']}{self.event_log_file}"

        safe_desc = str(description).replace('"', "'")
        safe_details = str(details).replace('"', "'")

        with open(log_path, 'a') as f:
            f.write(f"{offset:.1f},{event_type},\"{safe_desc}\",\"{safe_details}\"\n")

        print(f"[+{offset:.1f}s] [{event_type}] {safe_desc}")

    async def change_priority(self, changes, description=""):
        """Muda a prioridade de slices enviando o intent diretamente ao Strategist Agent."""
        print(f"\n[ORCHESTRATION] Enviando intent ao strategist agent...")

        details = json.dumps({s: v for s, v in changes.items()})
        self.log_event("PRIORITY_CHANGE", f"Intent: {description} | Mudanças esperadas: {details}", details)

        print(f"\n[STRATEGIST INPUT] {description}")
        print(f"Mudanças esperadas: {', '.join([f'{s}={v}' for s, v in changes.items()])}\n")

        try:
            await self.notifier.send_intent(
                target_jid=self.strategist_jid,
                intent_text=description,
                thread=self.conversation_thread,
            )
            print(f"[ORCHESTRATION] Intent entregue a {self.strategist_jid}. Aguardando o LLM processar...")
            # self.log_event("PRIORITY_CHANGE_SENT", f"Intent entregue via XMPP a {self.strategist_jid}", details)
        except Exception as e:
            print(f"[ERRO] Falha ao enviar intent ao strategist agent: {e}")
            self.log_event("PRIORITY_CHANGE_FAILED", f"Falha ao entregar intent: {e}", details)

    def pause_traffic(self, slice_name):
        """Pausa o tráfego de um slice matando temporariamente o container DASH."""
        try:
            namespace = "pfsevaldt"

            target_pod_base = self.config['slices'][slice_name]['client_pod']

            cmd = f"kubectl get pods -n {namespace} -o name | grep {target_pod_base}"
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            pods = [p.replace('pod/', '') for p in result.stdout.splitlines() if p.strip()]

            if not pods:
                print(f"[WARN] Nenhum pod encontrado para slice {slice_name} (buscando por {target_pod_base})")
                self.log_event("TRAFFIC_PAUSE_FAIL", f"Nenhum pod encontrado para {slice_name}", "")
                return

            for pod in pods:
                cmd = f'kubectl -n {namespace} exec {pod} -c dash-client -- sh -c "pkill -SIGUSR1 node" 2>/dev/null || true'
                subprocess.run(cmd, shell=True)
                print(f"[PAUSE] Tráfego pausado no pod {pod}")

            details = f"pods={','.join(pods)}"
            self.log_event("TRAFFIC_PAUSE", f"Tráfego pausado em {slice_name}", details)

        except KeyError:
            print(f"[ERROR] Slice '{slice_name}' ou 'client_pod' não definido no YAML.")
        except Exception as e:
            print(f"[ERROR] Falha ao pausar tráfego: {e}")
            self.log_event("TRAFFIC_PAUSE_ERROR", f"Erro ao pausar {slice_name}", str(e))

    def resume_traffic(self, slice_name):
        """Retoma o tráfego de um slice."""
        try:
            namespace = "pfsevaldt"

            target_pod_base = self.config['slices'][slice_name]['client_pod']

            cmd = f"kubectl get pods -n {namespace} -o name | grep {target_pod_base}"
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            pods = [p.replace('pod/', '') for p in result.stdout.splitlines() if p.strip()]

            if not pods:
                print(f"[WARN] Nenhum pod encontrado para slice {slice_name}")
                return

            for pod in pods:
                cmd = f'kubectl -n {namespace} exec {pod} -c dash-client -- sh -c "pkill -SIGUSR2 node" 2>/dev/null || true'
                subprocess.run(cmd, shell=True)
                print(f"[RESUME] Tráfego retomado no pod {pod}")

            details = f"pods={','.join(pods)}"
            self.log_event("TRAFFIC_RESUME", f"Tráfego retomado em {slice_name}", details)

        except KeyError:
            print(f"[ERROR] Slice '{slice_name}' ou 'client_pod' não definido no YAML.")
        except Exception as e:
            print(f"[ERROR] Falha ao retomar tráfego: {e}")
            self.log_event("TRAFFIC_RESUME_ERROR", f"Erro ao retomar {slice_name}", str(e))

    def sinal_vr(self, slice_name, sinal):
        """USR2 retoma e USR1 pausa o exporter da VR (PID 1 do vr-client; o kill é o do sh). Devolve o erro ou ''."""
        client_pod = self.config['slices'][slice_name]['client_pod']
        cmd = ["kubectl", "exec", "-n", "pfsevaldt", f"deploy/{client_pod}", "-c", "vr-client", "--",
               "sh", "-c", f"kill -{sinal} 1"]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return r.stderr.strip() if r.returncode != 0 else ""

    async def vr_event(self, slice_name, entrar, description):
        """VR_JOIN / VR_LEAVE (Fase 5), como uma reserva de recursos:
        - entrada: avisa o leiloeiro, espera ele aplicar o piso da VR (rodada extra) e só então retoma o tráfego;
        - saída: pausa o tráfego e só então avisa o leiloeiro, que libera o piso.
        Em L0 (vr.floor: off) o leiloeiro responde na hora, sem mudar nada."""
        tipo = "VR_JOIN" if entrar else "VR_LEAVE"
        corpo = json.dumps({"fatia": slice_name})
        try:
            client_pod = self.config['slices'][slice_name]['client_pod']
            reserva = ""
            if entrar:
                t0 = time.monotonic()
                resposta = await self.notifier.ask_and_wait("resource_agent@localhost", "vr_join", corpo, "vr_ack", timeout=20)
                espera = time.monotonic() - t0
                if resposta is None:
                    reserva = f"reserva sem resposta do leiloeiro em {espera:.1f} s"
                else:
                    r = json.loads(resposta.body)
                    reserva = (f"piso aplicado em {espera:.1f} s: CPU {r['cpu']:.2f} (kubelet {r['cpu_aplicada']}), banda {r['bw']:.2f}"
                               if r["piso_aplicado"] else f"sem piso ({r['nota']}), resposta em {espera:.1f} s")
                    if any(v > 0 for v in r["falta"].values()):
                        reserva += f"; faltou {r['falta']}"
                print(f"[VR] {slice_name}: {reserva}")
            erro = await asyncio.to_thread(self.sinal_vr, slice_name, "USR2" if entrar else "USR1")
            if erro:
                self.log_event(f"{tipo}_FAIL", f"Sinal para a VR em {slice_name} falhou", erro)
                if entrar:      # a VR não voltou: desfaz a reserva, para o piso não ficar preso
                    await self.notifier.send_performative("resource_agent@localhost", "vr_leave", corpo)
                return
            self.log_event(tipo, description, f"pods={client_pod}" + (f" | {reserva}" if reserva else ""))
            if not entrar:
                await self.notifier.send_performative("resource_agent@localhost", "vr_leave", corpo)
        except Exception as e:
            print(f"[ERROR] Falha no {tipo} de {slice_name}: {e}")
            self.log_event(f"{tipo}_ERROR", f"Erro no {tipo} de {slice_name}", str(e))

    async def retomar_vr(self):
        """No fim do teste, retoma as VRs pausadas, para o testbed voltar ao normal (a VR medindo sempre).
        Não avisa o leiloeiro: o teste acabou."""
        for nome, s in self.config['slices'].items():
            if s.get('tipo') == 'vr':
                erro = await asyncio.to_thread(self.sinal_vr, nome, "USR2")
                print(f"[VR] {nome} retomada no fim do teste" + (f" (falhou: {erro})" if erro else ""))

    async def run(self):
        """Executa o teste seguindo os eventos programados."""
        print("\n" + "=" * 70)
        print(f"INICIANDO TESTE: {self.config['test_metadata']['name']}")
        print(f"Duração: {self.config['test_metadata']['duration_seconds']}s")
        print("=" * 70 + "\n")

        self.start_time = datetime.now()
        # A hora real do início, com fuso: o coletor da VR (analise_metricas_vr.py) precisa dela para levar as
        # janelas do log do pod para a régua dos eventos
        inicio = self.start_time.astimezone().isoformat(timespec="milliseconds")
        self.log_event("TEST_START", "Teste iniciado", f"Config: {self.config_file} | inicio={inicio}")

        try:
            if self.config['events']:
                events = sorted(self.config['events'], key=lambda e: e['timestamp'])

                for event in events:
                    timestamp = event['timestamp']
                    event_type = event['type']
                    description = event['description']

                    # Aguarda o tempo do evento
                    while (datetime.now() - self.start_time).total_seconds() < timestamp:
                        await asyncio.sleep(1)

                    # Executa o evento
                    if event_type == "priority_change":
                        await self.change_priority(event['changes'], event['description'])

                    elif event_type == "traffic_pause":
                        self.pause_traffic(event['pause_slice'])

                    elif event_type == "traffic_resume":
                        self.resume_traffic(event['resume_slice'])

                    elif event_type in ("vr_join", "vr_leave"):
                        await self.vr_event(event['vr_slice'], event_type == "vr_join", description)

            # Aguarda até o fim do teste
            total_duration = self.config['test_metadata']['duration_seconds']
            while (datetime.now() - self.start_time).total_seconds() < total_duration:
                await asyncio.sleep(5)

            self.log_event("TEST_END", "Teste finalizado", "")
            await self.retomar_vr()
            print("\n[SUCESSO] Teste concluído!")

        except KeyboardInterrupt:
            self.log_event("TEST_INTERRUPTED", "Teste interrompido pelo usuário", "")
            await self.retomar_vr()
            print("\n[INTERROMPIDO] Teste cancelado.")

        except Exception as e:
            self.log_event("TEST_ERROR", f"Erro durante execução: {e}", str(e))
            print(f"\n[ERRO] {e}")


async def main():
    config_file = sys.argv[1] if len(sys.argv) > 1 else TEST_CONFIG_PATH

    # NÃO passar embedded_xmpp_server=True aqui: o slice_agents.py já sobe o
    # servidor XMPP embutido; este agente só precisa se conectar como cliente.
    notifier = IntentNotifierAgent("test_orchestrator@localhost", "password")
    await notifier.start()

    orchestrator = TestOrchestrator(notifier, config_file)
    await orchestrator.run()

    await notifier.stop()


if __name__ == "__main__":
    spade.run(main())