import yaml
import json
import time
import spade
import asyncio
import csv
import os
from datetime import datetime
from spade.agent import Agent
from spade.behaviour import CyclicBehaviour
from spade.behaviour import PeriodicBehaviour
from spade.message import Message
from kubernetes import client, config
from dotenv import load_dotenv

load_dotenv()
TEST_CONFIG_PATH = os.getenv("TEST_FILE")
with open(TEST_CONFIG_PATH, "r") as f:
    test_config = yaml.safe_load(f)

# CONSTANTS
NAMESPACE = "pfsevaldt"
MINIMUM_CPU = test_config['auction']['minimum_cpu']
MINIMUM_BW = test_config['auction']['minimum_bw']
MINIMUM_MEMORY = test_config['auction']['minimum_memory']

CORE_CPU_LIMIT = test_config['auction']['total_cpu_pool']
CORE_MEMORY_LIMIT = test_config['auction']['total_memory_pool']
CORE_BW_LIMIT = test_config['auction']['total_bw_pool']
AUCTION_PERIOD = test_config['auction']['period_seconds']
RESULTS_DIR = test_config['monitoring']['csv_output_dir']
AUCTION_LOG_FILE = test_config['monitoring']['auction_log_file']
os.makedirs(RESULTS_DIR, exist_ok=True)

class ResourceAgent(Agent):
    class AuctioneerBehavior(PeriodicBehaviour):
        async def on_start(self):
            print("[AUCTION] Initializing auctioneer behavior (runs every 5 seconds).")
            self.auction_id = 0
            # List of auction's participants
            self.slice_agents = [slice_name for slice_name in test_config['slices'].keys()]
            self.slice_agents = [f"{agent}_slice@localhost" for agent in self.slice_agents]
            
            n_slices = len(self.slice_agents)
            cpu_limit = CORE_CPU_LIMIT/n_slices
            memory_limit = CORE_MEMORY_LIMIT/n_slices
            bw_limit = CORE_BW_LIMIT/n_slices

            # Broadcast for auction's participants
            for agent in self.slice_agents:
                msg = Message(to=agent)
                msg.set_metadata("performative", "scout")
                msg.body = json.dumps({
                    "base_cpu_limit" : cpu_limit,
                    "base_memory_limit" : memory_limit,
                    "base_bw_limit" : bw_limit
                })
                await self.send(msg)
                print(f"Message sent to {agent}.")
            
            replies = []
            time_limit = 2.0
            start_time = time.time()

            while time.time() - start_time < time_limit:
                time_remaining = time_limit - (time.time() - start_time)
                if time_remaining <= 0:
                    break
                msg = await self.receive(timeout=time_remaining)
                if msg and msg.get_metadata("performative") == "limits":
                    print(f"[AUCTION] Received reply from {msg.sender}: {msg.body}")
                    replies.append(msg)
            

            self.free_cluster_cpu = CORE_CPU_LIMIT - sum([float(json.loads(reply.body)["cpu_limit"]) for reply in replies])
            self.free_cluster_bw = CORE_BW_LIMIT - sum([float(json.loads(reply.body)["bw_limit"]) for reply in replies])
            self.free_cluster_memory = CORE_MEMORY_LIMIT - sum([float(json.loads(reply.body)["memory_limit"]) for reply in replies])

            for reply in replies:
                reply_data = json.loads(reply.body)
                if self.free_cluster_cpu < 0:
                    if reply_data["cpu_limit"] > MINIMUM_CPU:
                        excess_cpu = min(reply_data["cpu_limit"]-MINIMUM_CPU, -self.free_cluster_cpu)
                        reply_data["cpu_limit"] -= excess_cpu
                        self.agent.update_pod_cpu(reply_data["upf_target"], reply_data["cpu_limit"])
                        self.free_cluster_cpu += excess_cpu
                        msg = Message(to=str(reply.sender))
                        msg.set_metadata("performative", "adjustment")
                        msg.body = json.dumps({ "new_cpu": reply_data["cpu_limit"] })
                        await self.send(msg)
                if self.free_cluster_bw < 0:
                    if reply_data["bw_limit"] > MINIMUM_BW:
                        excess_bw = min(reply_data["bw_limit"]-MINIMUM_BW, -self.free_cluster_bw)
                        reply_data["bw_limit"] -= excess_bw
                        self.agent.update_pod_bandwidth(reply_data["upf_target"], reply_data["bw_limit"])
                        self.free_cluster_bw += excess_bw
                        msg = Message(to=str(reply.sender))
                        msg.set_metadata("performative", "adjustment")
                        msg.body = json.dumps({ "new_bw": reply_data["bw_limit"] })
                        await self.send(msg)
                if self.free_cluster_memory < 0:
                    if reply_data["memory_limit"] > MINIMUM_MEMORY:
                        excess_memory = min(reply_data["memory_limit"]-MINIMUM_MEMORY, -self.free_cluster_memory)
                        reply_data["memory_limit"] -= excess_memory
                        #self.agent.update_pod_memory(reply_data["upf_target"], reply_data["memory_limit"])
                        self.free_cluster_memory += excess_memory
                        # msg = Message(to=str(reply.sender))
                        # msg.set_metadata("performative", "adjustment")
                        # msg.body = json.dumps({ "new_mem": reply_data["memory_limit"] })
                        # await self.send(msg)
                if self.free_cluster_cpu >= 0 and self.free_cluster_bw >= 0 and self.free_cluster_memory >= 0:
                    break
            await asyncio.sleep(2)

            print(f"[AUCTION] Free cluster resources calculated: CPU={self.free_cluster_cpu}, Memory={self.free_cluster_memory}Mi, BW={self.free_cluster_bw}Mbps.")

            self.log_file = RESULTS_DIR + AUCTION_LOG_FILE
            with open(self.log_file, mode='w', newline='') as file:
                writer = csv.writer(file)
                writer.writerow(["Timestamp", "Auction_ID", "Agent", "Result", "Bid_Value", "Price_Paid", "CPU_Allocated", "BW_Allocated"])
            print(f"[AUCTION] Logging initialized in {self.log_file}")
            
        
        async def run(self):
            self.auction_id += 1
            print(f"[AUCTION] Starting auction #{self.auction_id} for resource allocation.")

            # Broadcast for auction's participants
            for agent in self.slice_agents:
                msg = Message(to=agent)
                msg.set_metadata("performative", "cfp")
                msg.body = json.dumps({ "auction_id": self.auction_id })
                await self.send(msg)
                print(f"Message sent to {agent}.")
            
            # Collect bids from participants
            print(f"[AUCTION] CFP sent to participants. Awaiting bids...")
            bids_dict = {}
            time_limit = 2.0
            start_time = time.time()

            while time.time() - start_time < time_limit:
                time_remaining = time_limit - (time.time() - start_time)
                if time_remaining <= 0:
                    break
                msg = await self.receive(timeout=time_remaining)
                if msg and msg.get_metadata("performative") == "propose":
                    print(f"[AUCTION] Received bid from {msg.sender}: {msg.body}")
                    bids_dict[str(msg.sender)] = msg

            bids = list(bids_dict.values())
            print(f"[AUCTION] Auction #{self.auction_id} ended. Total bids received: {len(bids)}")

            if not bids:
                print(f"[AUCTION] No bids received for this auction. Resources remain as it is.")
                return
            
            # Determine the winning bid basen on Vickrey auction rules (highest bidder wins but pays the second-highest bid price)
            
            structured_bids = []
            for bid in bids:
                bid_data = json.loads(bid.body)
                bid_value = float(bid_data["bid"])
                cpu_target = float(bid_data["cpu_target"])
                cpu_limit = float(bid_data["cpu_limit"])
                memory_target = float(bid_data["memory_target"])
                memory_limit = float(bid_data["memory_limit"])
                memory_usage = float(bid_data["memory_usage"])
                bw_target = float(bid_data["bw_target"])
                bw_limit = float(bid_data["bw_limit"])

                structured_bids.append({
                    "sender": bid.sender, 
                    "bid": bid_value, 
                    "upf_target": bid_data["upf_target"],
                    "cpu_target": cpu_target,
                    "cpu_limit": cpu_limit,
                    "memory_target": memory_target,
                    "memory_limit": memory_limit,
                    "memory_usage": memory_usage,
                    "bw_target": bw_target,
                    "bw_limit": bw_limit
                })
            structured_bids.sort(key=lambda x: x["bid"], reverse=True)
            
            winner = structured_bids.pop(0)
            number_losers = len(structured_bids)
            value = structured_bids[0]["bid"] if number_losers > 0 else winner["bid"]
            # Calculate the quantity of CPU reduction for the loser(s) based on winner's cpu target

            requested_cpu = winner["cpu_target"]-winner["cpu_limit"]
            requested_bw = winner["bw_target"]-winner["bw_limit"]
            requested_memory = winner["memory_target"]-winner["memory_limit"]

            if requested_cpu >= self.free_cluster_cpu:
                actual_extracted_cpu = self.free_cluster_cpu
                cpu_reduce = (requested_cpu-self.free_cluster_cpu)/(number_losers) if (number_losers > 0) else 0
                self.free_cluster_cpu = 0
            else:
                actual_extracted_cpu = requested_cpu
                cpu_reduce = 0
                self.free_cluster_cpu -= requested_cpu
            
            if requested_memory >= self.free_cluster_memory:
                actual_extracted_memory = self.free_cluster_memory
                memory_reduce = (requested_memory-self.free_cluster_memory)/(number_losers) if (number_losers > 0) else 0
                self.free_cluster_memory = 0
            else:
                actual_extracted_memory = requested_memory
                memory_reduce = 0
                self.free_cluster_memory -= requested_memory
            
            if requested_bw >= self.free_cluster_bw:
                actual_extracted_bw = self.free_cluster_bw
                bw_reduce = (requested_bw-self.free_cluster_bw)/(number_losers) if (number_losers > 0) else 0
                self.free_cluster_bw = 0
            else:
                actual_extracted_bw = requested_bw
                bw_reduce = 0
                self.free_cluster_bw -= requested_bw
            
            with open(self.log_file, mode='a', newline='') as file:
                writer = csv.writer(file)
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                
                # Ordena do menor limite para o maior, para que os mais pobres declarem falência primeiro 
                # e repassem a dívida aos mais ricos.
                structured_bids = sorted(structured_bids, key=lambda x: (x["bw_limit"], x["cpu_limit"]))
                for i, bid in enumerate(structured_bids):
                    msg = Message(to=str(bid["sender"]))
                    agent_name = str(bid["sender"]).split("@")[0] # Clean up the name for logging purposes
                    new_cpu = bid["cpu_limit"]-cpu_reduce
                    new_bandwidth = bid["bw_limit"]-bw_reduce

                    if new_cpu < MINIMUM_CPU:
                        diff_cpu = bid["cpu_limit"]-MINIMUM_CPU
                        actual_extracted_cpu += diff_cpu
                        new_cpu = MINIMUM_CPU
                        cpu_reduce += (cpu_reduce - diff_cpu)/(number_losers - i - 1) if (number_losers - i -1) > 0 else cpu_reduce
                    else:
                        actual_extracted_cpu += cpu_reduce


                    if new_bandwidth < MINIMUM_BW:
                        diff_bw = bid["bw_limit"]-MINIMUM_BW
                        actual_extracted_bw += diff_bw
                        new_bandwidth = MINIMUM_BW
                        bw_reduce += (bw_reduce - diff_bw)/(number_losers - i - 1) if (number_losers - i -1) > 0 else bw_reduce
                    else:
                        actual_extracted_bw += bw_reduce
                    
                    # It is not possible to dinamically reduce memory
                    # new_memory = max(bid["memory_limit"]-memory_reduce, memory_usage*1.2, 128.0)
                    # new_bandwidth = max(bid["bw_limit"]-bw_reduce, 1.0)
                    print(f"[AUCTION] Loser: {bid['sender']} with bid {bid['bid']}. CPU reduced to: {new_cpu}. BW reduced to: {new_bandwidth}. ")
                    self.agent.update_pod_cpu(bid["upf_target"], new_cpu)
                    # self.agent.update_pod_memory(bid["upf_target"], new_memory)
                    self.agent.update_pod_bandwidth(bid["upf_target"], new_bandwidth)

                    msg.set_metadata("performative", "reject-proposal")
                    msg.body = json.dumps({ "new_cpu": new_cpu, 
                                        #"new_memory": new_memory, 
                                        "new_bandwidth": new_bandwidth})
                    
                    writer.writerow([timestamp, self.auction_id, agent_name, "LOSER", bid["bid"], 0.0, f"{new_cpu:.2f}", f"{new_bandwidth:.2f}"])

                    await self.send(msg)
                
                
                

                msg_winner = Message(to=str(winner["sender"]))
                agent_name = str(winner["sender"]).split("@")[0] # Clean up the name for logging purposes

                new_cpu = winner["cpu_limit"]+actual_extracted_cpu
                new_bandwidth = winner["bw_limit"]+actual_extracted_bw
                self.agent.update_pod_cpu(winner["upf_target"], new_cpu)
                #self.agent.update_pod_memory(winner["upf_target"], winner["memory_target"])
                self.agent.update_pod_bandwidth(winner["upf_target"], new_bandwidth)
                msg_winner.set_metadata("performative", "accept-proposal")
                msg_winner.body = json.dumps({ "value": value , "new_cpu": new_cpu, "new_memory": winner["memory_target"], "new_bandwidth": new_bandwidth})
                
                print(f"[AUCTION] Winner: {winner['sender']} with bid {winner['bid']}. Price to pay: {value}. CPU allocated: {new_cpu}. BW allocated: {new_bandwidth}.")

                writer.writerow([timestamp, self.auction_id, agent_name, "WINNER", winner["bid"], value, f"{new_cpu:.2f}", f"{new_bandwidth:.2f}"])
                    
                await self.send(msg_winner)

                

    def update_pod_cpu(self, upf_name, new_cpu):
        try:
            # Fetch the corresponding pod
            pods = self.v1.list_namespaced_pod(namespace=NAMESPACE, label_selector=f"app={upf_name}")
            if pods.items:
                for pod in pods.items:
                    # Update the CPU resource request/limit
                    pod_name = pod.metadata.name
                    # Create a patch to update the CPU resources
                    patch = [
                        {
                            "op": "replace",
                            "path": "/spec/containers/0/resources/requests/cpu",
                            "value": f"{int(new_cpu * 1000)}m"
                        },
                        {
                            "op": "replace",
                            "path": "/spec/containers/0/resources/limits/cpu",
                            "value": f"{int(new_cpu * 1000)}m"
                        }
                    ]
                    self.v1.patch_namespaced_pod_resize(name=pod_name, namespace=NAMESPACE, body=patch)
                    print(f"[SUCCESS] Updated CPU for pod {pod_name} to {new_cpu}")
            else:
                print(f"[ERROR] No pods found for UPF {upf_name}")
        except Exception as e:
            print(f"[ERROR] Failed to update CPU for UPF {upf_name}: {e}")

    def update_pod_bandwidth(self, upf_name, new_bandwidth):
        try:
            pods = self.v1.list_namespaced_pod(namespace=NAMESPACE, label_selector=f"app={upf_name}")
            if pods.items:
                for pod in pods.items:
                    pod_name = pod.metadata.name
                    bw_kilo = int(new_bandwidth * 1000)
                    patch = {
                        "metadata": {
                            "annotations" : {
                                "qos.projectcalico.org/ingressBandwidth": f"{bw_kilo}k",
                                "qos.projectcalico.org/egressBandwidth": f"{bw_kilo}k"
                            }
                        }
                    }
                    self.v1.patch_namespaced_pod(name=pod_name, namespace=NAMESPACE, body=patch)
                    print(f"[SUCCESS] Updated BANDWIDTH for pod {pod_name} to {new_bandwidth}")
            else:
                print(f"[ERROR] No pods found for UPF {upf_name}")
        except Exception as e:
            print(f"[ERROR] Failed to update BANDWIDTH for UPF {upf_name}: {e}")
    
    def update_pod_memory(self, upf_name, new_memory):
        try:
            # Fetch the corresponding pod
            pods = self.v1.list_namespaced_pod(namespace=NAMESPACE, label_selector=f"app={upf_name}")
            if pods.items:
                for pod in pods.items:
                    # Update the CPU resource request/limit
                    pod_name = pod.metadata.name
                    # Create a patch to update the CPU resources
                    patch = [
                        {
                            "op": "replace",
                            "path": "/spec/containers/0/resources/requests/memory",
                            "value": f"{int(new_memory/2)}Mi"
                        },
                        {
                            "op": "replace",
                            "path": "/spec/containers/0/resources/limits/memory",
                            "value": f"{int(new_memory)}Mi"
                        }
                    ]
                    self.v1.patch_namespaced_pod_resize(name=pod_name, namespace=NAMESPACE, body=patch)
                    print(f"[SUCCESS] Updated MEMORY for pod {pod_name} to {new_memory}")
            else:
                print(f"[ERROR] No pods found for UPF {upf_name}")
        except Exception as e:
            print(f"[ERROR] Failed to update MEMORY for UPF {upf_name}: {e}")            

    async def setup(self):
        print("ResourceAgent starting...")
        try:
            # Load Kubernetes configuration and initialize the API client
            config.load_kube_config()
            self.v1 = client.CoreV1Api()
            print("ResourceAgent started and connected to Kubernetes cluster.")
        except Exception as e:
            print(f"Failed to connect to Kubernetes cluster: {e}")
            await self.agent.stop()
        # self.add_behaviour(self.ResourceBehavior())
        self.add_behaviour(self.AuctioneerBehavior(period=30))
        return await super().setup() 

async def main():
    resource_agent = ResourceAgent("resource_agent@localhost", "password")
    await resource_agent.start()
    print("ResourceAgent is running...")
    try:
        while resource_agent.is_alive():
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        print("Stopping ResourceAgent...")
    await resource_agent.stop()

if __name__ == "__main__":
    spade.run(main(), embedded_xmpp_server=False)