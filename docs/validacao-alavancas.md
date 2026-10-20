# Validação das alavancas do leilão (Fase 1)

**Pergunta:** o resize de CPU e a anotação de banda do Calico, do jeito que o `resource_agent.py` aplica, mudam de verdade a UPF **com o pod rodando**? O piso da VR depende disso.

**Resposta: sim, as duas.** A CPU nova vale em ~1 s, sem reinício, e o container passa a usar exatamente o pedido (±1,5m). A banda nova vale em menos de 1 s, subindo ou descendo, e a vazão medida pelo túnel fica entre 0,90 e 1,08 do pedido. O risco 1 do plano (anotação só na criação do pod) não se confirmou.

## Ambiente

- 20/10/2026, Kubernetes 1.33.2, namespace `pfsevaldt`, UPFs no nó `wb-fibre-01` (cgroup v2, driver systemd).
- UPF testada: `upf4` (nenhuma fatia de vídeo passa por ela). Os scripts devolvem o valor original no fim.
- Os scripts aplicam **as mesmas chamadas do leiloeiro**: `patch_namespaced_pod_resize` com `requests = limits` (CPU) e `patch_namespaced_pod` nas anotações `ingressBandwidth`/`egressBandwidth` em "k" (banda).
- Estado do cluster: as **10 UPFs** (upf1–10) estavam de pé no `wb-fibre-01`; elas continuam assim, com as upf5–10 paradas e reservadas para um teste futuro de interferência (ver CLAUDE.md, "Testbed"). **Não foi conferido** se havia um experimento rodando na ledzeppelin durante o teste; antes de repetir, confira com `tmux ls` lá.

```bash
bash vr/subir_teste_banda.sh                  # assinante IMSI 08 na slice04, servidor iperf01 e UE ue-iperf-08 (só para a banda)
venv/bin/python vr/validar_cpu.py             # resize da upf4 em 6 valores; mede status, cpu.max e uso sob carga
venv/bin/python vr/validar_banda.py           # muda a banda da upf4 no meio de um iperf3 e mede antes/depois
bash vr/subir_teste_banda.sh --remover        # apaga o UE e o servidor de iperf (o assinante fica)
```

Os CSVs ficam em `metricas_scripts/resultados/validacao/` (fora do git).

## CPU (resize)

Para cada valor: o que o kubelet diz ter aplicado (`status.containerStatuses[0].resources`), a cota no kernel (`cpu.max` do cgroup do container) e o efeito, medido como a CPU que dois laços ocupados conseguem usar dentro do container por 5 s.

| Pedido | Status (kubelet) | Tempo | cpu.max (container) | Medido sob carga | Reinícios |
| ------ | ---------------- | ----- | ------------------- | ---------------- | --------- |
| 400m   | 400m             | 1,0 s | 40000 100000 (400m) | 397,6m           | 0         |
| 100m   | 100m             | 0,5 s | 10000 100000 (100m) | 98,6m            | 0         |
| 50m    | 50m              | 1,1 s | 5000 100000 (50m)   | 48,9m            | 0         |
| 10m    | 10m              | 1,1 s | 1000 100000 (10m)   | 10,2m            | 0         |
| 229m   | 229m             | 1,0 s | 23000 100000 (230m) | 229,6m           | 0         |
| 15m    | 15m              | 1,1 s | 2000 100000 (20m)   | 14,7m            | 0         |

- **O valor vale em ~1 s e sem reinício.** O `status` bate com o pedido em todas as linhas, e o `restartCount` fica em 0.
- **O efeito bate com o pedido** em ±1,5m, inclusive no mínimo do leilão (10m).
- **Arredondamento de 10m.** No cgroup do container, o systemd arredonda a cota para cima, para múltiplos de 10m (1% de um núcleo): 15m vira 20m, e 229m vira 230m. O limite que vale é o menor entre o do container e o do pod (o cgroup pai). No caso de 15m, o pod ficou com 1500 (15m exatos), por isso a medida deu 14,7m. Na prática, o erro é de no máximo 10m para cima. Os pontos da calibração (Fase 3) já são múltiplos de 10m.
- **Onde ler o `cpu.max`.** A UPF é privilegiada e enxerga a raiz do cgroup do nó, então `/sys/fs/cgroup/cpu.max` não existe dentro dela. O caminho certo é o que aparece em `/proc/self/cgroup`:

```bash
kubectl exec -n pfsevaldt $UPF_POD -- sh -c 'C=/sys/fs/cgroup$(cut -d: -f3 /proc/self/cgroup); cat $C/cpu.max $(dirname $C)/cpu.max'   # cota do container e do pod
```

## Banda (Calico)

UE de teste `ue-iperf-08` na `slice04` (IP `172.20.0.2`, sub-rede da `upf4`), rota até o `iperf01` confirmada por `ip route get` (`dev uesimtun0`). Cada linha é um iperf3 de download (`-R`) de 20 s, com a anotação trocada aos ~6 s. "Antes" é a média dos intervalos de 0,5 s antes do patch; "depois" é a média a partir de 4 s depois dele.

Ao contrário da CPU, a banda não tem coluna "aplicado": o Calico não devolve nenhum `status`, e o limitador que ele instala só pode ser visto no nó (`tc qdisc show`), onde não temos acesso. A vazão medida é a evidência de que a anotação foi aplicada.

| Pedido          | Antes      | Depois (iperf) | Depois / pedido | Mudou | Tempo até o efeito |
| --------------- | ---------- | -------------- | --------------- | ----- | ------------------ |
| 5 Mbps (de 10M) | 9,02 Mbps  | 5,26 Mbps      | 1,05            | sim   | 0,9 s              |
| 15 Mbps (de 5M) | 4,51 Mbps  | 13,57 Mbps     | 0,90            | sim   | 0,2 s              |
| 2 Mbps (de 15M) | 13,47 Mbps | 2,15 Mbps      | 1,08            | sim   | 0,9 s              |
| 8 Mbps (de 2M)  | 2,76 Mbps  | 7,58 Mbps      | 0,95            | sim   | 0,2 s              |

- **A anotação vale com o pod rodando**, subindo e descendo, em menos de 1 s (a resolução é o intervalo de 0,5 s do iperf). Descer leva um pouco mais, porque a fila do limitador esvazia.
- **A unidade "k" que o leiloeiro escreve é aceita** (`5000k`, `15000k`…).
- **A vazão útil (TCP) fica entre 0,90 e 1,08 do pedido.** Com 10M, deu 9,0 Mbps. Fica abaixo nas taxas altas pelos cabeçalhos (GTP-U, IP, TCP). Fica um pouco acima nas taxas baixas, e isso não está explicado por completo: o burst de 1M sozinho daria só ~0,1–0,2 Mbps a mais. Logo depois de uma conexão nova, com 2M, chegou a 2,76 Mbps. A hipótese, não testada, é que o balde e a fila do limitador começam cheios. Para a VR, que tem taxa fixa, a Fase 3 mede onde está o degrau.

## Limites do teste

- **Uma medida por valor, sem repetição.** Basta para a pergunta de sim ou não da Fase 1, mas não serve para calibrar.
- **TCP não é VR.** O iperf3 usou TCP, que se adapta ao gargalo. O PhotonPath usa UDP com taxa fixa: abaixo do limite, a fila cresce (latência) e depois descarta (perda). O degrau da VR é medido na Fase 3, com o próprio PhotonPath.
- **Só download.** O upload da VR é pequeno (poses de 128 B a 250 Hz, ~0,26 Mbps) e não foi testado.

## O que isso muda no plano

- **Risco 1 descartado:** o piso de banda da VR pode ser aplicado pelo leilão, com o pod rodando. Não precisa fixá-lo na criação da `upf4`.
- **Rodada extra (Fase 5):** depois do patch, o piso vale em ~1 s para a CPU e para a banda. O "tempo até o piso" vai ser dominado pelo leiloeiro, não pelo cluster.
- **Fase 5.5:** registrar o valor aplicado continua valendo. O `status` só mostra o pedido depois que o kubelet aplica (~1 s), mas o kernel pode estar até 10m acima.
- **Fase 3:** manter os pontos de CPU em múltiplos de 10m.
- **Caminho da VR:** a `slice04` leva à `upf4` de ponta a ponta (assinante, sessão PDU, túnel e rota). Isso adianta a Fase 2.1.
