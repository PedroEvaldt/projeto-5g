#!/usr/bin/env python3
"""
Roda um script do PhotonPath (servidor ou cliente) pedindo os buffers de socket pela via forçada.

O PhotonPath pede 8 MB de buffer, mas o kernel corta o pedido para o net.core.rmem_max/wmem_max
do nó (no blacksabbath, o padrão de 208 kB). Com a permissão NET_ADMIN no container, as opções
SO_RCVBUFFORCE/SO_SNDBUFFORCE ignoram esse teto. Este lançador troca a função photon_clock.buffers,
que os dois scripts usam para pedir o buffer, e depois roda o script pedido sem editar photonpath/.
Sem NET_ADMIN, cai na função original (e o PhotonPath avisa do corte, como sempre).

Uso:
  python3 -u pp_launcher.py photon_server.py [opções do servidor]
  python3 -u pp_launcher.py photon_client.py [opções do cliente]
A pasta dos scripts vem de PP_DIR (padrão: /opt/photonpath, onde o ConfigMap é montado).
"""

import os
import runpy
import socket
import sys

PP_DIR = os.environ.get("PP_DIR", "/opt/photonpath")
SO_RCVBUFFORCE = getattr(socket, "SO_RCVBUFFORCE", 33)   # números do Linux, caso o Python não os exporte
SO_SNDBUFFORCE = getattr(socket, "SO_SNDBUFFORCE", 32)

sys.path.insert(0, PP_DIR)                                 # para o "import photon_clock" achar a cópia do PP_DIR
import photon_clock                                        # noqa: E402

buffers_original = photon_clock.buffers


def buffers_forcados(sock, wanted):
    """Mesmo contrato do photon_clock.buffers: pede `wanted` e devolve (rcvbuf, sndbuf) concedidos."""
    try:
        for opt in (SO_RCVBUFFORCE, SO_SNDBUFFORCE):
            sock.setsockopt(socket.SOL_SOCKET, opt, wanted)
    except OSError as e:
        # Sem NET_ADMIN. Não dá para chamar a original depois de forçar: o pedido comum cortaria de volta.
        print(f"[pp_launcher] via forçada recusada ({e}); usando a via comum", file=sys.stderr, flush=True)
        return buffers_original(sock, wanted)
    print("[pp_launcher] buffers pela via forçada (SO_RCVBUFFORCE/SO_SNDBUFFORCE)", file=sys.stderr, flush=True)
    return (sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF),
            sock.getsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF))


photon_clock.buffers = buffers_forcados

if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("uso: pp_launcher.py <photon_server.py|photon_client.py> [opções]")
    script = os.path.join(PP_DIR, sys.argv[1])
    sys.argv = [script] + sys.argv[2:]                     # o script vê só as opções dele, como se fosse chamado direto
    runpy.run_path(script, run_name="__main__")
