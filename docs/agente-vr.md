# Agente da VR por SLA (Fase 6)

**Pergunta:** dá para proteger a VR sem tirar do vídeo mais do que o piso? Na Fase 5, o agente por utilização (L1) manteve a VR no SLA, mas ela venceu metade das rodadas, acumulou quase o triplo da banda de que precisa, e o bitrate do gold caiu de ~11 para ~5 Mbps.

**Resposta:** dá. Com o agente por SLA (L2), a VR fica no SLA (100% das janelas na execução final) **praticamente sem disputar o leilão**:
- ela não ganha nenhuma rodada que mude recursos e fica no piso (0,28 núcleo e 9,9 Mbps);
- o vídeo recupera boa parte do que a L1 tirava (o gold vai de ~4,9 para 6,7 a 8,8 Mbps).

O que o vídeo ainda perde, em relação à execução só de vídeo, é o próprio piso da VR enquanto ela está ativa.

## O que foi construído

| Arquivo | O que faz |
| --- | --- |
| `spade_agents/agente_vr.py` (novo) | a decisão do agente em funções puras: a fórmula de lance do plano (`lance_vr`) e a decisão de cada rodada (`decidir`) |
| `spade_agents/test_agente_vr.py` (novo) | 16 testes, um por situação da decisão, mais os casos de borda |
| `spade_agents/slice_agents.py` | a classe `VRSliceAgent`, criada quando `vr.agent: sla`. Na hora de cada lance, lê a última janela da VR e o throttling da `upf4` no Prometheus e os limites reais da `upf4` no Kubernetes; grava cada decisão no `vr_decisoes.csv`. No agente de vídeo, só mudou a condição do bloco do lance |
| `tests_configs/test_config_hvr_l2.yaml` (novo) | o H-VR em L2: igual ao de L1, com `vr.agent: sla`, os parâmetros do agente (`vr.sla_agent`) e a saída em `resultados/hvr_l2/` |

O leiloeiro não mudou: o agente manda o lance no mesmo formato do agente de vídeo e usa a mesma carteira (orçamento, renda e prioridade).

## Como o agente decide

Quando a VR pede algo, o lance mede quanto ela gastou da folga do SLA, pela fórmula do plano: `d` é o pior entre atraso, continuidade e perda (0 = perfeito, 1 = no limite, até 1,5), e o lance vai de 0,1 a 1,5 × base. Quando não pede nada, o lance é 0. O pedido segue o diagnóstico das Fases 3 e 4.

| Situação | Como reconhece | Lance | O que pede |
| --- | --- | --- | --- |
| `fora` | `vr_paused = 1`, ou sem métricas | 0 | nada |
| `fora (piso liberado)` | a `upf4` nos mínimos globais (0,01 e 1 Mbps): só acontece quando o leiloeiro liberou o piso | 0 | nada |
| `sem_saude` | `health = 0` | repete a decisão anterior | repete |
| `no_sla` | `d` < 0,5 | 0 | nada |
| `cpu` | `d` ≥ 0,5 e throttling da `upf4` ≥ 2% | pela fórmula | +0,05 núcleo (pelo menos o piso), até 0,45 |
| `banda` | `d` ≥ 0,5, vazão abaixo do SLA, sem throttling | pela fórmula | +1 Mbps (pelo menos o piso) |
| `fora_das_cotas` | `d` ≥ 0,5, sem throttling, com o fluxo inteiro | 0 | nada: CPU não resolve (o resto fora das cotas, Fase 4) |
| `teto_cpu` | como `cpu`, mas já em 0,45 | 0 | nada: acima disso, CPU não melhorou na calibração com vídeo |

- **Sem nada a pedir, o lance é 0.** A VR perde a rodada, o piso a protege, e o vídeo leva a sobra. Pela fórmula do plano, a VR daria lance mesmo sem nada a pedir, e chegou a vencer uma rodada vazia (ver "A vitória vazia", abaixo).
- **CPU só com throttling.** É a lição da Fase 4: atraso alto sem a cota esgotar não se resolve com CPU.
- **O agente não pede o piso sozinho.** Quem sabe se o piso vale (VR ativa) é o leiloeiro, e ele devolve ao piso quem está abaixo quando perde uma rodada. Os pedidos de CPU e de banda já vão pelo menos até o piso.
- **Os parâmetros** (`d_pedir` 0,5; `throttling_min` 0,02; `cpu_passo` 0,05; `cpu_teto` 0,45; `bw_passo` 1,0) ficam em `vr.sla_agent` no YAML, com esses valores como padrão. O SLA e o piso vêm do `vr/calibracao.yaml`.

## Como rodar

```bash
cd spade_agents && ../venv/bin/python -m unittest test_agente_vr test_leilao -v; cd ..   # os 26 testes do agente e do leilão
./run_experiment_tmux.sh tests_configs/test_config_hvr_l2.yaml hvrl2_NN   # H-VR em L2 (~16 min)
```

O pós-execução é o da Fase 4 (`docs/coleta-vr.md`). O `vr_decisoes.csv` da pasta da execução traz uma linha por lance da VR: a situação, `d`, o lance, os limites atuais e os pedidos, o atraso, o throttling e a pausa.

## Testes sem cluster

- **26 testes** (16 do agente e 10 do leilão), todos passando.
- **Integração local** (SPADE real, com o leiloeiro, 3 agentes de vídeo e o `VRSliceAgent`; Prometheus e Kubernetes falsos):
  - a VR fora e a VR no SLA dão lance 0, e o vídeo vence (o leiloeiro aceita o lance 0 sem nada de especial);
  - com atraso e throttling, a VR vence e a CPU sobe 0,05 por rodada (0,28 → 0,33 → 0,38);
  - sem throttling, não pede;
  - com o Prometheus atrasado 5 s em relação à pausa, a VR não pega o piso de volta.

## Validação no cluster (30/10/2026)

O cenário H-VR, contando só as 30 rodadas de dentro do teste:

| | L1 (`hvr01`) | L2 (`hvrl2_01`) | **L2 corrigida (`hvrl2_02`)** | Só vídeo (`fase5_video01`) |
| --- | --- | --- | --- | --- |
| VR no SLA | 100% | 95,6% | **100%** | — |
| Rodadas vencidas pela VR | 15 | 1 (pausada, pegou o piso) | 1 (vazia: não mudou nada) | — |
| CPU / banda máximas da VR | 0,47 / 27 Mbps | 0,28 / 9,9 | **0,28 / 9,9** | — |
| Bitrate médio: gold / silver / bronze (kbps) | 4 929 / 3 356 / 2 727 | 8 774 / 4 052 / 3 842 | **6 653 / 4 520 / 4 200** | 11 187 / 4 392 / 3 300 |
| Entradas no SLA na 1ª janela | 2 de 2 | 2 de 2 | 2 de 2 | — |

- **A VR deixou de disputar o leilão.** Na `hvrl2_02`, as decisões de dentro do teste foram só `no_sla` (18) e `fora` (12), sempre com o lance mínimo da época (10). A VR ficou no piso, e o vídeo levou o resto.
- **A vitória vazia.** Logo depois do início, os vídeos recém-reiniciados ainda dão lances baixos (8, 5 e 3), e o lance mínimo da VR na época (0,1 × base = 10) foi o maior. Ela "venceu" sem pedir nada: nenhum recurso mudou, mas a VR pagou 8, e o gold perdeu a rodada que teria vencido. **Corrigido depois da execução** (decisão do Pedro, 30/10/2026): sem nada a pedir, o lance passou a ser 0. Não houve execução no cluster com essa correção: ela foi conferida nos testes (um deles refaz essa rodada) e na integração local.
- **As 2 janelas fora do SLA na `hvrl2_01`** (de 661 a 781 s) são o resto fora das cotas da Fase 4: atraso de 3,8 a 5,5 ms com throttling zero. O agente as reconheceu como `fora_das_cotas` e corretamente não pediu CPU.
- **O vídeo varia entre as execuções.** O gold fez 8,8 e 6,7 Mbps nas duas da L2. Uma execução por condição não basta para medir o custo com precisão; isso fica para as 30 execuções da Fase 8. O padrão, porém, é claro: a L2 devolve ao vídeo a maior parte do que a L1 tirava.

### A correção depois da `hvrl2_01`

A única vitória da VR na `hvrl2_01` foi um erro: logo depois da saída inicial, a VR pausada venceu e voltou a 0,28 e 9,9, e ficou com isso parado por ~50 s. Somaram-se duas coisas:
- o agente usava uma leitura do Prometheus de até 20 s atrás, que ainda dizia "VR ativa";
- havia uma regra "abaixo do piso, peça o piso", e a `upf4` estava no mínimo da saída.

As três correções: a leitura passou a ser feita na hora do lance; a regra do piso saiu (o piso é do leiloeiro); e a `upf4` nos mínimos globais passou a querer dizer "VR fora", o que cobre os ~5 s em que o Prometheus ainda não viu a pausa. A `hvrl2_02` confirmou: a VR não ganhou mais nada pausada.

### O teste de pronto do plano: o aperto à mão

Na `hvrl2_01`, com o leilão e os agentes ainda rodando (o aperto foi feito ~6 min depois do `TEST_END`), a `upf4` foi apertada à mão para 150m:
- às 19:07:47, o agente viu a CPU em 0,15, deu lance 22 e venceu;
- às 19:08:17, ela estava de volta em 0,28: **1 rodada**, contra as 2 do critério.

Vale saber que qualquer rodada desfaz um aperto à mão: o leiloeiro reaplica os limites de todas as fatias a cada rodada, então o aperto dura no máximo até a rodada seguinte, com ou sem o agente.

## Limites

- **Uma execução por condição** (duas na L2). Os números mostram o mecanismo; a estatística é a da Fase 8.
- **O lance 0 de quem não pede nada ainda não rodou no cluster.** A primeira execução da Fase 8 confirma (a VR não pode aparecer como vencedora sem pedir nada).
- **O caminho de pedir CPU só foi exercitado no cluster pelo aperto à mão**, e com a versão de antes da correção. Nas duas execuções de L2, o piso de 0,28 bastou, e a VR nunca precisou de mais CPU. O caminho foi testado nos testes e na integração local.
- **O agente lê o Prometheus, que coleta a cada 5 s, e a janela da VR tem ~12,6 s.** A reação a um aperto real leva de uma a duas janelas mais uma rodada.
- **O resto fora das cotas continua**: com o vídeo alto, algumas janelas podem sair do SLA sem que nenhuma alavanca do leilão resolva (Fase 4).

## O que isso muda no plano

- **Fase 6:** o critério de pronto foi cumprido: a VR voltou ao SLA em 1 rodada depois do aperto à mão. E a L2 mostrou o que o plano esperava ("a VR fica no SLA; quando está bem, dá lance baixo e o vídeo leva a sobra"). Dois desvios do desenho do plano: sem nada a pedir, o lance é 0 (não 0,1 × base), e a CPU só é pedida com throttling.
- **Fase 7 (LLM):** o prompt passa a conhecer a VR e o piso. É a hora de decidir o bug do `inform` (a prioridade nova não muda o `priority` do agente), que afeta o cenário SC-VR.
- **Fase 8:**
  - os YAMLs de L1 (`test_config_hvr.yaml`) e L2 (`test_config_hvr_l2.yaml`) existem; falta o de L0 (cópia com `vr.floor: off` e saída `hvr_l0/`);
  - o custo para o vídeo e a variação entre execuções saem das 30 execuções por configuração;
  - o `vr_decisoes.csv` explica cada lance da VR.
