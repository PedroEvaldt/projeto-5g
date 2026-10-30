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
from spade.template import Template
from kubernetes import client, config
from dotenv import load_dotenv

import leilao

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

# Phase 5: per-slice floors (default = the global minimums, so a video-only YAML behaves as before).
# The VR slice (tipo: vr) has a conditional floor: it only holds while the VR is active (VR_JOIN/VR_LEAVE),
# and comes from vr/calibracao.yaml unless the slice sets floor_cpu/floor_bw. vr.floor: off (L0) disables it.
MINIMOS = {"cpu": MINIMUM_CPU, "bw": MINIMUM_BW, "mem": MINIMUM_MEMORY}
POOL = {"cpu": CORE_CPU_LIMIT, "bw": CORE_BW_LIMIT, "mem": CORE_MEMORY_LIMIT}
CALIBRACAO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vr", "calibracao.yaml")
VR_FLOOR_ON = str((test_config.get('vr') or {}).get('floor', True)).lower() in ("true", "on", "1")   # YAML reads `on` as True


def load_floors():
    """{slice: {"cpu", "bw", "mem"}} and the set of slices with a conditional floor (the VR)."""
    floors, conditional = {}, set()
    for name, s in test_config['slices'].items():
        floor = dict(MINIMOS)
        if s.get('tipo') == 'vr':
            conditional.add(name)
            if VR_FLOOR_ON:
                with open(CALIBRACAO) as f:
                    calib = yaml.safe_load(f)['piso']
                floor["cpu"] = float(s.get('floor_cpu', calib['cpu']))
                floor["bw"] = float(s.get('floor_bw', calib['banda_mbps']))
        else:
            floor["cpu"] = float(s.get('floor_cpu', MINIMUM_CPU))
            floor["bw"] = float(s.get('floor_bw', MINIMUM_BW))
        floors[name] = floor
    return floors, conditional


def slice_of(sender):
    """'gold_slice@localhost/...' -> 'gold'"""
    return str(sender).split("@")[0].removesuffix("_slice")


class ResourceAgent(Agent):
    class AuctioneerBehavior(PeriodicBehaviour):
        async def on_start(self):
            print(f"[AUCTION] Initializing auctioneer behavior (runs every {AUCTION_PERIOD} seconds).")
            async with self.agent.lock:
                await self.initial_limits()

        async def initial_limits(self):
            ag = self.agent
            ag.auction_id = 0
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

            # Over the pool? Cut from the slices, in reply order, down to each slice's floor (leilao.ajustar_inicio)
            order, limits = [], {}
            for reply in replies:
                reply_data = json.loads(reply.body)
                name = slice_of(reply.sender)
                order.append(name)
                limits[name] = {"cpu": float(reply_data["cpu_limit"]), "bw": float(reply_data["bw_limit"]),
                                "mem": float(reply_data["memory_limit"])}
                ag.upf_of[name] = reply_data["upf_target"]
                ag.jid_of[name] = str(reply.sender)
            new_limits, ag.free, cuts = leilao.ajustar_inicio(limits, order, POOL, ag.current_floors())
            for name, resource, value in cuts:
                if resource == "cpu":
                    await asyncio.to_thread(ag.update_pod_cpu, ag.upf_of[name], value)
                    body = {"new_cpu": value}
                elif resource == "bw":
                    await asyncio.to_thread(ag.update_pod_bandwidth, ag.upf_of[name], value)
                    body = {"new_bw": value}
                else:
                    continue    # memory is counted but never applied (it cannot be reduced on a running pod)
                msg = Message(to=ag.jid_of[name])
                msg.set_metadata("performative", "adjustment")
                msg.body = json.dumps(body)
                await self.send(msg)
            ag.limits = {name: {"cpu": v["cpu"], "bw": v["bw"]} for name, v in new_limits.items()}
            await asyncio.sleep(2)

            print(f"[AUCTION] Free cluster resources calculated: CPU={ag.free['cpu']}, Memory={ag.free['mem']}Mi, BW={ag.free['bw']}Mbps.")

            ag.log_file = RESULTS_DIR + AUCTION_LOG_FILE
            with open(ag.log_file, mode='w', newline='') as file:
                writer = csv.writer(file)
                # CPU_Applied (Phase 5): the CPU limit the kubelet actually applied (a resize can be Deferred/Infeasible)
                writer.writerow(["Timestamp", "Auction_ID", "Agent", "Result", "Bid_Value", "Price_Paid", "CPU_Allocated", "BW_Allocated", "CPU_Applied"])
            print(f"[AUCTION] Logging initialized in {ag.log_file}")


        async def run(self):
            # One lock for the regular round and the extra round at VR_JOIN: they never interleave
            async with self.agent.lock:
                await self.auction_round()

        async def auction_round(self):
            ag = self.agent
            ag.auction_id += 1
            print(f"[AUCTION] Starting auction #{ag.auction_id} for resource allocation.")

            # Broadcast for auction's participants
            for agent in self.slice_agents:
                msg = Message(to=agent)
                msg.set_metadata("performative", "cfp")
                msg.body = json.dumps({ "auction_id": ag.auction_id })
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
            print(f"[AUCTION] Auction #{ag.auction_id} ended. Total bids received: {len(bids)}")

            if not bids:
                print(f"[AUCTION] No bids received for this auction. Resources remain as it is.")
                return

            # Determine the winning bid basen on Vickrey auction rules (highest bidder wins but pays the second-highest bid price).
            # The arithmetic is in leilao.rodada (identical to the original when the floors are the global minimums).
            bids_in = []
            for bid in bids:
                bid_data = json.loads(bid.body)
                name = slice_of(bid.sender)
                ag.upf_of[name] = bid_data["upf_target"]
                ag.jid_of[name] = str(bid.sender)
                bids_in.append({"fatia": name, "lance": float(bid_data["bid"]),
                                "cpu_alvo": float(bid_data["cpu_target"]), "cpu": float(bid_data["cpu_limit"]),
                                "bw_alvo": float(bid_data["bw_target"]), "bw": float(bid_data["bw_limit"]),
                                "mem_alvo": float(bid_data["memory_target"]), "mem": float(bid_data["memory_limit"])})
            result = leilao.rodada(bids_in, ag.free, ag.current_floors())
            ag.free = result["livre"]
            winner, value = result["vencedor"], result["preco"]

            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            rows = []
            # Losers from the smallest limit to the largest (leilao.rodada already returns them in this order)
            for loser in result["perdedores"]:
                name = loser["fatia"]
                new_cpu, new_bandwidth = result["novos"][name]["cpu"], result["novos"][name]["bw"]
                msg = Message(to=ag.jid_of[name])
                agent_name = ag.jid_of[name].split("@")[0] # Clean up the name for logging purposes
                print(f"[AUCTION] Loser: {ag.jid_of[name]} with bid {loser['lance']}. CPU reduced to: {new_cpu}. BW reduced to: {new_bandwidth}. ")
                await asyncio.to_thread(ag.update_pod_cpu, ag.upf_of[name], new_cpu)
                await asyncio.to_thread(ag.update_pod_bandwidth, ag.upf_of[name], new_bandwidth)

                msg.set_metadata("performative", "reject-proposal")
                msg.body = json.dumps({ "new_cpu": new_cpu,
                                    "new_bandwidth": new_bandwidth})
                rows.append([timestamp, ag.auction_id, agent_name, "LOSER", loser["lance"], 0.0, f"{new_cpu:.2f}", f"{new_bandwidth:.2f}", name])
                await self.send(msg)

            name = winner["fatia"]
            msg_winner = Message(to=ag.jid_of[name])
            agent_name = ag.jid_of[name].split("@")[0] # Clean up the name for logging purposes
            new_cpu, new_bandwidth = result["novos"][name]["cpu"], result["novos"][name]["bw"]
            await asyncio.to_thread(ag.update_pod_cpu, ag.upf_of[name], new_cpu)
            await asyncio.to_thread(ag.update_pod_bandwidth, ag.upf_of[name], new_bandwidth)
            msg_winner.set_metadata("performative", "accept-proposal")
            msg_winner.body = json.dumps({ "value": value , "new_cpu": new_cpu, "new_memory": winner["mem_alvo"], "new_bandwidth": new_bandwidth})

            print(f"[AUCTION] Winner: {ag.jid_of[name]} with bid {winner['lance']}. Price to pay: {value}. CPU allocated: {new_cpu}. BW allocated: {new_bandwidth}.")
            rows.append([timestamp, ag.auction_id, agent_name, "WINNER", winner["lance"], value, f"{new_cpu:.2f}", f"{new_bandwidth:.2f}", name])
            await self.send(msg_winner)

            for n, v in result["novos"].items():
                ag.limits[n] = dict(v)
            await ag.log_rows(rows)

    class VRJoinLeave(CyclicBehaviour):
        """VR_JOIN / VR_LEAVE from deploy_test.py (Phase 5). On join, the VR floor holds at once (extra round:
        first from the free pool, then from the other slices in equal parts, down to their floors). On leave,
        the floor is released: the VR goes back to the global minimum, and what it had above it returns to the
        free pool, to be auctioned from the next round on."""
        async def run(self):
            msg = await self.receive(timeout=10)
            if not msg:
                return
            ag = self.agent
            name = json.loads(msg.body).get("fatia")
            joined = msg.get_metadata("performative") == "vr_join"
            if name not in ag.conditional:
                print(f"[VR] Ignoring {msg.get_metadata('performative')} for '{name}': not a VR slice in the YAML.")
                return
            async with ag.lock:
                if joined:
                    ag.vr_active.add(name)
                else:
                    ag.vr_active.discard(name)
                if not VR_FLOOR_ON:
                    print(f"[VR] {name} {'joined' if joined else 'left'}; vr.floor is off (L0): the auction changes nothing.")
                    return
                if name not in ag.limits:
                    print(f"[VR] {name} {'joined' if joined else 'left'} before the auctioneer knew its limits; the floor holds from the next round.")
                    return
                if joined:
                    changed, ag.free, missing = leilao.aplicar_piso(name, ag.limits, ag.free, ag.current_floors())
                else:
                    new, ag.free = leilao.liberar(name, ag.limits, ag.free, MINIMOS)
                    changed, missing = {name: new}, {}
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                rows = []
                for n, v in changed.items():
                    await asyncio.to_thread(ag.update_pod_cpu, ag.upf_of[n], v["cpu"])
                    await asyncio.to_thread(ag.update_pod_bandwidth, ag.upf_of[n], v["bw"])
                    adj = Message(to=ag.jid_of[n])
                    adj.set_metadata("performative", "adjustment")
                    adj.body = json.dumps({"new_cpu": v["cpu"], "new_bw": v["bw"]})
                    await self.send(adj)
                    ag.limits[n] = dict(v)
                    result = ("FLOOR_JOIN" if joined else "FLOOR_LEAVE") if n == name else "FLOOR_DONOR"
                    rows.append([timestamp, ag.auction_id, ag.jid_of[n].split("@")[0], result, "", 0.0, f"{v['cpu']:.2f}", f"{v['bw']:.2f}", n])
                print(f"[VR] {name} {'joined: floor applied' if joined else 'left: floor released'}. "
                      f"New limits: { {n: (round(v['cpu'], 3), round(v['bw'], 2)) for n, v in changed.items()} }. "
                      f"Free: CPU={ag.free['cpu']:.3f}, BW={ag.free['bw']:.2f}Mbps.")
                if any(m > 0 for m in missing.values()):
                    print(f"[VR] WARNING: could not reach the floor of {name}; missing CPU={missing['cpu']:.3f}, BW={missing['bw']:.2f}Mbps (the other slices are at their floors).")
                await ag.log_rows(rows)

    def current_floors(self):
        """The floor that holds now: the VR's own floor only while it is active (leilao.piso_efetivo)."""
        return leilao.piso_efetivo(self.floors, MINIMOS, self.conditional, self.vr_active)

    async def log_rows(self, rows):
        """Writes rows to the auction_history.csv with the CPU the kubelet actually applied. The last field of
        each row is the slice name, replaced by the applied CPU (read 2 s after the resize, in cores)."""
        await asyncio.sleep(2)
        applied = {}
        for row in rows:
            name = row[-1]
            if name not in applied:
                applied[name] = await asyncio.to_thread(self.read_applied_cpu, self.upf_of[name])
            row[-1] = applied[name]
        with open(self.log_file, mode='a', newline='') as file:
            csv.writer(file).writerows(rows)

    def read_applied_cpu(self, upf_name):
        """CPU limit in status.containerStatuses (what the kubelet applied), in cores; '' if not available."""
        try:
            pods = self.v1.list_namespaced_pod(namespace=NAMESPACE, label_selector=f"app={upf_name}")
            cs = pods.items[0].status.container_statuses[0]
            cpu = cs.resources.limits["cpu"]
            return f"{(float(cpu[:-1]) / 1000 if cpu.endswith('m') else float(cpu)):.2f}"
        except Exception as e:
            print(f"[WARNING] Could not read the applied CPU of {upf_name}: {e}")
            return ""

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

        # Auction state shared by the regular round and the VR extra round (Phase 5)
        self.lock = asyncio.Lock()
        self.limits, self.free, self.upf_of, self.jid_of = {}, {"cpu": 0.0, "bw": 0.0, "mem": 0.0}, {}, {}
        self.auction_id, self.log_file = 0, RESULTS_DIR + AUCTION_LOG_FILE
        self.floors, self.conditional = load_floors()
        self.vr_active = set()      # VR slices start out of the network (their floor holds only after VR_JOIN)

        # The floors, all at once, must fit in the pool (Phase 5.4)
        fits = True
        for resource, (total_floor, pool, ok) in leilao.conferir_pisos(self.floors, POOL).items():
            print(f"[AUCTION] Sum of the floors ({resource}): {total_floor} of {pool} -> {'ok' if ok else 'DOES NOT FIT'}")
            fits = fits and ok
        if not fits:
            print("[ERROR] The slice floors do not fit in the pool: stopping the auctioneer. Check floor_cpu/floor_bw and vr/calibracao.yaml.")
            await self.stop()
            return

        # self.add_behaviour(self.ResourceBehavior())
        # The auctioneer only reads the slices' replies; VR_JOIN/VR_LEAVE go to their own behaviour
        auction_msgs = Template(metadata={"performative": "limits"}) | Template(metadata={"performative": "propose"})
        self.add_behaviour(self.AuctioneerBehavior(period=AUCTION_PERIOD), auction_msgs)
        if self.conditional:
            print(f"[AUCTION] VR slices with a conditional floor: {sorted(self.conditional)}; "
                  f"floor {'on' if VR_FLOOR_ON else 'off (L0)'}: {[self.floors[n] for n in sorted(self.conditional)]}")
            vr_msgs = Template(metadata={"performative": "vr_join"}) | Template(metadata={"performative": "vr_leave"})
            self.add_behaviour(self.VRJoinLeave(), vr_msgs)
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
