# Experimentos (Fase 8)

**O que é:** as 5 combinações do plano, 30 execuções de 15 min cada, intercaladas em ciclos. As tabelas e as figuras saem de um script, a partir dos CSVs brutos.

| Combinação | Cenário | Piso | Agente da VR | Prompt do LLM | YAML |
| --- | --- | --- | --- | --- | --- |
| `hvr_l0` | H-VR | off | utilização | artigo | `tests_configs/test_config_hvr_l0.yaml` |
| `hvr_l1` | H-VR | on | utilização | artigo | `tests_configs/test_config_hvr.yaml` |
| `hvr_l2` | H-VR | on | SLA | artigo | `tests_configs/test_config_hvr_l2.yaml` |
| `scvr_l2` | SC-VR | on | SLA | artigo | `tests_configs/test_config_scvr_l2.yaml` |
| `scvr_l3` | SC-VR | on | SLA | vr | `tests_configs/test_config_scvr_l3.yaml` |

- **Fixo em todas:** chave do inform desligada (`bidding.inform_updates_priority: false`) e drone com prioridade inicial 5 no SC-VR (Fase 7). A fila confere isso nos YAMLs antes de começar e não roda se estiver diferente.
- **Fora:** a configuração estática (E) e o cenário misto (M), por decisão de 01/11/2026.
- **A L0** é a L1 (`test_config_hvr.yaml`) com `vr.floor: off` e nada mais. O agente da VR continua por utilização, porque a L0 é "o sistema do artigo com a VR como mais uma fatia".

## Como rodar

Na ledzeppelin, da raiz do repositório:

```bash
git pull                                   # o código da fase; a ledzeppelin não tem chave do GitHub: entre com ssh -A (agente) para o pull
./run_fase8.sh --seco 1                    # mostra o que o próximo ciclo rodaria, sem rodar; um [ERRO] aqui impede a fila
./run_fase8.sh --ciclos 8                  # roda os próximos 8 ciclos (~12 h) num tmux "fase8" e volta; pode sair do SSH
./run_fase8.sh --status                    # quantas execuções válidas por combinação, as últimas falhas e avisos
tmux attach -t fase8                       # acompanha a fila (Ctrl+b d sai sem parar)
touch metricas_scripts/resultados/fase8_parar   # para a fila depois da execução atual (sem perder nada)
```

- **Retomar:** é rodar `--ciclos N` de novo. A fila lê o `fase8_fila.csv` e continua da 1ª execução que falta, com a mesma ordem de antes.
- **Ordem:** dentro de cada ciclo, as 5 combinações são sorteadas, com o número do ciclo como semente. Assim, uma variação do cluster ao longo do tempo cai igualmente em todas.
- **Código:** a fila não começa com mudança não commitada em arquivo do git, para todas as execuções rodarem no mesmo commit. O commit (via `git describe`) fica em cada linha do `fase8_fila.csv`.
- **O 1º ciclo é a conferência:** analisar as 5 execuções antes do 2º ciclo. Se estiverem certas, contam para as 30. Se aparecer um bug: corrigir, criar uma tag nova e mover o ciclo para `descartadas/` (apagando as linhas dele do `fase8_fila.csv`).

## O que cada execução faz

A fila chama o `run_experiment_tmux.sh <yaml> <nome> --completo`:

1. **Sobe tudo:** o reset das UPFs, os agentes, o LLM, o leiloeiro e o restart dos clientes de vídeo, como antes.
2. **Conferência dos clientes, antes de qualquer evento:**
   - cada cliente de vídeo tem de mostrar um `[STATUS 5s]` com bitrate > 0;
   - o UE de VR tem de ter a `uesimtun0`.

   O prazo é de até 180 s (`CONFERENCIA_S`). Se algo falhar, a sessão é fechada, o motivo vai para `conferencia.txt` e o script sai com 3. Na Fase 7, isso pegaria os 3 vídeos que não tocaram. O túnel da VR cai quando a `upf4` fica no mínimo do leilão e perde o PFCP. Ele volta ~20 s depois do reset: medido em 01/11/2026.
3. **Execução:** os 15 min. O log da VR é copiado durante a execução.
4. **Fim:** espera o `[SUCESSO]` do coletor de QoE e o `qoe_vr_timeseries.csv`, com limite de duração + 10 min (senão, sai com 4). Guarda o texto de cada janela do tmux em `janelas/` e fecha a sessão.
5. **Pós-execução:** `fetch_prometheus.py`, `resumo_execucao.py` (→ `resumo.txt`) e `merge_auction_and_qoe.py` (→ `analise/`).
6. **Veredito:** o `conferir_execucao.py` aplica as regras abaixo e grava o `veredito.txt`. Se for para descartar, sai com 5.

Depois de cada tentativa, a fila reseta as UPFs de novo, para a `upf4` não passar a pausa entre execuções no mínimo, e espera 60 s. Uma tentativa que falha vai para `descartadas/` e é repetida. **Com 3 tentativas falhando em seguida, a fila para**: o testbed deve estar com problema e precisa de alguém olhando.

Sem `--completo`, o `run_experiment_tmux.sh` faz só os passos 1 a 3 (com a conferência) e volta, como antes.

## Regras de descarte (escritas antes da 1ª execução)

Só falha de infraestrutura descarta. **Um resultado ruim da configuração não descarta.** A VR travar, sair do SLA ou perder a sessão na L0 é o que a L0 quer medir: é resultado, não falha.

| Regra | Por quê | Veredito |
| --- | --- | --- |
| Cliente de vídeo sem tocar, ou UE de VR sem túnel, na conferência | o cenário não começou completo | descartar |
| Sem `TEST_END`, ou um `TEST_ERROR` / `*_FAIL` / `*_ERROR` / `*_FAILED` no `test_events.csv` | o roteiro de eventos não rodou inteiro | descartar |
| Um vídeo com bitrate 0 o teste inteiro, ou com menos de 80% das amostras esperadas | cliente ou coletor morto | descartar |
| Leilão com menos de 80% das rodadas esperadas (uma a cada 30 s) | um agente caiu | descartar |
| Sem `qoe_vr_timeseries.csv`, ou mais de 10% das janelas da VR com `health = 0` | o instrumento da VR falhou | descartar |
| SC-VR sem nenhuma resposta do LLM | servidor do LLM fora do ar | descartar |
| Até 10% das janelas da VR com `health = 0` | ficam fora da conta do SLA, como sempre | aviso |
| VR ativa sem janela por mais de 60 s | olhar se é a VR travada (resultado) ou o instrumento | aviso |
| Vídeo com os quadros parados por 60 s ou mais, fora das pausas | olhar | aviso |
| A VR vence uma rodada com lance 0 | conferência pendente da Fase 6: não deveria acontecer | aviso |
| Menos respostas do LLM que intenções | olhar a janela `llm` | aviso |

Uma execução com aviso **vale** e entra na análise. O aviso é só para olhar. Os limites (80%, 10%, 60 s) estão no topo do `metricas_scripts/conferir_execucao.py`.

O verificador foi testado em 01/11/2026:
- nas 9 execuções piloto das Fases 5 a 7, as 9 deram `ok`;
- em 7 cópias estragadas de propósito (sem `TEST_END`, vídeo mudo, sem LLM, `health = 0`, leilão curto, VR com lance 0 vencendo, buraco nas janelas da VR), deu o veredito esperado em todas.

A fila foi testada com um `run_experiment_tmux.sh` falso. Os casos foram: falha e repetição; 3 falhas seguidas; retomada; uma pasta que sobrou de uma interrupção; o pedido de parada; código sem commit; um YAML com a chave do inform ligada.

## Como os resultados ficam organizados

```
metricas_scripts/resultados/            (fora do git)
├── calibracao/  fase7_llm/  piloto/    ← das fases anteriores; a Fase 8 não mexe
├── hvr_l0/  hvr_l1/  hvr_l2/  scvr_l2/  scvr_l3/
│     └── hvrl0_01 … hvrl0_30/          ← <prefixo>_<ciclo>; uma pasta por execução válida
├── descartadas/<combinação>/<nome>-tentativaN/   ← o que falhou, com conferencia.txt ou veredito.txt
├── fase8_fila.csv                      ← uma linha por tentativa: ciclo, ordem, nome, início, fim, status, motivo, commit
└── fase8_fila.log                      ← tudo o que a fila e o run_experiment_tmux.sh escreveram
```

Cada execução tem `config.yaml` (o YAML exato que rodou), os CSVs de sempre, `vr_log.txt`, `janelas/`, `resumo.txt`, `analise/` e `veredito.txt`.

Para trazer ao PC, de dentro de `metricas_scripts/resultados/` (copia por cima e só acrescenta o que é novo):

```bash
ssh -o ClearAllForwardings=yes k8s-inf 'cd ~/dev/projeto-5g/metricas_scripts/resultados && tar cf - hvr_l0 hvr_l1 hvr_l2 scvr_l2 scvr_l3 descartadas fase8_fila.csv fase8_fila.log' | tar xf -   # traz a Fase 8 da ledzeppelin
```

## Onde parei

- **01/11/2026:** preparação feita. Estão prontos:
  - o YAML da L0;
  - a conferência e o modo `--completo` no `run_experiment_tmux.sh`;
  - o `conferir_execucao.py`;
  - a fila (`run_fase8.sh` + `scripts/fila_fase8.py`);
  - este doc.

  Falta a tag `v2.0-fase8`, que espera a decisão abaixo.
- **Bloqueio (02/11/2026, 00:50 UTC): a `upf2` está presa sem PFCP.** A conferência foi testada com um restart real dos 3 vídeos e pegou o `ue-video-03` sem túnel por 180 s. É a mesma falha dos 3 vídeos que não tocaram na Fase 7. A causa:
  - a `upf2` perdeu o vínculo PFCP com o SMF às 17:42 de 01/11 (no relógio do cluster), no fim da `scvrl2_on03`, com os bombeiros apertados pelo leilão em 0,04 núcleo e 1 Mbps;
  - o lado da UPF ficou "de-associated", e o SMF não percebeu: continua mandando pedidos de sessão, que a UPF recusa ("cannot handle PFCP message type[50]"). O UE recebe `NETWORK_FAILURE` a cada 7,5 s;
  - o reset do `upf_set_resources.py` não conserta: a `upf2` está em 200m e 10M e continua presa.

  Hipótese, ainda não testada: a anotação de banda do Calico vale para todo o tráfego do pod, inclusive o PFCP, e com 1 Mbps e o vídeo enchendo a fila, os heartbeats se perdem. A `upf4` no mínimo mostra o mesmo, mas lá o SMF percebe e reassocia sozinho.

  Também visto: as 3 UPFs de vídeo acumulam sessões (1800 a 2400, quase nenhuma removida) e logam "All IP addresses in all subnets are occupied" desde 30/10. Os clientes 01 e 05 tocam assim mesmo, então isso sozinho não bloqueia.
- **Decisão do Pedro antes da 1ª execução:** como recuperar a `upf2` (reiniciar o pod?) e o que a fila faz se isso voltar a acontecer no meio dos ciclos.
- **Depois:** o Pedro roda `./run_fase8.sh --ciclos 1` na ledzeppelin. As 5 execuções do 1º ciclo são analisadas antes de seguir.
- **Ainda por escrever, durante os ciclos:** o script único de análise (`analise_fase8.py`). Ele produz as tabelas e as figuras com média e intervalo de confiança de 95% por combinação.
