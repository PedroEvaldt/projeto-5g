"""Testes do leitor do log de PFCP das UPFs. Da raiz: venv/bin/python -m unittest scripts/test_pfcp_upf.py -v"""
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pfcp_upf  # noqa: E402

# Trecho real da upf4 em 02/11/2026 (a trava), com as cores do Open5GS e o carimbo do kubectl --timestamps
LOG = """\
2026-11-02T20:36:08.678123456Z \x1b[32m11/02 20:36:08.678\x1b[0m: [\x1b[33mupf\x1b[0m] \x1b[1;32mINFO\x1b[0m: PFCP associated [10.251.46.210]:8805 (../src/upf/pfcp-sm.c:168)
2026-11-02T20:41:57.3711Z \x1b[32m11/02 20:41:57.371\x1b[0m: [\x1b[33mupf\x1b[0m] \x1b[1;36mWARNING\x1b[0m: PFCP[REQ] has already been associated [10.251.46.210]:8805 (../src/upf/pfcp-sm.c:256)
2026-11-02T20:41:57.371Z \x1b[32m11/02 20:41:57.371\x1b[0m: [\x1b[33mupf\x1b[0m] \x1b[1;36mWARNING\x1b[0m: No Heartbeat from SMF [10.251.46.210]:8805 (../src/upf/pfcp-sm.c:309)
2026-11-02T20:41:57Z \x1b[32m11/02 20:41:57.371\x1b[0m: [\x1b[33mupf\x1b[0m] \x1b[1;32mINFO\x1b[0m: PFCP de-associated [10.251.46.210]:8805 (../src/upf/pfcp-sm.c:184)
2026-11-02T20:41:57.671Z \x1b[32m11/02 20:41:57.671\x1b[0m: [\x1b[33mupf\x1b[0m] \x1b[1;36mWARNING\x1b[0m: cannot handle PFCP message type[50] (../src/upf/pfcp-sm.c:139)
"""


class TestEventos(unittest.TestCase):
    def test_so_as_duas_linhas_de_estado(self):
        evs = pfcp_upf.eventos(LOG)
        self.assertEqual([e for _, e in evs], ["associated", "de-associated"])

    def test_ja_associado_nao_conta_como_volta(self):
        # "has already been associated" chegou no mesmo ms da trava: contá-la esconderia a UPF presa
        self.assertEqual(pfcp_upf.eventos(LOG)[-1][1], "de-associated")

    def test_carimbos_com_e_sem_fracao(self):
        evs = pfcp_upf.eventos(LOG)
        self.assertEqual(evs[0][0], datetime(2026, 11, 2, 20, 36, 8, 678123, tzinfo=timezone.utc))
        self.assertEqual(evs[1][0], datetime(2026, 11, 2, 20, 41, 57, tzinfo=timezone.utc))

    def test_log_sem_eventos(self):
        self.assertEqual(pfcp_upf.eventos("2026-11-02T20:00:00Z qualquer coisa\n"), [])


if __name__ == "__main__":
    unittest.main()
