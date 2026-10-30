import yaml
import json
import resource
import time
import spade
import asyncio
import os
from spade.agent import Agent
from spade.behaviour import CyclicBehaviour
from spade.behaviour import PeriodicBehaviour
from spade.message import Message
from spade.template import Template
from kubernetes import client, config
from prometheus_api_client import PrometheusConnect
from dotenv import load_dotenv

load_dotenv()
TEST_CONFIG_PATH = os.getenv("TEST_FILE")
with open(TEST_CONFIG_PATH, "r") as f:
    test_config = yaml.safe_load(f)

# CONSTANTS
NAMESPACE = "pfsevaldt"
URL_PROMETHEUS = test_config['monitoring']['prometheus_url']
BASE_BUDGET = test_config['bidding']['base_budget']
BASE_INCOME = test_config['bidding']['base_income']
BASE_BID = test_config['bidding']['base_bid']
MONITORING_PERIOD = test_config['auction']['monitoring_period_seconds']

HIGH_STRESS_THRESHOLD = test_config['bidding']['high_stress_threshold']
MODERATE_STRESS_THRESHOLD = test_config['bidding']['moderate_stress_threshold']
COMFORTABLE_THRESHOLD = test_config['bidding']['comfortable_threshold']

HIGH_STRESS_COEFF_MAX = test_config['bidding']['high_stress_coeff_max']
HIGH_STRESS_COEFF_MIN = test_config['bidding']['high_stress_coeff_min']
MODERATE_STRESS_COEFF_MAX = test_config['bidding']['moderate_stress_coeff_max']
MODERATE_STRESS_COEFF_MIN = test_config['bidding']['moderate_stress_coeff_min']
COMFORTABLE_COEFF_MAX = test_config['bidding']['comfortable_coeff_max']
COMFORTABLE_COEFF_MIN = test_config['bidding']['comfortable_coeff_min']

class SliceAgent(Agent):

    class AuctionParticipant(CyclicBehaviour):
        async def on_start(self):
            print(f"[{self.agent.name}] Iniciating listening for Auction...")
        async def run(self):
            msg = await self.receive(timeout=10)

            if msg:
                if msg.get_metadata("performative") == "scout":
                    msg_data = json.loads(msg.body)
                    self.base_cpu_limit = msg_data["base_cpu_limit"]
                    self.base_bw_limit = msg_data["base_bw_limit"]
                    self.base_memory_limit = msg_data["base_memory_limit"]
                    print(f"[{self.agent.name}] Received auction parameters. Base CPU: {self.base_cpu_limit}, Base Memory: {self.base_memory_limit}Mi, Base BW: {self.base_bw_limit}Mbps.")

                    reply = Message(to=str(msg.sender))
                    reply.set_metadata("performative", "limits")
                    reply.body = json.dumps({
                        "cpu_limit" : self.agent.cpu_limit,
                        "memory_limit" : self.agent.memory_limit,
                        "bw_limit" : self.agent.bandwidth_limit,
                        "upf_target" : self.agent.upf_target
                    })
                    await self.send(reply)
                if msg.get_metadata("performative") == "adjustment":
                    msg_data = json.loads(msg.body)
                    if "new_cpu" in msg_data:
                        self.agent.cpu_limit = msg_data["new_cpu"]
                        print(f"[{self.agent.name}] Received CPU adjustment. New CPU limit: {self.agent.cpu_limit}.")
                    if "new_bw" in msg_data:
                        self.agent.bandwidth_limit = msg_data["new_bw"]
                        print(f"[{self.agent.name}] Received Bandwidth adjustment. New BW limit: {self.agent.bandwidth_limit}Mbps.")
                    if "new_mem" in msg_data:
                        self.agent.memory_limit = msg_data["new_mem"]
                        print(f"[{self.agent.name}] Received Memory adjustment. New Memory limit: {self.agent.memory_limit}Mi.")
                
                if msg.get_metadata("performative") == "cfp":
                    print(f"[{self.agent.name}] CFP receveid from Resource Agent. Calculating bid...")
                    # 1. Calculate utilization based on current CPU usage and CPU limit
                    cpu_usage = self.agent.cpu_usage
                    cpu_limit = self.agent.cpu_limit

                    memory_usage = self.agent.memory_usage
                    memory_limit = self.agent.memory_limit

                    bw_usage = self.agent.bandwidth_usage
                    bw_limit = self.agent.bandwidth_limit

                    base_cpu_limit = self.base_cpu_limit
                    base_bw_limit = self.base_bw_limit
                    base_mem_limit = self.base_memory_limit

                    priority = self.agent.priority

                    cpu_utilization = (cpu_usage / cpu_limit ) if cpu_limit > 0.0 else 0.0
                    memory_utilization = (memory_usage / memory_limit) if memory_limit > 0.0 else 0.0
                    bw_utilization = (bw_usage / bw_limit) if bw_limit > 0.0 else 0.0
                    print(f"[{self.agent.name}] Current CPU Utilization: {cpu_utilization:.2%} ({cpu_usage}/{cpu_limit})")
                    print(f"[{self.agent.name}] Current Memory Utilization: {memory_utilization:.2%} ({memory_usage}/{memory_limit})")
                    print(f"[{self.agent.name}] Current Bandwidth Utilization: {bw_utilization:.2%} ({bw_usage}Mbps/{bw_limit}Mbps)")
                    highest_utilization = max(cpu_utilization, bw_utilization)
                    budget = self.agent.budget

                    def calculate_coeff(priority, i_min, i_max):
                        # 1. Normalize priority to a -1.0 to 1.0 scale
                        p_norm = (priority - 5.0) / 5.0

                        # 2. Apply a non-linear transformation to create a more aggressive curve
                        curve = p_norm ** 3

                        # 3. Transform the curve to fit between 0 and 1
                        fator = (curve + 1.0) / 2.0

                        # 4. Calculate final coefficient
                        coeff = i_min + fator * (i_max - i_min)

                        return coeff

                    # 2. Dynamic Bidding
                    if highest_utilization > HIGH_STRESS_THRESHOLD:
                        
                        coeff = calculate_coeff(priority, HIGH_STRESS_COEFF_MIN, HIGH_STRESS_COEFF_MAX)
                        target_cpu = cpu_limit + (base_cpu_limit*coeff) if cpu_utilization > HIGH_STRESS_THRESHOLD else cpu_limit
                        target_memory = memory_limit + (base_mem_limit*coeff) if memory_utilization > HIGH_STRESS_THRESHOLD else memory_limit
                        target_bandwidth = bw_limit + (base_bw_limit * coeff) if bw_utilization > HIGH_STRESS_THRESHOLD else bw_limit

                        bid = min(budget, self.agent.base_bid * 1.5)
                        print(f"[{self.agent.name}] HIGH STRESS! Requesting CPU: {target_cpu}, MEM: {target_memory}Mi, BW: {target_bandwidth}Mbps. Bidding {bid}.")
                    elif highest_utilization > MODERATE_STRESS_THRESHOLD:
                        
                        coeff = calculate_coeff(priority, MODERATE_STRESS_COEFF_MIN, MODERATE_STRESS_COEFF_MAX)
                        target_cpu = cpu_limit + (base_cpu_limit*coeff) if cpu_utilization > MODERATE_STRESS_THRESHOLD else cpu_limit
                        target_memory = memory_limit + (base_mem_limit*coeff) if memory_utilization > MODERATE_STRESS_THRESHOLD else memory_limit
                        target_bandwidth = bw_limit + (base_bw_limit * coeff) if bw_utilization > MODERATE_STRESS_THRESHOLD else bw_limit
                        bid = min(budget, self.agent.base_bid)
                        print(f"[{self.agent.name}] MODERATE STRESS. Requesting CPU: {target_cpu}, MEM: {target_memory}Mi, BW: {target_bandwidth}Mbps. Bidding {bid}.")
                    elif highest_utilization > COMFORTABLE_THRESHOLD:
                        
                        coeff = calculate_coeff(priority, COMFORTABLE_COEFF_MIN, COMFORTABLE_COEFF_MAX)
                        target_cpu = cpu_limit + (base_cpu_limit*coeff) if cpu_utilization > COMFORTABLE_THRESHOLD else cpu_limit
                        target_memory = memory_limit + (base_mem_limit*coeff) if memory_utilization > COMFORTABLE_THRESHOLD else memory_limit
                        target_bandwidth = bw_limit + (base_bw_limit * coeff) if bw_utilization > COMFORTABLE_THRESHOLD else bw_limit
                        bid = min(budget, self.agent.base_bid) 
                        print(f"[{self.agent.name}] COMFORTABLE. Requesting CPU: {target_cpu}, MEM: {target_memory}Mi, BW: {target_bandwidth}Mbps. Bidding {bid}.")
                    else:
                        target_cpu = cpu_limit
                        target_memory = memory_limit
                        target_bandwidth = bw_limit
                        bid = min(budget, self.agent.base_bid * 0.1)
                        print(f"[{self.agent.name}] LOW STRESS. Maintaining targets. Bidding {bid}.")

                    
                    reply = Message(to=str(msg.sender))
                    reply.set_metadata("performative", "propose")
                    reply.body = json.dumps({
                        "bid" : bid,
                        "cpu_target" : target_cpu,
                        "cpu_limit" : cpu_limit,
                        "memory_target" : target_memory,
                        "memory_limit" : memory_limit,
                        "memory_usage" : memory_usage,
                        "bw_target" : target_bandwidth,
                        "bw_limit" : bw_limit,
                        "upf_target" : self.agent.upf_target
                    }) 

                    await self.send(reply)

                if msg.get_metadata("performative") == "accept-proposal":
                    msg_data = json.loads(msg.body)
                    print(f"[{self.agent.name}] Bid accepted. Value to pay: {msg_data['value']}.")
                    self.agent.cpu_limit = msg_data["new_cpu"]
                    self.agent.memory_limit = msg_data["new_memory"]
                    self.agent.bandwidth_limit = msg_data["new_bandwidth"]
                    self.agent.budget -= msg_data['value']
                if msg.get_metadata("performative") == "reject-proposal":
                    msg_data = json.loads(msg.body)
                    self.agent.cpu_limit = msg_data["new_cpu"]
                    # self.agent.memory_limit = msg_data["new_memory"]
                    self.agent.bandwidth_limit = msg_data["new_bandwidth"]
                    print(f"[{self.agent.name}] Bid rejected. CPU reduced to: {self.agent.cpu_limit}. BW reduced to: {self.agent.bandwidth_limit}Mbps.")
                
                if msg.get_metadata("performative") == "inform":
                    msg_data = json.loads(msg.body)
                    new_priority = msg_data["new_priority"]
                    self.agent.base_bid = BASE_BID * new_priority
                    self.agent.income = BASE_INCOME * new_priority
                    self.agent.budget = BASE_BUDGET * new_priority
                    print(f"[{self.agent.name}] Received new priority: {new_priority}. Updated base bid: {self.agent.base_bid}, base income: {self.agent.income}, base budget: {self.agent.budget}.")
                

    class ResourceMonitoring(PeriodicBehaviour):
        async def on_start(self):
            print("[MONITORING] Starting resource monitoring behavior (runs every 5 seconds).")
        async def run(self):
            upf_name = self.agent.upf_target

            # In a thread (Phase 5): a slow Prometheus must not block the agent and make it miss the CFP
            cpu_usage = await asyncio.to_thread(self.agent.prometheus_query, upf_name, "cpu")
            memory_usage = await asyncio.to_thread(self.agent.prometheus_query, upf_name, "memory")
            bandwidth_usage = await asyncio.to_thread(self.agent.prometheus_query, upf_name, "bandwidth")

            if cpu_usage is not None:
                self.agent.cpu_usage = cpu_usage
            else:
                print("[ERROR] Failed to retrieve cpu usage from Prometheus.")
            
            if memory_usage is not None:
                self.agent.memory_usage = memory_usage
            else:
                print("[ERROR] Failed to retrieve memory usage from Prometheus.")
            
            if bandwidth_usage is not None:
                self.agent.bandwidth_usage = bandwidth_usage
            else:
                print("[ERROR] Failed to retrieve bandwidth usage from Prometheus.")
            

            income = self.agent.income  # Adjust this to change how fast they recover
            max_budget = 200.0 # Prevent infinite wealth hoarding
            
            self.agent.budget += income
            self.agent.budget = min(self.agent.budget, max_budget)
    def prometheus_query(self, upf_name, resource):
        # Query (Ex: rate(container_cpu_usage_seconds_total{namespace="pfsevaldt", pod=~"upf-.*", container="upf"}[1m]))
        if resource == "cpu":
            query = self.prom.custom_query(f"rate(container_{resource}_usage_seconds_total{{namespace=\"{NAMESPACE}\", pod=~\"{upf_name}-.*\", container=\"{upf_name}\"}}[1m])")
            # Print the result
            if query:
                resource_usage = query[0]['value'][1]
                return float(resource_usage)
            else:
                return None
        if resource == "memory":
            query = self.prom.custom_query(f"container_memory_usage_bytes{{namespace=\"{NAMESPACE}\", pod=~\"{upf_name}-.*\", container=\"{upf_name}\"}}")
            if query:
                resource_usage = query[0]['value'][1]
                return float(resource_usage)/(1024*1024)  # Convert to MiB
            else:
                return None
        if resource == "bandwidth":
            query = self.prom.custom_query(f"rate(container_network_receive_bytes_total{{namespace=\"{NAMESPACE}\", pod=~\"{upf_name}-.*\"}}[1m])")
            if query:
                resource_usage = query[0]['value'][1]
                return (float(resource_usage)*8.0) / (1024*1024)
            else:
                return None
    def fetch_initial_limits(self):

        print(f"[{self.name}] Fetching initial CPU configuration from Kubernetes...")
    
        # Connect to K8s API
        config.load_kube_config()
        v1 = client.CoreV1Api()
        
        # Find the pod for this specific UPF
        pods = v1.list_namespaced_pod(namespace=NAMESPACE, label_selector=f"app={self.upf_target}")
        
        if pods.items:
            pod = pods.items[0]
            resources = pod.spec.containers[0].resources
            
            if resources and resources.limits:
                if 'cpu' in resources.limits:
                    cpu_str = resources.limits['cpu']
                    
                    # Convert K8s format (e.g., "500m" or "1") to a Python float (0.5 or 1.0)
                    if cpu_str.endswith('m'):
                        self.cpu_limit = float(cpu_str[:-1]) / 1000.0
                    else:
                        self.cpu_limit = float(cpu_str)
                        
                    print(f"[{self.name}] Successfully loaded initial CPU Limit: {self.cpu_limit}")
                else:
                    print(f"[{self.name}] Pod found, but no CPU limit specified. Defaulting to 1.0")
                    self.cpu_limit = 1.0
                
                if 'memory' in resources.limits:
                    memory_str = resources.limits['memory']
                    if memory_str.endswith('Mi'):
                        self.memory_limit = float(memory_str[:-2])
                    elif memory_str.endswith('Gi'):
                        self.memory_limit = float(memory_str[:-2]) * 1024
                    else:
                        self.memory_limit = float(memory_str) / (1024 * 1024)  # Assume bytes if no unit
                    print(f"[{self.name}] Successfully loaded initial Memory Limit: {self.memory_limit}Mi")
                else:
                    print(f"[{self.name}] Pod found, but no Memory limit specified. Defaulting to 512Mi")
                    self.memory_limit = 512.0
                
                annotations = pod.metadata.annotations or {}
                bw_str = annotations.get("qos.projectcalico.org/egressBandwidth", None)

                if bw_str:
                    if bw_str.endswith('M'):
                        self.bandwidth_limit = float(bw_str[:-1])
                    elif bw_str.endswith('G'):
                        self.bandwidth_limit = float(bw_str[:-1]) * 1024
                    elif bw_str.endswith('k'):     # what the auctioneer writes (e.g. "9900k"; Phase 5)
                        self.bandwidth_limit = float(bw_str[:-1]) / 1000
                else:
                    self.bandwidth_limit = 100.0
                print(f"[{self.name}] Successfully loaded initial Bandwidth Limit: {self.bandwidth_limit}Mbps")
                
    async def setup(self):
        print(f"[{self.name}] Slice Agent starting...")
        self.fetch_initial_limits()

        self.cpu_usage = 0.0
        self.memory_usage = 0.0
        self.bandwidth_usage = 0.0

        self.prom = PrometheusConnect(url =URL_PROMETHEUS, disable_ssl=True)
        self.add_behaviour(self.ResourceMonitoring(period=MONITORING_PERIOD))
        self.add_behaviour(self.AuctionParticipant())
        return await super().setup()
async def main():
    
    # 1. Define the 1-10 Tiers
    PRIORITY_TIERS = {
        slice_name: {
            "weight": slice_info['initial_priority'],
            "upf": slice_info['upf']
        }
        for slice_name, slice_info in test_config['slices'].items()
    }

    # VR slices (tipo: vr, Phase 5) bid with the same utilization agent (L0, L1); the SLA agent (L2) is Phase 6
    vr_agent = (test_config.get('vr') or {}).get('agent', 'utilizacao')
    if any(s.get('tipo') == 'vr' for s in test_config['slices'].values()) and vr_agent != 'utilizacao':
        raise SystemExit(f"[ERROR] vr.agent: {vr_agent} is not available yet (the SLA agent is Phase 6). Use vr.agent: utilizacao.")

    # 2. Create Slice Agents for each tier
    slice_agents = []

    for tier_name, config in PRIORITY_TIERS.items():
        
        w = config["weight"]
        agent_jid = f"{tier_name}_slice@localhost"
        
        agent = SliceAgent(agent_jid, "password")
        agent.upf_target = config["upf"]
        
        # --- THE FORMULAS ---
        agent.priority = w
        agent.budget = BASE_BUDGET * w
        agent.income = BASE_INCOME * w
        agent.base_bid = BASE_BID * w
        
        slice_agents.append(agent)
        print(f"[{tier_name.upper()}] Initialized -> Income: {agent.income} | Base Bid: {agent.base_bid}")

    # Start the agents
    for agent in slice_agents:
        await agent.start()
        
    print("SliceAgents are running...")

    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        print("Stopping SliceAgents...")
        for agent in slice_agents:
            await agent.stop()

if __name__ == "__main__":
    spade.run(main(), embedded_xmpp_server=True)