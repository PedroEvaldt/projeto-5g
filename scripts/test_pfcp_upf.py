"""Testes do leitor do log de PFCP das UPFs e da regra de PFCP do verificador.
Da raiz: venv/bin/python -m unittest scripts/test_pfcp_upf.py -v"""
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "scripts"))
sys.path.insert(0, str(RAIZ / "metricas_scripts"))
import pfcp_upf  # noqa: E402
from conferir_execucao import conferir_pfcp  # noqa: E402

NOSSO = "10.251.46.210"
DE_FORA = "10.251.46.199"

# Trecho real da upf4 em 02/11/2026 (a trava), com as cores do Open5GS e o carimbo do kubectl --timestamps
LOG = """\
2026-11-02T20:36:08.678123456Z \x1b[32m11/02 20:36:08.678\x1b[0m: [\x1b[33mupf\x1b[0m] \x1b[1;32mINFO\x1b[0m: PFCP associated [10.251.46.210]:8805 (../src/upf/pfcp-sm.c:168)
2026-11-02T20:41:57.3711Z \x1b[32m11/02 20:41:57.371\x1b[0m: [\x1b[33mupf\x1b[0m] \x1b[1;36mWARNING\x1b[0m: PFCP[REQ] has already been associated [10.251.46.210]:8805 (../src/upf/pfcp-sm.c:256)
2026-11-02T20:41:57.371Z \x1b[32m11/02 20:41:57.371\x1b[0m: [\x1b[33mupf\x1b[0m] \x1b[1;36mWARNING\x1b[0m: No Heartbeat from SMF [10.251.46.210]:8805 (../src/upf/pfcp-sm.c:309)
2026-11-02T20:41:57Z \x1b[32m11/02 20:41:57.371\x1b[0m: [\x1b[33mupf\x1b[0m] \x1b[1;32mINFO\x1b[0m: PFCP de-associated [10.251.46.210]:8805 (../src/upf/pfcp-sm.c:184)
2026-11-02T20:41:57.671Z \x1b[32m11/02 20:41:57.671\x1b[0m: [\x1b[33mupf\x1b[0m] \x1b[1;36mWARNING\x1b[0m: cannot handle PFCP message type[50] (../src/upf/pfcp-sm.c:139)
"""

# Trecho real da upf4 em 03/11/2026: o SMF de fora cai e volta, e o nosso continua fora
LOG_DOIS = """\
2026-11-03T12:55:56.592897682Z 11/03 12:55:56.592: [upf] INFO: PFCP de-associated [10.251.46.199]:8805 (../src/upf/pfcp-sm.c:184)
2026-11-03T12:55:59.092657871Z 11/03 12:55:59.092: [upf] INFO: PFCP de-associated [10.251.46.210]:8805 (../src/upf/pfcp-sm.c:184)
2026-11-03T12:56:31.687044363Z 11/03 12:56:31.686: [upf] INFO: PFCP associated [10.251.46.199]:8805 (../src/upf/pfcp-sm.c:168)
"""


# Trecho real do smf em 03/11/2026 (a VR na L0): uma restauração, uma tentativa recusada e a sessão que valeu
LOG_SMF = """\
2026-11-03T11:52:35.684669848Z 11/03 11:52:35.684: [smf] INFO: UE SUPI[imsi-999700000000007] DNN[slice04] IPv4[172.20.21.90] IPv6[] (../src/smf/pfcp-sm.c:475)
2026-11-03T12:57:01.533279724Z 11/03 12:57:01.533: [smf] INFO: UE SUPI[imsi-999700000000007] DNN[slice04] IPv4[172.20.21.101] IPv6[] (../src/smf/npcf-handler.c:586)
2026-11-03T12:57:09.042445443Z 11/03 12:57:09.042: [smf] INFO: Removed Session: UE IMSI:[imsi-999700000000007] DNN:[slice04:1] IPv4:[172.20.21.101] IPv6:[] (../src/smf/context.c:1701)
2026-11-03T12:57:09.238028068Z 11/03 12:57:09.237: [smf] INFO: UE SUPI[imsi-999700000000007] DNN[slice04] IPv4[172.20.21.102] IPv6[] (../src/smf/npcf-handler.c:586)
2026-11-03T13:12:22.938878118Z 11/03 13:12:22.938: [smf] INFO: Removed Session: UE IMSI:[imsi-999700000000003] DNN:[slice02:1] IPv4:[172.18.20.92] IPv6:[] (../src/smf/context.c:1701)
"""


class TestEventos(unittest.TestCase):
    def test_so_as_duas_linhas_de_estado(self):
        evs = pfcp_upf.eventos(LOG)
        self.assertEqual([e for _, e, _ in evs], ["associated", "de-associated"])

    def test_ja_associado_nao_conta_como_volta(self):
        # "has already been associated" chegou no mesmo ms da trava: contá-la esconderia a UPF presa
        self.assertEqual(pfcp_upf.eventos(LOG)[-1][1], "de-associated")

    def test_carimbos_com_e_sem_fracao(self):
        evs = pfcp_upf.eventos(LOG)
        self.assertEqual(evs[0][0], datetime(2026, 11, 2, 20, 36, 8, 678123, tzinfo=timezone.utc))
        self.assertEqual(evs[1][0], datetime(2026, 11, 2, 20, 41, 57, tzinfo=timezone.utc))

    def test_log_sem_eventos(self):
        self.assertEqual(pfcp_upf.eventos("2026-11-02T20:00:00Z qualquer coisa\n"), [])

    def test_guarda_o_smf_de_cada_evento(self):
        self.assertEqual([p for _, _, p in pfcp_upf.eventos(LOG_DOIS)], [DE_FORA, NOSSO, DE_FORA])

    def test_o_smf_de_fora_nao_esconde_a_queda_do_nosso(self):
        self.assertEqual(pfcp_upf.eventos(LOG_DOIS)[-1][1], "associated")          # sem o filtro: pareceria de pé
        self.assertEqual(pfcp_upf.eventos(LOG_DOIS, NOSSO)[-1][1], "de-associated")


class TestSessoes(unittest.TestCase):
    def test_tipos_e_campos(self):
        s = pfcp_upf.sessoes(LOG_SMF)
        self.assertEqual([(tipo, dnn, ip) for _, tipo, dnn, ip, _ in s], [
            ("restaurada", "slice04", "172.20.21.90"), ("criada", "slice04", "172.20.21.101"),
            ("removida", "slice04", "172.20.21.101"), ("criada", "slice04", "172.20.21.102"),
            ("removida", "slice02", "172.18.20.92")])
        self.assertEqual(s[-1][4], "999700000000003")

    def test_so_os_dnns_pedidos(self):
        self.assertEqual({dnn for _, _, dnn, _, _ in pfcp_upf.sessoes(LOG_SMF, {"slice02"})}, {"slice02"})

    def test_dnn_da_upf(self):
        self.assertEqual(pfcp_upf.dnn_da_upf("upf4"), "slice04")


# Régua: TEST_START às 13:10:22,234 desta máquina; o cluster está 190 s à frente, então T+0 = 13:13:32,234 no cluster
EV = pd.DataFrame({
    "Timestamp_Offset_Seconds": [0.0, 900.0],
    "Event_Type": ["TEST_START", "TEST_END"],
    "Details": ["Config: x | inicio=2026-11-03T13:10:22.234+00:00", ""],
})
CAB = f"# Eventos de PFCP\n# diferenca_s=190.000 erro_s=0.1\n# smf={NOSSO}\n"


def em(t):
    """Hora do cluster de T+t s."""
    return (pd.Timestamp("2026-11-03T13:13:32.234Z") + pd.Timedelta(seconds=t)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class TestConferirPfcp(unittest.TestCase):
    def test_smf_de_fora_caido_no_fim_nao_descarta(self):
        texto = CAB + f"upf4 {em(800)} de-associated {DE_FORA}\n"
        self.assertEqual(conferir_pfcp(texto, EV, 900), ([], []))

    def test_nosso_caido_no_fim_descarta_mesmo_com_o_de_fora_voltando(self):
        texto = CAB + f"upf4 {em(500)} de-associated {NOSSO}\nupf4 {em(510)} associated {DE_FORA}\n"
        descartar, _ = conferir_pfcp(texto, EV, 900)
        self.assertEqual(len(descartar), 1)
        self.assertIn("upf4 sem PFCP de T+500 s", descartar[0])

    def test_queda_longa_do_nosso_que_volta_avisa(self):
        texto = CAB + f"upf4 {em(70)} de-associated {NOSSO}\nupf4 {em(140)} associated {NOSSO}\n"
        self.assertEqual(conferir_pfcp(texto, EV, 900), ([], ["upf4 sem PFCP por 70 s (T+70 s a T+140 s)"]))

    def test_arquivo_antigo_sem_a_coluna_conta_tudo(self):
        texto = "# diferenca_s=190.000 erro_s=0.1\n" + f"upf2 {em(203)} de-associated\n"
        descartar, _ = conferir_pfcp(texto, EV, 900)
        self.assertEqual(len(descartar), 1)

    def test_sem_o_ip_do_smf_conta_tudo_e_avisa(self):
        texto = "# diferenca_s=190.000 erro_s=0.1\n# erro ao ler o IP do smf: x\n" + \
                f"upf4 {em(800)} de-associated {DE_FORA}\n"
        descartar, aviso = conferir_pfcp(texto, EV, 900)
        self.assertEqual(len(descartar), 1)
        self.assertTrue(any("sem o IP do nosso SMF" in a for a in aviso))


def sessao(t, tipo, dnn, ip):
    return f"sessao {em(t)} {tipo} {dnn} {ip} 9997000000000xx\n"


class TestUpfPresaRegraB(unittest.TestCase):
    """UPF presa no fim: descarta só se o cliente dela estava sem sessão no TEST_END (decisão do Pedro, 03/11/2026)."""
    CAB_S = CAB + "# sessoes_smf=ok\n"
    PRESA_UPF2 = f"upf2 {em(203)} de-associated {NOSSO}\n"

    def test_sessao_do_cliente_de_pe_avisa(self):
        # a tentativa 1 da hvrl0_01: o silver abriu a sessão antes do início e tocou até o fim
        texto = self.CAB_S + self.PRESA_UPF2 + sessao(-100, "criada", "slice02", "172.18.20.92") + \
                sessao(1100, "removida", "slice02", "172.18.20.92")
        self.assertEqual(conferir_pfcp(texto, EV, 900),
                         ([], ["upf2 sem PFCP de T+203 s até o fim do teste (a sessão do cliente seguiu de pé)"]))

    def test_cliente_tentando_sem_conseguir_descarta(self):
        # o incidente de 02/11: a sessão de antes cai e cada tentativa é recusada em ~7,5 s, até depois do fim
        texto = self.CAB_S + f"upf4 {em(500)} de-associated {NOSSO}\n" + sessao(500, "removida", "slice04", "a")
        for i, t in enumerate(range(501, 910, 8)):
            texto += sessao(t, "criada", "slice04", f"ip{i}") + sessao(t + 7.5, "removida", "slice04", f"ip{i}")
        descartar, _ = conferir_pfcp(texto, EV, 900)
        self.assertEqual(descartar,
                         ["upf4 sem PFCP de T+500 s até o fim do teste, e o cliente dela sem sessão no fim (Open5GS preso)"])

    def test_sessao_de_antes_da_janela_sem_remocao_conta_de_pe(self):
        # a VR, cujo UE não é reiniciado: a sessão é de horas antes e não aparece no arquivo
        texto = self.CAB_S + f"upf4 {em(500)} de-associated {NOSSO}\n"
        self.assertEqual(conferir_pfcp(texto, EV, 900)[0], [])

    def test_restaurada_e_removida_antes_do_fim_descarta(self):
        texto = self.CAB_S + f"upf4 {em(500)} de-associated {NOSSO}\n" + \
                sessao(-50, "restaurada", "slice04", "x") + sessao(505, "removida", "slice04", "x")
        self.assertEqual(len(conferir_pfcp(texto, EV, 900)[0]), 1)

    def test_tentativa_em_curso_no_fim_nao_conta(self):
        # criada 3 s antes do fim e recusada 4,5 s depois dele: durou 7,5 s, não estava de pé
        texto = self.CAB_S + f"upf4 {em(500)} de-associated {NOSSO}\n" + sessao(500, "removida", "slice04", "a") + \
                sessao(897, "criada", "slice04", "b") + sessao(904.5, "removida", "slice04", "b")
        self.assertEqual(len(conferir_pfcp(texto, EV, 900)[0]), 1)

    def test_sessao_nova_substitui_a_anterior(self):
        # o UE abriu uma sessão nova no meio do teste e ela durou: está de pé, mesmo sem a remoção da antiga
        texto = self.CAB_S + self.PRESA_UPF2 + sessao(-100, "criada", "slice02", "v") + \
                sessao(300, "criada", "slice02", "w")
        self.assertEqual(conferir_pfcp(texto, EV, 900)[0], [])

    def test_sessao_de_outro_dnn_nao_salva(self):
        texto = self.CAB_S + self.PRESA_UPF2 + sessao(-100, "criada", "slice02", "v") + \
                sessao(250, "removida", "slice02", "v") + sessao(-100, "criada", "slice01", "g")
        self.assertEqual(len(conferir_pfcp(texto, EV, 900)[0]), 1)

    def test_erro_no_log_do_smf_descarta_como_antes(self):
        texto = CAB + f"# smf: erro ao ler o log: x\n" + self.PRESA_UPF2
        descartar, aviso = conferir_pfcp(texto, EV, 900)
        self.assertEqual(descartar, ["upf2 sem PFCP de T+203 s até o fim do teste (Open5GS preso)"])
        self.assertTrue(any("erro ao ler o log" in a for a in aviso))


if __name__ == "__main__":
    unittest.main()
