"""
Testes do agente_vr.py, sem cluster (Fase 6): uma situação da tabela de decisão por teste.

Uso (de dentro de spade_agents/):
  ../venv/bin/python -m unittest test_agente_vr -v
"""

import unittest

import agente_vr
import leilao

SLA = {"net_ref_ms": 1.87, "folga_net_ms": 3, "perda_max_pct": 1, "cont_min_pct": 98, "vazao_min_mbps": 7.65}
PISO = {"cpu": 0.28, "bw": 9.9}
ATUAL = {"cpu": 0.30, "bw": 12.0}
BASE, SALDO = 100.0, 200.0
BOA = {"paused": 0, "health": 1, "net_ms": 2.0, "cont_pct": 99.7, "perda_pct": 0.1, "vazao_mbps": 8.05, "throttling": 0.0}


def janela(**mudancas):
    return {**BOA, **mudancas}


class Formula(unittest.TestCase):

    def test_lance_vr_do_plano(self):
        self.assertAlmostEqual(agente_vr.lance_vr(0, BASE, SALDO), 10.0)
        self.assertAlmostEqual(agente_vr.lance_vr(1, BASE, SALDO), 103.33, places=2)
        self.assertAlmostEqual(agente_vr.lance_vr(1.5, BASE, SALDO), 150.0)
        self.assertEqual(agente_vr.lance_vr(1.5, BASE, 40.0), 40.0)           # nunca passa do saldo

    def test_distancia_pega_o_pior_e_limita(self):
        d, partes = agente_vr.distancia(4.87, 99.0, 0.5, SLA)
        self.assertAlmostEqual(partes["net"], 1.0)
        self.assertAlmostEqual(partes["cont"], 0.5)
        self.assertAlmostEqual(partes["perda"], 0.5)
        self.assertAlmostEqual(d, 1.0)
        self.assertEqual(agente_vr.distancia(2.0, 0.0, 100, SLA)[0], 1.5)      # sem quadros: no máximo 1,5


class Decisao(unittest.TestCase):

    def decidir(self, m, atual=ATUAL, anterior=None, saldo=SALDO):
        return agente_vr.decidir(m, atual, PISO, SLA, BASE, saldo, anterior)

    def assertNaoPede(self, r, atual=ATUAL):
        self.assertEqual((r["cpu_alvo"], r["bw_alvo"]), (atual["cpu"], atual["bw"]))

    def test_fora_ou_sem_metricas(self):
        for m in (None, janela(paused=1), {"paused": None}):
            r = self.decidir(m)
            self.assertEqual(r["situacao"], "fora")
            self.assertEqual(r["lance"], 0.0)
            self.assertNaoPede(r)

    def test_sem_pedido_nao_vence_a_rodada(self):
        # A vitória vazia da hvrl2_02: os vídeos recém-reiniciados dão lances de 8, 5 e 3, e a VR fora não pode vencer
        r = self.decidir(janela(paused=1), atual={"cpu": 0.01, "bw": 1.0})
        lances = [{"fatia": f, "lance": l, "cpu_alvo": 0.3, "cpu": 0.2, "bw_alvo": 12.0, "bw": 10.0, "mem_alvo": 0, "mem": 0}
                  for f, l in (("gold", 8.0), ("silver", 5.0), ("bronze", 3.0))]
        lances.append({"fatia": "vr", "lance": r["lance"], "cpu_alvo": r["cpu_alvo"], "cpu": 0.01,
                       "bw_alvo": r["bw_alvo"], "bw": 1.0, "mem_alvo": 0, "mem": 0})
        piso = {f: {"cpu": 0.01, "bw": 1.0} for f in ("gold", "silver", "bronze", "vr")}
        resultado = leilao.rodada(lances, {"cpu": 0.0, "mem": 0.0, "bw": 0.0}, piso)
        self.assertEqual(resultado["vencedor"]["fatia"], "gold")

    def test_no_sla_sem_lance_e_nada(self):
        r = self.decidir(janela(net_ms=3.2))                                   # gastou 44% da folga: ainda não pede
        self.assertEqual(r["situacao"], "no_sla")
        self.assertEqual(r["lance"], 0.0)
        self.assertNaoPede(r)

    def test_atraso_com_throttling_pede_cpu(self):
        r = self.decidir(janela(net_ms=5.0, throttling=0.2))
        self.assertEqual(r["situacao"], "cpu")
        self.assertAlmostEqual(r["cpu_alvo"], 0.35)                            # +0,05
        self.assertEqual(r["bw_alvo"], ATUAL["bw"])
        self.assertAlmostEqual(r["lance"], agente_vr.lance_vr(r["d"], BASE, SALDO))
        self.assertGreater(r["lance"], 100)                                    # d > 1: lance acima da base

    def test_cpu_para_no_teto(self):
        r = self.decidir(janela(net_ms=6.0, throttling=0.3), atual={"cpu": 0.44, "bw": 12.0})
        self.assertAlmostEqual(r["cpu_alvo"], 0.45)
        r = self.decidir(janela(net_ms=6.0, throttling=0.3), atual={"cpu": 0.45, "bw": 12.0})
        self.assertEqual(r["situacao"], "teto_cpu")                          # no teto: não há mais o que pedir
        self.assertEqual(r["lance"], 0.0)

    def test_atraso_sem_throttling_nao_pede(self):
        r = self.decidir(janela(net_ms=6.0, throttling=0.0))
        self.assertEqual(r["situacao"], "fora_das_cotas")
        self.assertEqual(r["lance"], 0.0)
        self.assertNaoPede(r)

    def test_vazao_baixa_sem_throttling_pede_banda(self):
        r = self.decidir(janela(net_ms=700.0, vazao_mbps=7.0, cont_pct=98.0))
        self.assertEqual(r["situacao"], "banda")
        self.assertAlmostEqual(r["bw_alvo"], 13.0)                            # +1 Mbps
        self.assertAlmostEqual(r["lance"], 150.0)                              # d no máximo (1,5)

    def test_sem_quadros_conta_como_vazao_zero(self):
        # o exporter zera a continuidade mas deixa a vazão da janela anterior: sem quadros é fluxo parado
        r = self.decidir(janela(cont_pct=0.0, perda_pct=100.0, vazao_mbps=8.05))
        self.assertEqual(r["situacao"], "banda")
        self.assertAlmostEqual(r["lance"], 150.0)

    def test_abaixo_do_piso_sofrendo_volta_direto_ao_piso(self):
        atual = {"cpu": 0.15, "bw": 9.9}
        r = self.decidir(janela(net_ms=29.0, throttling=0.25), atual=atual)  # alguém apertou a upf4 à mão
        self.assertEqual(r["situacao"], "cpu")
        self.assertEqual(r["cpu_alvo"], 0.28)                                  # direto ao piso, não só +0,05

    def test_abaixo_do_piso_bem_nao_pede(self):
        # Logo depois de uma saída, a VR está no mínimo e a leitura pode ainda dizer "ativa" e "bem": o piso é do
        # leiloeiro (ele sabe se vale), e o agente não pede nada (o erro da execução hvrl2_01)
        r = self.decidir(janela(), atual={"cpu": 0.01, "bw": 1.0})
        self.assertEqual(r["situacao"], "no_sla")
        self.assertEqual(r["lance"], 0.0)
        self.assertNaoPede(r, {"cpu": 0.01, "bw": 1.0})

    def test_upf4_no_minimo_quer_dizer_fora(self):
        # Logo depois de um VR_LEAVE, a leitura (de até 5 s atrás) pode ainda dizer "ativa" e até "sofrendo"; a upf4 nos
        # mínimos globais só acontece quando o leiloeiro liberou o piso
        no_minimo = {"cpu": 0.01, "bw": 1.0}
        r = agente_vr.decidir(janela(net_ms=6.0, throttling=0.3), no_minimo, PISO, SLA, BASE, SALDO,
                              minimo={"cpu": 0.01, "bw": 1.0})
        self.assertEqual(r["situacao"], "fora (piso liberado)")
        self.assertEqual(r["lance"], 0.0)
        self.assertNaoPede(r, no_minimo)
        r = agente_vr.decidir(janela(net_ms=6.0, throttling=0.3), {"cpu": 0.15, "bw": 9.9}, PISO, SLA, BASE, SALDO,
                              minimo={"cpu": 0.01, "bw": 1.0})                # apertada à mão: não é o mínimo
        self.assertEqual(r["situacao"], "cpu")

    def test_sem_saude_repete(self):
        anterior = self.decidir(janela(net_ms=5.0, throttling=0.2))
        r = self.decidir(janela(health=0), anterior=anterior)
        self.assertEqual(r["lance"], anterior["lance"])
        self.assertEqual(r["cpu_alvo"], anterior["cpu_alvo"])
        self.assertTrue(r["situacao"].startswith("sem_saude"))
        r = self.decidir(janela(health=0))                                    # sem decisão anterior: não dá lance
        self.assertEqual(r["lance"], 0.0)

    def test_alvo_nunca_abaixo_do_atual(self):
        anterior = self.decidir(janela(net_ms=5.0, throttling=0.2))          # pediu 0,35
        r = self.decidir(janela(health=0), atual={"cpu": 0.40, "bw": 12.0}, anterior=anterior)
        self.assertEqual(r["cpu_alvo"], 0.40)                                  # um alvo menor faria o leiloeiro tirar CPU

    def test_saldo_limita_o_lance(self):
        r = self.decidir(janela(net_ms=6.0, throttling=0.2), saldo=30.0)
        self.assertEqual(r["lance"], 30.0)


if __name__ == "__main__":
    unittest.main()
