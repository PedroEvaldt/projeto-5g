# Experimentos (Fase 8)

**O que é:** as 5 combinações do plano, 30 execuções de 15 min cada, intercaladas em ciclos. As tabelas e as figuras saem de um script, a partir dos CSVs brutos.

| Combinação | Cenário | Piso | Agente da VR | Prompt do LLM | YAML |
| --- | --- | --- | --- | --- | --- |
| `hvr_l0` | H-VR | off | utilização | artigo | `tests_configs/test_config_hvr_l0.yaml` |
| `hvr_l1` | H-VR | on | utilização | artigo | `tests_configs/test_config_hvr.yaml` |
| `hvr_l2` | H-VR | on | SLA | artigo | `tests_configs/test_config_hvr_l2.yaml` |
| `scvr_l2` | SC-VR | on | SLA | artigo | `tests_configs/test_config_scvr_l2.yaml` |
| `scvr_l3` | SC-VR | on | SLA | vr | `tests_configs/test_config_scvr_l3.yaml` |

- **Fixo em todas:** chave do inform desligada (`bidding.inform_updates_priority: false`), drone com prioridade inicial 5 no SC-VR (Fase 7) e `vr.espera_saida_s: 5` (ver "A VR e a upf4"). A fila confere isso nos YAMLs antes de começar e não roda se estiver diferente.
- **Fora:** a configuração estática (E) e o cenário misto (M), por decisão de 01/11/2026.
- **A L0** é a L1 (`test_config_hvr.yaml`) com `vr.floor: off` e nada mais. O agente da VR continua por utilização, porque a L0 é "o sistema do artigo com a VR como mais uma fatia". Sem piso, a entrada da VR não muda nada no leilão; a saída libera o que ela tinha, como na L1 (até 02/11/2026 a saída também era ignorada, e a VR ficava com a CPU que ganhou nas rodadas de aquecimento e nunca entrava numa rede cheia: o 1º ciclo foi refeito por isso). Desde 03/11/2026 a VR fica pausada entre as execuções e entra de fato numa `upf4` no mínimo; o PFCP da `upf4` cai logo depois, e a VR fica ~1 min sem sessão. É resultado da L0 (ver "A VR e a upf4").

## A VR e a upf4: por que a saída espera 5 s

Incidente de 02/11/2026 (detalhes em "Onde parei"): a `upf4` perdeu a associação PFCP com o SMF em quase toda execução, e na `hvrl0_02` ficou presa até o pod ser recriado. As UPFs de vídeo, nas mesmas 10 execuções, não perderam nenhuma vez (no ciclo 1 novo, de 03/11, duas perderam, depois de um corte de banda: ver abaixo).

- **O mecanismo:** a saída da VR pausava o tráfego e, no mesmo segundo, o leiloeiro cortava a `upf4` para o mínimo (0,01 núcleo, 1 Mbps). A `upf4` gastava 0,15 núcleo para os 8 Mbps da VR (~0,2 ms por pacote); com 0,01, ela processa ~50 pacotes por segundo. O servidor do PhotonPath ainda manda por ~2 s depois da pausa, e a fila de pacotes levou 30 s para escoar, com o throttling em 100%. A resposta do SMF ao heartbeat esperava atrás desses pacotes, o temporizador da `upf4` estourava e ela desfazia a associação, 10 a 25 s depois do corte. Sem tráfego, o mínimo não derruba nada: na SC-VR a `upf4` ficou 5 min em 0,01/1 Mbps sem perder o PFCP.
- **Por que só a VR:** o vídeo também chega a 0,01 núcleo tocando, mas o leilão o baixa de rodada em rodada, e o TCP desacelera quando a UPF fica lenta. A VR é UDP a taxa fixa e era cortada 15× de uma vez.
- **Quando a `upf4` levava o corte:** (a) no T+0,3 s ("a VR começa fora"), com a VR tocando desde o fim da execução anterior; (b) na saída no meio do teste; (c) depois do `TEST_END`, quando a VR era retomada com a `upf4` ainda no mínimo. As 8 quedas do ciclo 2 caem nesses três casos.
- **O que mudou (decisão do Pedro, 03/11/2026):** a saída pausa, espera `vr.espera_saida_s` (5 s) e só então avisa o leiloeiro (`deploy_test.py`), o que cobre (a) e (b). No `TEST_END`, a VR é pausada em vez de retomada, e fica parada entre execuções, o que cobre (c). O leiloeiro não mudou. Com a espera, o que a VR tinha volta ao vídeo 5 s mais tarde; por isso as 10 execuções anteriores foram apagadas e a Fase 8 recomeçou do ciclo 1.

O ciclo 1 novo (03/11/2026) confirmou a correção: a `upf4` não caiu nenhuma vez na L1, na L2 e no SC-VR, e o `FLOOR_LEAVE` veio 5 s depois de cada saída. Ele também mostrou quatro coisas:

- **Na L0, a entrada da VR derruba o PFCP da `upf4`.** Sem piso, a VR entra (T+60 s) com a `upf4` no mínimo. O PFCP com o SMF caiu 12 e 16 s depois, nas duas tentativas; o SMF apagou a sessão da VR, e cada nova tentativa do UE (a cada 7,5 s) falhou até a associação voltar, ~60 s depois. A VR ficou 62 a 70 s sem sessão. No ciclo 1 antigo isso não acontecia porque a VR tocava entre as execuções: no início do teste, a taxa de 1 min da `upf4` ainda estava alta, e o agente por utilização já comprava antes da entrada. **É resultado da L0, não falha** (decisão do Pedro, 03/11/2026): sem piso, a entrada da VR derruba o plano de controle da própria UPF. A análise mostra essa perda de sessão à parte. Ela também melhora o vídeo, já que a VR sem sessão não gasta nada.
- **A banda também derruba o PFCP, mesmo com CPU sobrando.** Na L0 caíram duas UPFs de vídeo, cada uma logo depois de um corte de banda: a `upf2` (silver, de 5,79 para 2,88 Mbps, com 0,19 núcleo) caiu 14 s depois e ficou presa; a `upf3` (bronze, de 4,08 para 1,16 Mbps, com 0,20 núcleo) caiu 8 s depois e voltou em 1,4 s. Com 0,19 núcleo sobra CPU para um vídeo de 3 Mbps: são dois indícios de que o limite de banda do Calico atrasa ou descarta os heartbeats junto com o tráfego (a pendência "CPU ou banda" continua sem o teste controlado). A `upf2` ficou presa de um lado só: ela desistiu do SMF, e o SMF continuou achando que estava associado até o pod ser recriado.
- **Uma UPF presa não para a sessão que já está aberta.** Com a `upf2` presa desde T+203 s, o silver continuou tocando, com o mesmo padrão da tentativa válida (1 356 × 1 178 kbps depois de voltar; 726 × 749 kbps depois da volta do bronze). Por isso a regra do verificador mudou (ver "Regras de descarte").
- **Um SMF de fora fala com a `upf4`.** Desde que o pod da `upf4` foi recriado (03/11, 11:52), o `10.251.46.199` pede associação PFCP a cada 11 s. Ele não é o nosso `smf` (`10.251.46.210`) nem o `smf2` (que só atende a `upf8` a `upf10`), e não está no `pfsevaldt`: deve ser o SMF de outro namespace, apontando para o IP que o pod novo herdou (o `pfsevaldt` não lista os outros namespaces). Ele não cria sessões na `upf4`. Mas cai e volta sozinho (59 vezes na `hvrl0_01`), e o `pfcp_upf.py` contava esses eventos como da `upf4`: na tentativa 1, a volta dele fez uma queda de 61 s do nosso SMF parecer de 35 s, abaixo do limite do aviso. Desde 04/11/2026, só os eventos do nosso SMF decidem; os dele ficam no `upf_pfcp.txt`.

## Como rodar

**Uma vez, antes da 1ª execução:** os clientes de vídeo têm de usar a estratégia `Recreate`. O `videoclients-script.sh` já cria assim; os deployments que estão no ar foram criados antes e precisam do patch. A fila confere isso e não começa sem ele.

```bash
for d in ue-video-01 ue-video-03 ue-video-05; do kubectl patch deployment $d -n pfsevaldt --type=merge -p '{"spec":{"strategy":{"type":"Recreate","rollingUpdate":null}}}'; done   # o restart passa a matar o pod antigo antes de subir o novo
kubectl get deployment ue-video-01 ue-video-03 ue-video-05 -n pfsevaldt -o jsonpath='{range .items[*]}{.metadata.name}={.spec.strategy.type} {end}{"\n"}'   # tem de mostrar =Recreate nos três
```

Por quê: com o `RollingUpdate` (o padrão), o `rollout restart` sobe o pod novo antes de apagar o antigo. Os dois ficam de pé com o mesmo IMSI, e o AMF às vezes para de encaminhar os pedidos de sessão do novo ("Holding NG Context", "Unknown UE by SUCI"). O UE fica sem `uesimtun0`, e o cliente de vídeo espera para sempre. É o mesmo problema que a VR teve na Fase 2. Visto ao vivo no `ue-video-03` em 02/11/2026.

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
- **O 1º ciclo é a conferência:** analisar as 5 execuções antes do 2º ciclo. Se estiverem certas, contam para as 30. Se aparecer um bug: corrigir, criar uma tag nova e mover o ciclo para `descartadas/` (apagando as linhas dele do `fase8_fila.csv`). Desde 03/11/2026, conferir também: o `upf_pfcp.txt` de cada execução sem quedas da `upf4`; o `FLOOR_LEAVE` do `auction_history.csv` ~5 s depois do `VR_LEAVE`; e a VR pausada entre as execuções (`[COMANDO] pausa` logo depois do `TEST_END` no `vr_log.txt`).

## O que cada execução faz

A fila chama o `run_experiment_tmux.sh <yaml> <nome> --completo`:

1. **Sobe tudo:** o reset das UPFs, os agentes, o LLM, o leiloeiro e o restart dos clientes de vídeo, como antes.
2. **Conferência dos clientes, antes de qualquer evento:**
   - cada cliente de vídeo tem de mostrar um `[STATUS 5s]` com bitrate > 0;
   - o túnel do UE de VR tem de levar até o `vr-app-01`: um ping pela `uesimtun0` até o pod do servidor (o ClusterIP não responde a ping). Até 03/11/2026 bastava a `uesimtun0` existir; mas, com a `upf4` recriada, o túnel pode ficar de pé sem levar a lugar nenhum (visto num ensaio: a VR com 100% de perda e `health = 1`, o que entraria na análise como resultado). A VR pausada não atrapalha o ping.

   O prazo é de até 180 s (`CONFERENCIA_S`). Se algo falhar, a sessão é fechada, o motivo vai para `conferencia.txt` e o script sai com 3. Na Fase 7, isso pegaria os 3 vídeos que não tocaram.
3. **Execução:** os 15 min. O log da VR é copiado durante a execução. No `TEST_END`, o `deploy_test.py` pausa a VR.
4. **Fim:** espera o `[SUCESSO]` do coletor de QoE e o `qoe_vr_timeseries.csv`, com limite de duração + 10 min (senão, sai com 4). Guarda o texto de cada janela do tmux em `janelas/`, fecha a sessão e grava em `upf_pfcp.txt` as quedas e voltas do PFCP de cada UPF, com o SMF de cada evento, e as sessões que o nosso SMF criou, restaurou e removeu nos DNNs da execução (`scripts/pfcp_upf.py capturar`): o log some quando o pod é recriado.
5. **Pós-execução:** `fetch_prometheus.py`, `resumo_execucao.py` (→ `resumo.txt`) e `merge_auction_and_qoe.py` (→ `analise/`).
6. **Veredito:** o `conferir_execucao.py` aplica as regras abaixo e grava o `veredito.txt`. Se for para descartar, sai com 5.

Depois de cada tentativa, a fila reseta as UPFs de novo, para a `upf4` não passar a pausa entre execuções no mínimo, e espera 60 s. Uma tentativa que falha vai para `descartadas/` e é repetida. **Com 3 tentativas falhando em seguida, a fila para**: o testbed deve estar com problema e precisa de alguém olhando.

**Antes de cada tentativa, a fila conserta o testbed sozinha** (`scripts/pfcp_upf.py`; decisão do Pedro de 03/11/2026, que substitui a recuperação à mão de 01/11):
- **UPF presa:** a última linha de PFCP do log com o nosso SMF (o pod `app=smf`) é "de-associated" e não aparece "associated" em 30 s (as quedas normais voltam antes). A fila apaga o pod, espera o novo, roda o `upf_set_resources.py` (o pod novo nasce com o template) e espera a reassociação por até 60 s.
- **UDM cheio:** "Maximum number of SDM Subscriptions" no log dos últimos 10 min. A fila apaga o pod.
- **Túnel da VR morto:** sem resposta ao ping por 30 s. A fila recria o UE de VR pelo procedimento de sempre (apagar o deployment, esperar o pod sumir, `vrclients-script.sh`). Ela é a única fatia cujo UE as execuções não reiniciam, e, com a `upf4` recriada, o UE às vezes fica com a sessão numa UPF que não existe mais.

Cada conserto vai para o `fase8_fila.log` e para o `fase8_reparos.csv` (hora, execução seguinte, alvo, motivo, resultado), e o `--status` mostra os últimos. **Se um conserto falhar, a fila para**, e a recuperação é à mão (os mesmos passos). Para ver o estado sem a fila: `venv/bin/python scripts/pfcp_upf.py estado tests_configs/test_config_hvr.yaml` (as UPFs e o UDM) e `... tunel ue-vr-07`.

Sem `--completo`, o `run_experiment_tmux.sh` faz só os passos 1 a 3 (com a conferência) e volta, como antes.

## Regras de descarte (escritas antes da 1ª execução)

Só falha de infraestrutura descarta. **Um resultado ruim da configuração não descarta.** A VR travar, sair do SLA ou perder a sessão na L0 é o que a L0 quer medir: é resultado, não falha. As duas regras do PFCP entraram em 03/11/2026, depois do incidente da `hvrl0_02` e antes do recomeço do ciclo 1, ou seja, também antes da 1ª das 150 execuções que valem. Uma UPF presa é um defeito do Open5GS, que não se recupera sem recriar o pod. Na `hvrl0_02`, ela deixou a VR sem sessão na 2ª entrada, e a execução tinha passado só com um aviso.

A regra da UPF presa mudou em 03/11/2026, depois do ciclo 1 novo (decisão do Pedro): ela só descarta se o travamento chegou ao cliente. Na `hvrl0_01`, a `upf2` presa não mudou nada no silver, porque a sessão aberta passa dados sem o PFCP; e os travamentos aparecem onde os cortes são mais fortes, então descartá-los tiraria justamente as piores execuções da L0. A regra nova vale do ciclo 2 em diante; o ciclo 1 fica como foi julgado (a tentativa 1 da `hvrl0_01` em `descartadas/`). O que mudou é como se reconhece uma falha do Open5GS, não o resultado de uma configuração.

O cliente "tem sessão no fim" se o nosso SMF criou (ou restaurou) a sessão dele antes do `TEST_END`, ela ainda não tinha sido removida e durou pelo menos 10 s. Com a UPF presa, o SMF escreve "criada" a cada tentativa do UE e a remove ~7,5 s depois; os 10 s separam essas tentativas de uma sessão de verdade. A sessão de antes da janela (a da VR, cujo UE não é reiniciado) conta como de pé até aparecer a remoção. A `upfN` atende o DNN `sliceNN` (configmap do `smf`), e cada DNN da execução tem um UE só.

| Regra | Por quê | Veredito |
| --- | --- | --- |
| Cliente de vídeo sem tocar, ou UE de VR sem túnel, na conferência | o cenário não começou completo | descartar |
| Sem `TEST_END`, ou um `TEST_ERROR` / `*_FAIL` / `*_ERROR` / `*_FAILED` no `test_events.csv` | o roteiro de eventos não rodou inteiro | descartar |
| Um vídeo com bitrate 0 o teste inteiro, ou com menos de 80% das amostras esperadas | cliente ou coletor morto | descartar |
| Leilão com menos de 80% das rodadas esperadas (uma a cada 30 s) | um agente caiu | descartar |
| Sem `qoe_vr_timeseries.csv`, ou mais de 10% das janelas da VR com `health = 0` | o instrumento da VR falhou | descartar |
| SC-VR sem nenhuma resposta do LLM | servidor do LLM fora do ar | descartar |
| Uma UPF sem PFCP no `TEST_END` há mais de 30 s (`upf_pfcp.txt`) e o cliente dela sem sessão no fim (ou sem o log do SMF para saber) | o Open5GS travou e tirou o cliente da rede: o resto do teste mediu a falha, não o leilão | descartar |
| Uma UPF sem PFCP no `TEST_END` há mais de 30 s, com a sessão do cliente de pé | a sessão aberta passa dados sem o PFCP; a fila recria a UPF antes da próxima execução | aviso |
| Uma UPF sem PFCP por mais de 60 s durante o teste, mesmo voltando sozinha | longa o bastante para pegar uma entrada da VR (na H-VR, ela entra aos 60 s) | aviso |
| Até 10% das janelas da VR com `health = 0` | ficam fora da conta do SLA, como sempre | aviso |
| VR ativa sem janela por mais de 60 s | olhar se é a VR travada (resultado) ou o instrumento | aviso |
| Vídeo com os quadros parados por 60 s ou mais, fora das pausas | olhar | aviso |
| A VR vence uma rodada com lance 0 | conferência pendente da Fase 6: não deveria acontecer | aviso |
| Menos respostas do LLM que intenções | olhar a janela `llm` | aviso |

Uma execução com aviso **vale** e entra na análise. O aviso é só para olhar. Os limites (80%, 10%, 60 s; 30 e 60 s do PFCP; 10 s da sessão) estão no topo do `metricas_scripts/conferir_execucao.py`. As quedas curtas, que voltam sozinhas, não viram aviso (aconteciam em quase toda execução), mas ficam no `upf_pfcp.txt` para a análise contar. Uma trava nos últimos 30 s do teste não descarta: a fila conserta antes da próxima, e o que se perde é só esse final.

O verificador foi testado em 01/11/2026:
- nas 9 execuções piloto das Fases 5 a 7, as 9 deram `ok`;
- em 7 cópias estragadas de propósito (sem `TEST_END`, vídeo mudo, sem LLM, `health = 0`, leilão curto, VR com lance 0 vencendo, buraco nas janelas da VR), deu o veredito esperado em todas.

As regras do PFCP foram testadas em 03/11/2026, em 8 cópias da piloto `hvrl2_02` com um `upf_pfcp.txt` montado à mão: sem o arquivo, `ok` (nada muda nas antigas); queda de 11 s, `ok`; queda de 75 s, aviso; presa desde T+360 s, descartar; presa desde antes do início, descartar; presa a 16 s do fim, `ok`; queda só depois do fim, `ok`; erro ao ler o log, aviso. O leitor do log tem testes: `venv/bin/python -m unittest scripts/test_pfcp_upf.py -v`.

Em 03/11/2026, depois do ciclo 1 novo, os testes passaram de 4 para 22, com linhas reais do log da `upf4` e do `smf`: o SMF de fora não esconde a queda do nosso nem inventa uma; arquivo antigo, sem a coluna do SMF; e a regra da UPF presa (sessão de pé, tentativas recusadas até depois do fim, sessão de antes da janela, restauração, tentativa em curso no fim, sessão nova no meio, outro DNN, erro no log do SMF). Ao vivo, numa cópia dos scripts fora do repositório da ledzeppelin, recapturando as duas tentativas da `hvrl0_01`: a tentativa 1 passaria de "descartar" para aviso (a sessão do silver, aberta antes do início, só foi removida depois do conserto) e ganharia o aviso "`upf4` sem PFCP por 61 s", que o SMF de fora tinha escondido; a tentativa 2 ficaria com o mesmo veredito.

A fila foi testada com um `run_experiment_tmux.sh` falso. Os casos foram: falha e repetição; 3 falhas seguidas; retomada; uma pasta que sobrou de uma interrupção; o pedido de parada; código sem commit; um YAML com a chave do inform ligada. Os consertos foram ensaiados ao vivo em 03/11/2026, numa cópia dos scripts fora do repositório da ledzeppelin: a `upf4` recriada à força (PFCP de volta em 34 a 49 s, com 200m e 10M); a conferência com tudo bom (0,9 s, nada a fazer); o túnel da VR morto depois da `upf4` recriada (uma vez voltou sozinho em 13 s pela "PFCP restoration" do SMF, na outra ficou morto até o UE ser recriado); o UE recriado pelo módulo (túnel de volta em 10 s, janela da VR com 8 Mbps e `health = 1`); e um YAML sem `espera_saida_s`, recusado.

## Como os resultados ficam organizados

```
metricas_scripts/resultados/            (fora do git)
├── calibracao/  fase7_llm/  piloto/    ← das fases anteriores; a Fase 8 não mexe
├── hvr_l0/  hvr_l1/  hvr_l2/  scvr_l2/  scvr_l3/
│     └── hvrl0_01 … hvrl0_30/          ← <prefixo>_<ciclo>; uma pasta por execução válida
├── descartadas/<combinação>/<nome>-tentativaN/   ← o que falhou, com conferencia.txt ou veredito.txt
├── fase8_fila.csv                      ← uma linha por tentativa: ciclo, ordem, nome, início, fim, status, motivo, commit
├── fase8_reparos.csv                   ← um conserto automático por linha (UPF presa, UDM cheio, túnel da VR)
└── fase8_fila.log                      ← tudo o que a fila e o run_experiment_tmux.sh escreveram
```

Cada execução tem `config.yaml` (o YAML exato que rodou), os CSVs de sempre, `vr_log.txt`, `janelas/`, `upf_pfcp.txt`, `resumo.txt`, `analise/` e `veredito.txt`.

Para trazer ao PC, de dentro de `metricas_scripts/resultados/` (copia por cima e só acrescenta o que é novo):

```bash
ssh -o ClearAllForwardings=yes k8s-inf 'cd ~/dev/projeto-5g/metricas_scripts/resultados && tar cf - hvr_l0 hvr_l1 hvr_l2 scvr_l2 scvr_l3 descartadas fase8_fila.csv fase8_reparos.csv fase8_fila.log' | tar xf -   # traz a Fase 8 da ledzeppelin
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

  Hipótese, ainda não testada: a anotação de banda do Calico vale para todo o tráfego do pod, inclusive o PFCP, e com 1 Mbps e o vídeo enchendo a fila, os heartbeats se perdem. A `upf4` no mínimo mostra o mesmo, mas lá o SMF percebe e reassocia sozinho. (Corrigido em 03/11/2026: nem sempre; ver o incidente abaixo.)

  Também visto: as 3 UPFs de vídeo acumulam sessões (1800 a 2400, quase nenhuma removida) e logam "All IP addresses in all subnets are occupied" desde 30/10. Os clientes 01 e 05 tocam assim mesmo, então isso sozinho não bloqueia.
- **Recuperação (02/11/2026, decisão do Pedro):** os pods da `upf1`, `upf2` e `upf3` foram apagados e recriados pelos deployments. Depois do `upf_set_resources.py`, as três reassociaram o PFCP, e as sessões acumuladas zeraram. O `ue-video-03` continuou sem túnel, mas aí por **outra causa**: o IMSI duplicado no restart (ver "Como rodar"). O patch `Recreate` falta: é uma mudança no cluster, então o Pedro roda.
- **Se uma UPF ficar presa sem PFCP durante os ciclos:** ~~a fila não reinicia UPF sozinha~~ (substituído em 03/11/2026: agora a fila conserta; ver "O que cada execução faz").
- **Depois do patch:** o Pedro roda `./run_fase8.sh --ciclos 1` na ledzeppelin. As 5 execuções do 1º ciclo são analisadas antes de seguir.
- **02/11/2026:** ciclo 1 ok (5 de 5); ciclos 2 a 30 começaram às 19:22 (horário da ledzeppelin).
- **Incidente (02/11/2026, 20:38, horário da ledzeppelin): a `upf4` ficou presa sem PFCP, e a fila parou.**
  - Na `hvrl0_02`, a saída da VR cortou a `upf4` para 0,01/1 Mbps com o fim do fluxo na fila. 14 s depois, ela desfez a associação, no mesmo milissegundo em que o SMF a refazia: o SMF ficou achando que estava associado, e a `upf4` recusando os pedidos de sessão ("cannot handle PFCP message type[50]"). A VR voltou aos 600 s sem sessão, e a execução passou só com o aviso "VR ativa sem janela por 300 s".
  - A `hvrl0_03` falhou 3 vezes na conferência (VR sem `uesimtun0`), e a fila parou às 21:00, como devia.
  - Durante a noite, o UE da VR pediu sessão a cada ~1,1 s por 14 h, e o UDM chegou ao limite de 20 000 assinaturas SDM. Ele passou a recusar qualquer pedido de sessão, de qualquer UE (HTTP 400; no UE, `PAYLOAD_NOT_FORWARDED`). Os vídeos só seguiam tocando porque mantinham as sessões abertas.
  - Recuperação (03/11, o Pedro): os pods do UDM e da `upf4` foram recriados, mais o `upf_set_resources.py`.
  - A análise (CPU, throttling e tráfego da `upf4` no Prometheus, amostra a amostra; os logs da `upf1` a `upf4`) está em "A VR e a upf4". Também se viu que a `upf4` acumulava sessões nunca removidas (683 no pod antigo).
  - Decisões do Pedro (03/11/2026): apagar as 10 execuções e as 3 tentativas da Fase 8 e recomeçar do ciclo 1 com a espera de 5 s na saída e a VR pausada entre execuções; a fila consertar sozinha (UPF presa, UDM cheio e, achado no ensaio, o túnel da VR morto); as duas regras do PFCP no verificador. Fica pendente, sem pressa: testar se quem derruba o PFCP é a CPU ou a banda (fixar a `upf4` em 0,01 com banda normal, e em 200m com 1 Mbps, com a VR saindo).
- **Ciclo 1 novo (03/11/2026, 11:57 a 13:44, horário da ledzeppelin): 5 de 5 válidas**, uma com aviso. A tentativa 1 da `hvrl0_01` foi descartada (`upf2` presa desde T+203 s), a fila recriou a `upf2` sozinha (PFCP de volta em 39 s), e a tentativa 2 passou.

  | Execução | Veredito | VR no SLA |
  | --- | --- | --- |
  | `hvrl2_01` | ok | 42 de 45 (93,3%): as 3 janelas logo depois da volta do bronze, com `net_p95` de 5,3 a 6,2 ms, o efeito conhecido dos 3 vídeos |
  | `scvrl2_01` | ok | 33 de 33 |
  | `scvrl3_01` | ok | 32 de 32 |
  | `hvrl0_01` | aviso (bronze parado por 330 s; VR sem janela por 66 s) | 20 de 41 (48,8%); no ciclo antigo, 88,9% |
  | `hvrl1_01` | ok | 45 de 45 |

  Os achados (a L0, a banda, a UPF presa com a sessão de pé, o SMF de fora) estão em "A VR e a upf4". Decisões do Pedro (03/11/2026): só o nosso SMF decide (`pfcp_upf.py` e `conferir_execucao.py`); a perda de sessão da VR na L0 é resultado; a UPF presa só descarta se o cliente ficou sem sessão; não conferir agora se a VR passa pela `upf4`. Esse último fica anotado como risco: com a `upf4` fora, o SMF tenta pôr a sessão da VR nas outras UPFs da lista (`upf5`, `upf6`, `upf7`, `upf1`, `upf2`, `upf3`, em rodízio); no ciclo 1 todas recusaram, mas, se uma aceitasse, a VR passaria por fora do leilão sem alarme. Também anotado: as UPFs voltam a acumular sessões velhas (a `upf3` tem 16 para um UE), sem efeito visto no tráfego.
- **Próximo:** commitar, fazer o `git pull` na ledzeppelin e rodar `./run_fase8.sh --ciclos 29`.
- **Ainda por escrever, durante os ciclos:** o script único de análise (`analise_fase8.py`). Ele produz as tabelas e as figuras com média e intervalo de confiança de 95% por combinação. Ele deve contar também as quedas de PFCP do `upf_pfcp.txt` (só as do nosso SMF), as UPFs presas com a sessão de pé, a perda de sessão da VR na entrada da L0 e os consertos do `fase8_reparos.csv`.
