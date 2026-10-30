# Leiloeiro com o piso da VR (Fase 5)

**Pergunta:** como proteger a VR no leilão sem mudar nada para o vídeo? O plano pede um piso por fatia, um piso condicional (só vale com a VR ativa) e uma rodada extra na entrada da VR.

**Resposta:** está pronto e validado no cluster.
- **O vídeo não muda:** a conta do leilão é idêntica à de antes (provado em 20 mil rodadas sorteadas), e uma execução só de vídeo repete o padrão da `v1.0-video`.
- **No cenário H-VR em L1**, a VR ficou **100% no SLA** e entrou no SLA já na 1ª janela, porque o piso agora vem antes do tráfego (uma reserva).
- **Mas o custo para o vídeo é alto:** a VR, com prioridade 10 e o agente por utilização, ganhou metade das rodadas e acumulou quase o triplo da banda de que precisa. É a fraqueza que o agente por SLA (Fase 6) tem de corrigir.

## O que foi construído

| Arquivo | O que faz |
| --- | --- |
| `spade_agents/leilao.py` (novo) | a conta do leilão em funções puras: o corte na subida, a rodada Vickrey, o piso efetivo, a conferência da soma dos pisos, a rodada extra (`aplicar_piso`) e a liberação (`liberar`) |
| `spade_agents/test_leilao.py` (novo) | 10 testes sem cluster: equivalência com o leiloeiro antigo, pisos, rodada extra e liberação |
| `spade_agents/resource_agent.py` | o leiloeiro usa o `leilao.py`; pisos por fatia; behaviour da entrada e saída da VR; uma trava única; período do YAML; Kubernetes em paralelo; coluna `CPU_Applied` |
| `spade_agents/slice_agents.py` | Prometheus em paralelo; banda lida em "k"; chave `vr.agent` (por enquanto, só `utilizacao`) |
| `metricas_scripts/deploy_test.py` | eventos `vr_join` e `vr_leave` (a reserva na entrada); retoma a VR no fim do teste |
| `tests_configs/test_config_hvr.yaml` (novo) | o cenário H-VR em L1: o de vídeo de sempre + a fatia `vr` + os eventos da VR; saída em `resultados/hvr_l1/` |
| `run_experiment_tmux.sh` | não reinicia o UE de VR; copia o log da VR (janela `vrlog`); roda o coletor da VR no fim |
| `metricas_scripts/merge_auction_and_qoe.py` | o gráfico da VR para no fim do teste |

## Como o leiloeiro trata a VR

- **Pisos por fatia.** Cada fatia pode ter `floor_cpu` e `floor_bw` no YAML; sem eles, vale o mínimo global (0,01 núcleo e 1 Mbps), o comportamento de antes. A fatia `tipo: vr` pega o piso do `vr/calibracao.yaml` (0,28 e 9,9), e o YAML pode sobrescrever. Na subida, o leiloeiro confere que todos os pisos juntos cabem no pool (hoje, 0,31 de 0,8 e 12,9 de 35) e para se não couberem.
- **Nas rodadas,** nenhuma perdedora desce abaixo do seu piso. O que ela não pode dar se divide entre as outras, a mesma regra que já valia para o mínimo global.
- **Piso condicional.** O piso da VR só vale enquanto ela está ativa. Ela começa fora.
- **Entrada como reserva** (`vr_join`):
  1. o `deploy_test.py` pede o piso ao leiloeiro;
  2. o leiloeiro leva a `upf4` ao piso, primeiro com a sobra do pool e depois tirando das fatias de vídeo em partes iguais, até o piso de cada uma;
  3. o leiloeiro espera o kubelet confirmar a CPU e responde (`vr_ack`);
  4. só então o `deploy_test.py` retoma a VR.

  O tempo da reserva fica na linha do `VR_JOIN` do `test_events.csv`.
- **Saída** (`vr_leave`): o `deploy_test.py` pausa a VR e depois avisa. O leiloeiro devolve a VR ao mínimo, e o que ela tinha acima disso vai para a sobra, que o leilão distribui a partir da próxima rodada.
- **Uma trava única:** a rodada normal e a rodada extra nunca se misturam. Uma entrada no meio de uma rodada espera alguns segundos.
- **L0** (`vr.floor: off`): os eventos só pausam e retomam a VR. O leiloeiro responde na hora e não muda nada.
- **No `auction_history.csv`:**
  - a coluna nova `CPU_Applied` é a CPU que o kubelet aplicou de fato;
  - a rodada extra aparece em linhas `FLOOR_JOIN` (a VR), `FLOOR_DONOR` (quem cedeu) e `FLOOR_LEAVE`.

## Como rodar

```bash
cd spade_agents && ../venv/bin/python -m unittest test_leilao -v; cd ..   # os 10 testes do leilão
./run_experiment_tmux.sh tests_configs/test_config_hvr.yaml hvrNN   # cenário H-VR em L1 (~16 min; a VR é copiada e analisada sozinha)
```

Depois, o mesmo pós-execução da Fase 4 (`fetch_prometheus.py`, `resumo_execucao.py`, `merge_auction_and_qoe.py`; ver `docs/coleta-vr.md`). Para L0, copie o `test_config_hvr.yaml`, troque `vr.floor` para `off` e a pasta de saída para `.../hvr_l0/`. Use nomes de execução novos (`hvrNN`), porque um nome repetido sobrescreve o `tests_configs/_run_<nome>.yaml`.

## Testes sem cluster

| Teste | Resultado |
| --- | --- |
| Equivalência com o leiloeiro antigo, com os pisos iguais aos mínimos (20 mil rodadas e 20 mil subidas sorteadas) | idêntico bit a bit |
| Pisos sorteados (20 mil rodadas) | nenhuma perdedora abaixo do piso, e nenhum recurso some |
| Rodada extra e liberação (casos montados à mão) | do pool primeiro, divisão igual, quem chega ao piso sai da divisão, falta quando não há de onde tirar |
| Integração local: SPADE real com XMPP embutido, 4 agentes de fatia, Kubernetes e Prometheus falsos | L1, L0 e só vídeo como desenhado |
| Reserva de ponta a ponta com o `deploy_test.py` real | em L1, a VR só volta com a `upf4` já em 280m e 9,9 Mbps; em L0, resposta imediata |

## Validação no cluster (30/10/2026)

### Só vídeo (`fase5_video01`) contra a `v1.0-video` (`exec01`)

| | `v1.0-video` | Fase 5 |
| --- | --- | --- |
| Rodadas nos 950 s do teste, a cada | 32, 30 s | 32, 30 s |
| Vitórias: gold / silver / bronze | 19 / 10 / 3 | 22 / 7 / 3 |
| Bitrate médio: gold / silver / bronze (kbps) | 11 873 / 5 823 / 2 096 | 11 187 / 4 392 / 3 300 |
| `CPU_Applied` igual à `CPU_Allocated` | — | 96 de 96 |

Mesmo ritmo, mesmo padrão: o gold domina quando toca, e o pool é respeitado. As diferenças entre silver e bronze estão na faixa de variação de uma execução isolada (os lances dependem do uso medido, que varia com o ABR).

### H-VR em L1 (`resultados/hvr_l1/hvr01`)

| | Resultado |
| --- | --- |
| VR no SLA | **45 de 45 janelas (100%)**, `net` mediano de 2,2 ms |
| Reserva | 2,8 s (entrada em 60 s) e 2,2 s (em 600 s), com o kubelet confirmando os 280m |
| Da retomada até a 1ª janela no SLA | 12,8 s e 12,7 s: a 1ª janela, o melhor possível |

**O custo para o vídeo.** Contando só as 30 rodadas de dentro do teste:

| | Só vídeo | H-VR em L1 |
| --- | --- | --- |
| Vitórias do gold | 20 | 11 |
| Vitórias da VR | — | 15 |
| Banda média da VR | — | 15,5 Mbps (chegou a 27; ela precisa de ~8,8) |
| CPU média da VR | — | 0,27 (chegou a 0,47; ela precisa de ~0,17) |
| Bitrate médio do gold | 11 187 kbps | 4 929 kbps (3 travadas) |

- **A VR não sabe quando já basta.** O agente por utilização pede mais enquanto a fatia usa mais de 20% do que tem. Com 9,9 Mbps a VR usa ~86%, pede, ganha (prioridade 10) e continua pedindo até perto de 40 Mbps, mesmo estando no SLA.
- **A VR pausada pega banda de volta.** Logo depois de cada saída, ela ganhou uma rodada e voltou a 9,75 Mbps (em 11 s e em 401 s), e ficou com essa banda parada até a entrada seguinte. O Prometheus mostra a `upf4` sem tráfego (0 kbps, 0,0005 núcleo). Mas o agente mede a utilização pela taxa do último minuto, que ainda contém o tráfego de antes da pausa, e, dividida pelo limite novo (o mínimo), parece altíssima.

São as duas fraquezas do agente por utilização que o plano previa para L0 e L1. Elas não vêm do piso, da reserva nem da liberação, que funcionaram.

## Decisões tomadas

- **A saída devolve os recursos da VR** (ela volta ao mínimo, e o resto vai para a sobra). Foi a leitura do plano: "a banda volta para o leilão na rodada seguinte".
- **A entrada é uma reserva** (decisão do Pedro): o piso vem antes do tráfego. Assim o "tempo até o piso" passa a ser o tempo da reserva (2 a 5 s), e a VR não sofre na entrada.
- **A VR começa fora**, com um `vr_leave` em 0 s, como o cenário H-VR pede.
- **O bug do `inform` ficou como está** (a prioridade vinda do LLM não muda o `priority` do agente, `slice_agents.py:184`). Ele só afeta os cenários com LLM, e corrigi-lo muda a comparação com a `v1.0-video`. A decisão fica para a Fase 7.

## Limites

- **Uma execução em cada condição.** Os números mostram o mecanismo, não uma estatística.
- **O leiloeiro segue rodando depois do `TEST_END`**, até a sessão do tmux ser fechada. As análises têm de olhar só as rodadas do teste. O gráfico da VR do `merge` já faz isso; o resumo ainda não.
- **As linhas `FLOOR_*` entram na média por período** da figura do artigo (`csv_graficos_ieee.py`), junto com as rodadas daquele período.
- **A cópia do `run_experiment_tmux.sh` em `../../Docs` ficou velha.** A do repositório é a que vale.

## O que isso muda no plano

- **Fase 5:** o critério de pronto foi cumprido (os testes do piso condicional e da rodada extra passam, e a execução com a VR desligada repete a `v1.0-video`).
- **Fase 6:** o agente da VR tem de resolver as duas fraquezas medidas aqui:
  - com a VR no SLA, dar lance baixo e não pedir mais, para o vídeo ficar com a sobra;
  - com a VR fora (sem janelas novas), dar o lance mínimo e não pedir nada, e não se guiar pela taxa de 1 minuto logo depois de uma saída.

  Junto com a ideia da Fase 4 (usar o throttling da `upf4` para saber se CPU resolve).
- **Fase 8:**
  - a L1 já dá a comparação "custo para o vídeo": o bitrate do gold caiu de ~11 para ~5 Mbps;
  - falta o YAML da L0;
  - o resumo deve contar só as rodadas do teste.
