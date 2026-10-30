"""
Testes do leilao.py, sem cluster (Fase 5).

1. Equivalência: com os pisos iguais aos mínimos globais, ajustar_inicio e rodada dão exatamente o que o
   leiloeiro original dava. As funções original_* abaixo são cópias das contas do resource_agent.py de antes
   da Fase 5 (commit 89c4f28f), com as mensagens e as chamadas ao Kubernetes trocadas por listas.
2. Pisos: nenhuma perdedora desce abaixo do seu piso, e o recurso se conserva.
3. Piso condicional e rodada extra: aplicar_piso, liberar, piso_efetivo e conferir_pisos.

Uso (de dentro de spade_agents/):
  ../venv/bin/python -m unittest test_leilao -v
"""

import random
import unittest

import leilao

MIN = {"cpu": 0.01, "bw": 1.0, "mem": 64.0}


# --- Cópias do código original (resource_agent.py antes da Fase 5) ---

def original_inicio(replies, pool):
    """on_start do AuctioneerBehavior: replies na ordem de chegada, cada uma {cpu_limit, bw_limit, memory_limit}."""
    replies = [dict(r) for r in replies]
    free_cpu = pool["cpu"] - sum([float(r["cpu_limit"]) for r in replies])
    free_bw = pool["bw"] - sum([float(r["bw_limit"]) for r in replies])
    free_mem = pool["mem"] - sum([float(r["memory_limit"]) for r in replies])
    for reply_data in replies:
        if free_cpu < 0:
            if reply_data["cpu_limit"] > MIN["cpu"]:
                excess_cpu = min(reply_data["cpu_limit"] - MIN["cpu"], -free_cpu)
                reply_data["cpu_limit"] -= excess_cpu
                free_cpu += excess_cpu
        if free_bw < 0:
            if reply_data["bw_limit"] > MIN["bw"]:
                excess_bw = min(reply_data["bw_limit"] - MIN["bw"], -free_bw)
                reply_data["bw_limit"] -= excess_bw
                free_bw += excess_bw
        if free_mem < 0:
            if reply_data["memory_limit"] > MIN["mem"]:
                excess_memory = min(reply_data["memory_limit"] - MIN["mem"], -free_mem)
                reply_data["memory_limit"] -= excess_memory
                free_mem += excess_memory
        if free_cpu >= 0 and free_bw >= 0 and free_mem >= 0:
            break
    return replies, {"cpu": free_cpu, "bw": free_bw, "mem": free_mem}


def original_rodada(bids, free):
    """run do AuctioneerBehavior: bids na ordem de chegada. Devolve o vencedor, o preço, as perdedoras
    (na ordem de aplicar, com o novo cpu e bw), o novo do vencedor e a sobra."""
    free_cpu, free_bw, free_mem = free["cpu"], free["bw"], free["mem"]
    structured_bids = [dict(b) for b in bids]
    structured_bids.sort(key=lambda x: x["bid"], reverse=True)
    winner = structured_bids.pop(0)
    number_losers = len(structured_bids)
    value = structured_bids[0]["bid"] if number_losers > 0 else winner["bid"]
    requested_cpu = winner["cpu_target"] - winner["cpu_limit"]
    requested_bw = winner["bw_target"] - winner["bw_limit"]
    requested_memory = winner["memory_target"] - winner["memory_limit"]
    if requested_cpu >= free_cpu:
        actual_extracted_cpu = free_cpu
        cpu_reduce = (requested_cpu - free_cpu) / (number_losers) if (number_losers > 0) else 0
        free_cpu = 0
    else:
        actual_extracted_cpu = requested_cpu
        cpu_reduce = 0
        free_cpu -= requested_cpu
    if requested_memory >= free_mem:
        free_mem = 0
    else:
        free_mem -= requested_memory
    if requested_bw >= free_bw:
        actual_extracted_bw = free_bw
        bw_reduce = (requested_bw - free_bw) / (number_losers) if (number_losers > 0) else 0
        free_bw = 0
    else:
        actual_extracted_bw = requested_bw
        bw_reduce = 0
        free_bw -= requested_bw
    perdedores = []
    structured_bids = sorted(structured_bids, key=lambda x: (x["bw_limit"], x["cpu_limit"]))
    for i, bid in enumerate(structured_bids):
        new_cpu = bid["cpu_limit"] - cpu_reduce
        new_bandwidth = bid["bw_limit"] - bw_reduce
        if new_cpu < MIN["cpu"]:
            diff_cpu = bid["cpu_limit"] - MIN["cpu"]
            actual_extracted_cpu += diff_cpu
            new_cpu = MIN["cpu"]
            cpu_reduce += (cpu_reduce - diff_cpu) / (number_losers - i - 1) if (number_losers - i - 1) > 0 else cpu_reduce
        else:
            actual_extracted_cpu += cpu_reduce
        if new_bandwidth < MIN["bw"]:
            diff_bw = bid["bw_limit"] - MIN["bw"]
            actual_extracted_bw += diff_bw
            new_bandwidth = MIN["bw"]
            bw_reduce += (bw_reduce - diff_bw) / (number_losers - i - 1) if (number_losers - i - 1) > 0 else bw_reduce
        else:
            actual_extracted_bw += bw_reduce
        perdedores.append((bid["sender"], new_cpu, new_bandwidth))
    vencedor = (winner["sender"], winner["cpu_limit"] + actual_extracted_cpu, winner["bw_limit"] + actual_extracted_bw)
    return vencedor, value, perdedores, {"cpu": free_cpu, "bw": free_bw, "mem": free_mem}


# --- Geradores de casos sorteados ---

def sortear_lances(rng, nomes):
    lances = []
    for f in nomes:
        cpu, bw, mem = rng.uniform(0.01, 0.6), rng.uniform(1, 25), rng.uniform(64, 512)
        mais = rng.random() < 0.7                            # nem todo mundo pede mais
        lances.append({"fatia": f, "lance": rng.choice([1.0, 3.0, 5.0, 8.0, rng.uniform(0, 30)]),
                       "cpu": cpu, "cpu_alvo": cpu + (rng.uniform(0, 0.4) if mais else 0),
                       "bw": bw, "bw_alvo": bw + (rng.uniform(0, 20) if mais else 0),
                       "mem": mem, "mem_alvo": mem + (rng.uniform(0, 200) if mais else 0)})
    return lances


def para_original(lances):
    return [{"sender": l["fatia"], "bid": l["lance"], "cpu_target": l["cpu_alvo"], "cpu_limit": l["cpu"],
             "bw_target": l["bw_alvo"], "bw_limit": l["bw"], "memory_target": l["mem_alvo"],
             "memory_limit": l["mem"]} for l in lances]


class Equivalencia(unittest.TestCase):
    """Com os pisos iguais aos mínimos globais, nada muda em relação ao leiloeiro original."""

    def test_rodada(self):
        rng = random.Random(1)
        for _ in range(20000):
            nomes = [f"f{i}" for i in range(rng.randint(1, 5))]
            lances = sortear_lances(rng, nomes)
            livre = {"cpu": rng.choice([0.0, rng.uniform(0, 0.3)]), "bw": rng.choice([0.0, rng.uniform(0, 10)]),
                     "mem": rng.uniform(0, 500)}
            piso = {f: dict(MIN) for f in nomes}
            novo = leilao.rodada(lances, livre, piso)
            venc, preco, perd, livre_o = original_rodada(para_original(lances), livre)
            self.assertEqual(novo["vencedor"]["fatia"], venc[0])
            self.assertEqual(novo["preco"], preco)
            self.assertEqual((novo["novos"][venc[0]]["cpu"], novo["novos"][venc[0]]["bw"]), venc[1:])
            self.assertEqual([(p["fatia"], novo["novos"][p["fatia"]]["cpu"], novo["novos"][p["fatia"]]["bw"])
                              for p in novo["perdedores"]], perd)
            self.assertEqual(novo["livre"], livre_o)

    def test_inicio(self):
        rng = random.Random(2)
        for _ in range(20000):
            nomes = [f"f{i}" for i in range(rng.randint(1, 5))]
            limites = {f: {"cpu": rng.uniform(0.01, 0.5), "bw": rng.uniform(1, 20), "mem": rng.uniform(64, 900)}
                       for f in nomes}
            pool = {"cpu": 0.8, "bw": 35.0, "mem": 2048.0}
            piso = {f: dict(MIN) for f in nomes}
            novos, livre, _ = leilao.ajustar_inicio(limites, nomes, pool, piso)
            replies = [{"cpu_limit": limites[f]["cpu"], "bw_limit": limites[f]["bw"], "memory_limit": limites[f]["mem"]}
                       for f in nomes]
            replies_o, livre_o = original_inicio(replies, pool)
            for f, r in zip(nomes, replies_o):
                self.assertEqual((novos[f]["cpu"], novos[f]["bw"], novos[f]["mem"]),
                                 (r["cpu_limit"], r["bw_limit"], r["memory_limit"]))
            self.assertEqual(livre, livre_o)


class Pisos(unittest.TestCase):
    """Com pisos por fatia, nenhuma perdedora desce abaixo do seu, e o recurso se conserva."""

    def test_rodada_respeita_piso_e_conserva(self):
        rng = random.Random(3)
        for _ in range(20000):
            nomes = [f"f{i}" for i in range(rng.randint(2, 5))]
            lances = sortear_lances(rng, nomes)
            # pisos sorteados, sempre abaixo do limite atual (como fica depois da rodada extra)
            piso = {l["fatia"]: {"cpu": rng.uniform(0.01, l["cpu"]), "bw": rng.uniform(1, l["bw"]), "mem": 64.0}
                    for l in lances}
            livre = {"cpu": rng.uniform(0, 0.2), "bw": rng.uniform(0, 8), "mem": 100.0}
            res = leilao.rodada(lances, livre, piso)
            for p in res["perdedores"]:
                for r in ("cpu", "bw"):
                    self.assertGreaterEqual(res["novos"][p["fatia"]][r], piso[p["fatia"]][r] - 1e-12)
            for r in ("cpu", "bw"):
                antes = sum(l[r] for l in lances) + livre[r]
                depois = sum(v[r] for v in res["novos"].values()) + res["livre"][r]
                self.assertAlmostEqual(antes, depois, places=9)

    def test_inicio_respeita_piso(self):
        limites = {"gold": {"cpu": 0.2, "bw": 10, "mem": 512}, "vr": {"cpu": 0.2, "bw": 10, "mem": 512},
                   "silver": {"cpu": 0.2, "bw": 10, "mem": 512}, "bronze": {"cpu": 0.2, "bw": 10, "mem": 512}}
        piso = {f: dict(MIN) for f in limites}
        piso["vr"] = {"cpu": 0.28, "bw": 9.9, "mem": 64.0}
        # a VR responde primeiro: só pode ceder 0,1 Mbps; o resto (4,9) sai da próxima
        novos, livre, _ = leilao.ajustar_inicio(limites, ["vr", "gold", "silver", "bronze"],
                                                {"cpu": 0.8, "bw": 35, "mem": 2048}, piso)
        self.assertAlmostEqual(novos["vr"]["bw"], 9.9)
        self.assertAlmostEqual(novos["gold"]["bw"], 5.1)
        self.assertAlmostEqual(sum(v["bw"] for v in novos.values()), 35)
        self.assertGreaterEqual(livre["bw"], -1e-12)


class PisoCondicional(unittest.TestCase):

    def test_piso_efetivo(self):
        pisos = {"gold": dict(MIN), "vr": {"cpu": 0.28, "bw": 9.9, "mem": 64.0}}
        fora = leilao.piso_efetivo(pisos, MIN, {"vr"}, set())
        dentro = leilao.piso_efetivo(pisos, MIN, {"vr"}, {"vr"})
        self.assertEqual(fora["vr"], MIN)
        self.assertEqual(dentro["vr"]["bw"], 9.9)
        self.assertEqual(fora["gold"], MIN)

    def test_conferir_pisos(self):
        pisos = {"gold": dict(MIN), "silver": dict(MIN), "bronze": dict(MIN), "vr": {"cpu": 0.28, "bw": 9.9, "mem": 64}}
        conta = leilao.conferir_pisos(pisos, {"cpu": 0.8, "bw": 35})
        self.assertEqual(conta["cpu"], (0.31, 0.8, True))
        self.assertEqual(conta["bw"], (12.9, 35, True))
        pisos["vr"]["bw"] = 40
        self.assertFalse(leilao.conferir_pisos(pisos, {"cpu": 0.8, "bw": 35})["bw"][2])

    def test_aplicar_piso_do_pool_primeiro(self):
        limites = {"vr": {"cpu": 0.01, "bw": 1.0}, "gold": {"cpu": 0.4, "bw": 20.0}}
        piso = {"vr": {"cpu": 0.28, "bw": 9.9}, "gold": {"cpu": 0.01, "bw": 1.0}}
        novos, livre, falta = leilao.aplicar_piso("vr", limites, {"cpu": 0.5, "bw": 20.0}, piso)
        self.assertEqual(novos["vr"], {"cpu": 0.28, "bw": 9.9})
        self.assertNotIn("gold", novos)                      # coube na sobra: o vídeo não perde nada
        self.assertAlmostEqual(livre["cpu"], 0.23)
        self.assertAlmostEqual(livre["bw"], 11.1)
        self.assertEqual(falta, {"cpu": 0.0, "bw": 0.0})

    def test_aplicar_piso_divide_entre_as_outras(self):
        limites = {"vr": {"cpu": 0.2, "bw": 1.0}, "gold": {"cpu": 0.3, "bw": 15.0},
                   "silver": {"cpu": 0.2, "bw": 10.0}, "bronze": {"cpu": 0.02, "bw": 1.5}}
        piso = {"vr": {"cpu": 0.28, "bw": 9.9}, "gold": dict(MIN), "silver": dict(MIN), "bronze": dict(MIN)}
        novos, livre, falta = leilao.aplicar_piso("vr", limites, {"cpu": 0.02, "bw": 0.0}, piso)
        self.assertEqual(novos["vr"], {"cpu": 0.28, "bw": 9.9})
        # banda: faltam 8,9; o bronze só tem 0,5 acima do piso, e o resto (8,4) se divide entre gold e silver
        self.assertEqual(novos["bronze"]["bw"], 1.0)
        self.assertAlmostEqual(novos["gold"]["bw"], 15.0 - 4.2)
        self.assertAlmostEqual(novos["silver"]["bw"], 10.0 - 4.2)
        # CPU: 0,02 da sobra; os 0,06 restantes: o bronze dá 0,01 (chega ao piso) e gold e silver 0,025 cada
        self.assertEqual(novos["bronze"]["cpu"], 0.01)
        self.assertAlmostEqual(novos["gold"]["cpu"], 0.275)
        self.assertAlmostEqual(novos["silver"]["cpu"], 0.175)
        self.assertAlmostEqual(livre["cpu"], 0.0)
        for r in ("cpu", "bw"):
            antes = sum(v[r] for v in limites.values()) + {"cpu": 0.02, "bw": 0.0}[r]
            depois = sum((novos.get(f) or limites[f])[r] for f in limites) + livre[r]
            self.assertAlmostEqual(antes, depois)
        self.assertEqual(falta, {"cpu": 0.0, "bw": 0.0})

    def test_aplicar_piso_sem_ter_de_onde_tirar(self):
        limites = {"vr": {"cpu": 0.01, "bw": 1.0}, "gold": {"cpu": 0.01, "bw": 3.0}}
        piso = {"vr": {"cpu": 0.28, "bw": 9.9}, "gold": dict(MIN)}
        novos, livre, falta = leilao.aplicar_piso("vr", limites, {"cpu": 0.0, "bw": 0.0}, piso)
        self.assertAlmostEqual(novos["vr"]["bw"], 3.0)       # só os 2 Mbps do gold acima do piso dele
        self.assertAlmostEqual(falta["bw"], 6.9)
        self.assertAlmostEqual(falta["cpu"], 0.27)

    def test_liberar(self):
        novo, livre = leilao.liberar("vr", {"vr": {"cpu": 0.28, "bw": 9.9}}, {"cpu": 0.0, "bw": 1.0, "mem": 0}, MIN)
        self.assertEqual(novo, {"cpu": 0.01, "bw": 1.0})
        self.assertAlmostEqual(livre["cpu"], 0.27)
        self.assertAlmostEqual(livre["bw"], 9.9)


if __name__ == "__main__":
    unittest.main()
