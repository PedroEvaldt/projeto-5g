from kubernetes import client, config
from dotenv import load_dotenv
import yaml
import os

load_dotenv()
TEST_CONFIG_PATH = os.getenv("TEST_FILE")
with open(TEST_CONFIG_PATH, "r") as f:
    test_config = yaml.safe_load(f)
# --- CONFIGURATION ---
NAMESPACE = "pfsevaldt"

# Define your desired starting limits here
INITIAL_CPU = test_config["initial_parameters"]["cpu"] 
INITIAL_MEMORY = test_config["initial_parameters"]["memory"]
INITIAL_BANDWIDTH = test_config["initial_parameters"]["bandwidth"]
# ---------------------

def reset_upf_pod(v1, app_label, cpu_limit, memory_limit_mi, bw_limit_m):
    # Fetch the pod by its label
    pods = v1.list_namespaced_pod(namespace=NAMESPACE, label_selector=f"app={app_label}")
    
    if not pods.items:
        print(f"[SKIP] No pods found for app={app_label}")
        return

    for pod in pods.items:
        pod_name = pod.metadata.name
        print(f"\nResetting {pod_name} (app={app_label})...")

        # ---------------------------------------------------------
        # 1. Patch Bandwidth (Annotations via Strategic Merge Patch)
        # ---------------------------------------------------------
        bw_patch = {
            "metadata": {
                "annotations": {
                    "qos.projectcalico.org/ingressBandwidth": f"{int(bw_limit_m)}M",
                    "qos.projectcalico.org/egressBandwidth": f"{int(bw_limit_m)}M",
                    "qos.projectcalico.org/ingressBurst": f"1M", 
                    "qos.projectcalico.org/egressBurst": f"1M"
                }
            }
        }
        try:
            v1.patch_namespaced_pod(name=pod_name, namespace=NAMESPACE, body=bw_patch)
            print(f"  [SUCCESS] Bandwidth set to {int(bw_limit_m)}M")
        except Exception as e:
            print(f"  [ERROR] Bandwidth patch failed: {e}")

        # ---------------------------------------------------------
        # 2. Patch CPU & Memory (Resources via JSON Patch)
        # ---------------------------------------------------------
        # Calculate memory request as half of limit to maintain 'Burstable' QoS
        mem_request = int(memory_limit_mi / 2) 
        
        resource_patch = [
            {"op": "replace", "path": "/spec/containers/0/resources/requests/cpu", "value": f"{int(cpu_limit * 1000)}m"},
            {"op": "replace", "path": "/spec/containers/0/resources/limits/cpu", "value": f"{int(cpu_limit * 1000)}m"},
            # {"op": "replace", "path": "/spec/containers/0/resources/requests/memory", "value": f"{mem_request}Mi"},
            # {"op": "replace", "path": "/spec/containers/0/resources/limits/memory", "value": f"{int(memory_limit_mi)}Mi"}
        ]
        try:
            v1.patch_namespaced_pod_resize(name=pod_name, namespace=NAMESPACE, body=resource_patch)
            print(f"  [SUCCESS] CPU set to {cpu_limit}, Memory set to {int(memory_limit_mi)}Mi")
        except Exception as e:
            print(f"  [ERROR] Resource patch failed: {e}")


def main():
    print(f"Connecting to Kubernetes cluster in namespace '{NAMESPACE}'...")
    try:
        config.load_kube_config()
        v1 = client.CoreV1Api()
    except Exception as e:
        print(f"Failed to connect to K8s: {e}")
        return

    print(f"Target Initial State -> CPU: {INITIAL_CPU} | MEM: {INITIAL_MEMORY}Mi | BW: {INITIAL_BANDWIDTH}M")

    # Generate the list of your UPF labels: ["upf", "upf2", "upf3" ... "upf9"]
    target_apps = [f"upf{i}" for i in range(1,10)]

    for app in target_apps:
        reset_upf_pod(v1, app, INITIAL_CPU, INITIAL_MEMORY, INITIAL_BANDWIDTH)

    print("\n[DONE] All UPFs have been reset to their initial baseline!")

if __name__ == "__main__":
    main()