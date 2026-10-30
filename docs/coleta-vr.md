# Coleta e análise da VR (Fase 4)

**Pergunta:** como medir a VR dentro de uma execução do experimento, ao lado do vídeo e do leilão? E como ela se comporta quando os vídeos estão rodando?

**Resposta:** a coleta e a análise da VR estão prontas, do log do pod até a figura do artigo e o Prometheus. A primeira execução real trouxe um achado: **com os 3 vídeos tocando, a VR precisa de mais CPU do que a calibração sozinha mostrava**. O piso de CPU passou de 0,22 para **0,28 núcleo**. Mesmo com CPU de sobra, a VR fica em **~91% das janelas no SLA** com o vídeo no máximo, por causa de um atraso que vem de fora de qualquer cota.

## O que foi construído

| Arquivo | O que faz | O que sai |
| --- | --- | --- |
| `metricas_scripts/deploy_test.py` | grava a hora real do início no `TEST_START` (`inicio=...`, com fuso) | `test_events.csv` |
| `metricas_scripts/analise_metricas_vr.py` (novo) | lê as janelas `[VR]` do log, leva cada uma para a régua dos eventos e marca o SLA com os números do `vr/calibracao.yaml` | `qoe_vr_timeseries.csv`, `vr_relogio.txt` |
| `metricas_scripts/resumo_execucao.py` | seção 4: SLA na execução inteira e por fase, o critério que reprovou e o tempo da retomada até a 1ª janela no SLA | terminal |
| `metricas_scripts/merge_auction_and_qoe.py` | a VR a cada 30 s, ao lado da CPU e da banda que o leilão deu a ela | `correlation_vr.csv`, `graph_vr_events.png` |
| `metricas_scripts/csv_graficos_ieee.py` | com `--vr <pasta>`, a figura do artigo do cenário com VR, agregando as execuções da pasta | `hvr_timeline.pdf` e `_1col` |
| `metricas_scripts/fetch_prometheus.py` | corrigido: pede a janela no relógio do Prometheus (ver "Relógios") | `infra_timeseries.csv` |
| `client_scripts/vrclients-script.sh` | Service e ServiceMonitor da porta 9200: o Prometheus coleta `vr_net_p95_us` e as outras métricas da VR | — |
| `vr/graficos_calibracao.py` | `--com-video`: a série de CPU com os 3 vídeos vira uma segunda curva e dá o piso de CPU | `vr/calibracao.yaml`, `calib_cpu.pdf` |

Nas execuções só de vídeo, todos esses scripts dão as mesmas saídas de antes. Isso foi conferido comparando as saídas antes e depois de cada mudança na `exec01`.

**Coletar depois, gravar durante.** Durante a execução, uma linha de shell copia o log bruto da VR para a pasta. Depois, o coletor lê esse arquivo.
- O exporter não reinicia a cada execução (o IMSI 07 seria registrado duas vezes), então o log fica no pod. Mesmo assim, a cópia protege contra a rotação do log pelo kubelet e contra um reinício do container.
- O log bruto fica guardado com os resultados, e a pasta pode ser reanalisada depois que o pod sumir.
- Sem o `vr_log.txt`, o coletor busca as janelas pelo `kubectl logs`.

## Como rodar uma execução com VR

```bash
tmux new-session -d -s vrlog "kubectl logs -f --timestamps --since=1s deploy/ue-vr-07 -c vr-client -n pfsevaldt > /tmp/vr_log_execNN.txt"   # copia o log da VR enquanto a execução roda
./run_experiment_tmux.sh tests_configs/test_config.yaml execNN   # a execução de sempre (~15 min)
tmux kill-session -t vrlog   # no fim: para a cópia
mv /tmp/vr_log_execNN.txt metricas_scripts/resultados/<pasta>/execNN/vr_log.txt   # o log da VR junto dos outros CSVs
cd metricas_scripts && R=resultados/<pasta>/execNN   # os scripts de análise rodam daqui
TEST_FILE=../tests_configs/_run_execNN.yaml ../venv/bin/python fetch_prometheus.py $R/   # métricas das UPFs, no relógio certo
../venv/bin/python analise_metricas_vr.py $R/   # janelas da VR + SLA -> qoe_vr_timeseries.csv
../venv/bin/python resumo_execucao.py $R/   # resumo de uma página, com a seção 4 da VR
../venv/bin/python merge_auction_and_qoe.py $R/ $R/analise/   # tabela por 30 s e gráfico da VR
../venv/bin/python csv_graficos_ieee.py --vr resultados/<pasta>   # figura do artigo, agregando as execuções da pasta
```

- **Uma pasta por configuração.** As execuções com VR não podem ficar dentro das pastas dos cenários só de vídeo, senão entram nas figuras deles. As duas desta fase estão em `resultados/fase4_vr/`.
- **Para trazer pastas ao PC, use `rsync`.** Copiar uma pasta por cima de outra que já existe cria uma dentro da outra. Isso aconteceu com a `fase4_exec01`.

```bash
rsync -a ledzeppelin:~/dev/projeto-5g/metricas_scripts/resultados/fase4_vr/ metricas_scripts/resultados/fase4_vr/   # traz as execuções sem criar pasta dentro de pasta
curl -s 'http://localhost:35235/api/v1/query?query=vr_net_p95_us'; echo   # confere que o Prometheus coleta a VR (com o encaminhamento de porta aberto)
```

## Relógios

O cluster (o `blacksabbath`, os pods e o Prometheus) está **187,5 a 187,7 s à frente** da ledzeppelin, que roda em UTC. A diferença foi medida três vezes entre 29 e 30/10/2026 e quase não andou (0,17 s em ~7 h).

- **O leilão não é afetado.** Os agentes perguntam ao Prometheus "quanto está agora?", sem horário.
- **O coletor da VR** mede a diferença com um `kubectl exec` no pod e a guarda em `vr_relogio.txt`. O erro é de ±0,1 s.
- **O `fetch_prometheus.py` pedia a janela da execução pelo relógio da ledzeppelin.** Por isso, o `infra_timeseries.csv` saía **~3 min deslocado e sem os últimos ~3 min** do teste. As planilhas do `csv_planilhas.py` são as únicas que leem esse arquivo, e herdaram o erro.
  - Na `exec01` (14/10), só 53% das amostras batiam com os pausa/volta dos eventos. Deslocando ~3,5 min, 98,6% batiam.
  - Corrigido: o script pergunta a hora ao Prometheus (`time()`) e pede a janela no relógio dele. Na `fase4_exec02`, 97,7% batem sem ajuste, e o arquivo vai até 900 s.
  - Os `infra_timeseries.csv` gerados antes da correção continuam errados. Só podem ser refeitos enquanto o Prometheus guardar os dados (15 dias, por padrão).

## A VR com os vídeos rodando

As duas execuções usaram o cenário hierárquico de sempre, com a VR ligada o tempo todo e fora do leilão. A banda da `upf4` ficou em 10 Mbps, como o `upf_set_resources.py` deixa.

### `fase4_exec01`: a `upf4` em 200m

A VR ficou em **56% das janelas no SLA**, contra 100% na calibração sozinha com a mesma CPU. Só o `net` reprovou; a vazão, a perda e a continuidade ficaram perfeitas. É a assinatura de falta de CPU da Fase 3.

| Fase | Vídeos tocando | Throttling da `upf4` | VR no SLA | `net` mediano |
| --- | --- | --- | --- | --- |
| 0–121 s | os 3 | 23% | 22% | 6,3 ms |
| 121–271 s | silver + bronze | 7% | 83% | 2,5 ms |
| 271–421 s | só o bronze | 3% | 100% | 2,1 ms |
| 421–540 s | gold + bronze | 19% | 44% | 5,9 ms |
| 540–661 s | só o gold | 20% | 30% | 6,2 ms |
| 661–781 s | gold + silver | 9% | 60% | 3,7 ms |
| 781–901 s | os 3 | 24% | 33% | 5,8 ms |

O throttling é a % dos períodos de 100 ms em que a `upf4` esgotou a cota (cAdvisor, taxa de 1 min).

- **Quanto mais vídeo passa pelas UPFs, pior a VR.** A correlação de Spearman entre o tráfego de vídeo e o `net` é 0,74, e entre o throttling e o `net`, 0,78.
- **A `upf4` gasta mais CPU pela mesma VR quando o vídeo dos outros sobe:** de 0,13 a 0,17 núcleo (Spearman 0,71), com o tráfego da VR constante.
  - Com o limite em 0,2, a média fica abaixo da cota, mas as rajadas de cada período de 100 ms passam dela.
  - Por que cada pacote fica mais caro não foi conferido. Duas hipóteses: o cache e a memória divididos no nó, ou o kernel cobrando da `upf4` o processamento de rede dos pacotes de vídeo (softirq).

### `fase4_exec02`: o controle, com a `upf4` em 400m

Mesma execução, mudando só a CPU da `upf4`. Ela foi trocada à mão logo depois do `upf_set_resources.py`.

| Vídeo nas UPFs | 200m: `net` / no SLA | 400m: `net` / no SLA |
| --- | --- | --- |
| até 3 Mbps | 1,97 ms / 100% | 2,00 ms / 100% |
| 3 a 8 Mbps | 2,46 ms / 100% | 2,29 ms / 100% |
| 8 a 12 Mbps | 4,10 ms / 56% | 2,46 ms / 100% |
| 12 a 16 Mbps | 6,22 ms / 21% | 2,22 ms / 100% |
| mais de 16 Mbps | 6,33 ms / 42% | 2,90 ms / 92% |

Com 400m, a VR ficou em **97% no SLA** (69 de 71 janelas), com **throttling de 0%** e uso de 0,11 a 0,18 núcleo. A causa principal era a cota. Ainda sobra um efeito pequeno: o `net` sobe de ~2,0 para ~2,9 ms com o vídeo alto.

### Calibração de CPU com os 3 vídeos tocando

Mesmo método da Fase 3: 5 repetições de 2 min, em ordem sorteada, julgadas pelo SLA da Fase 3. Os 3 vídeos tocaram o tempo todo, sem leilão, com as UPFs de vídeo em 200m e 10 Mbps. O tráfego ficou entre 14 e 18 Mbps (~16 em média).
- O player fecha a sessão 20 min depois de subir (`SESSION_DURATION=1200`) e não volta sozinho. Para a calibração, o valor foi para 4500 s com `kubectl set env` e voltou a 1200 no fim.
- Arquivo: `calib_cpu_20261030_104624.csv`.

| CPU da `upf4` | `net` p95 (média ± IC) | Janelas no SLA | Throttling médio |
| --- | --- | --- | --- |
| 0,25 | 3,18 ± 0,69 ms | 86,7 ± 18,0% | 0,7% |
| 0,30 | 3,29 ± 1,06 ms | 86,7 ± 22,7% | 0,1% |
| 0,35 | 2,39 ± 0,30 ms | 100% | 0,0% |
| 0,40 | 3,24 ± 0,26 ms | 91,1 ± 15,1% | 0,2% |
| 0,45 | 3,03 ± 0,85 ms | 91,1 ± 18,0% | 0,0% |

- **De 0,25 para cima, mais CPU não muda nada.** A correlação de Spearman entre a CPU e o `net` é −0,07. O throttling some entre 0,2 (20–25% na `exec01`) e 0,25.
- **O 100% do 0,35 é acaso.** As medidas ruins se espalham no tempo. O vídeo durante as medidas do 0,35 foi o mesmo das outras (16,0 Mbps). A chance de algum dos 5 pontos tirar 5 medidas perfeitas por acaso é de ~40%.
- **Nenhum ponto chega a 95% das janelas no SLA.** Na série inteira, foram 205 de 225 (91,1%).

**A regra do piso foi adaptada para a série com vídeo.**
- O ponto mais alto (0,45) faz o papel da folga.
- O piso é o menor ponto cujo % no SLA não fica abaixo do IC dele (73,1%), com todos os de cima também, mais 10%.
- Resultado: **0,25 + 10% = 0,28 núcleo**.
- O `calibracao.yaml` traz 0,28 em `piso.cpu` e o 0,22 sem vídeo em `piso.cpu_sem_video`, só como referência. O SLA não mudou: ele é o que a VR precisa, medido sem carga.

### O resto fora das cotas

Durante a calibração com vídeo, **nenhuma cota da VR foi esgotada**:
- a `upf4` teve 0,2% de throttling em média;
- o `vr-app-01` (o servidor, com limite de 300m) teve 0%;
- os containers do `ue-vr-07` não têm limite de CPU.

Mesmo assim, o `net` de base da VR sobe de 1,9 para ~3 ms e, de vez em quando, passa de 4,87 ms. Esse resto vem de algo que as fatias dividem e que nenhuma cota controla. Os candidatos são a CPU dos nós (o `blacksabbath` roda os 3 Chromes que decodificam vídeo, o servidor da VR e os UERANSIM) e o processamento de rede do kernel. O leilão, que só mexe na CPU e na banda das UPFs, não alcança essa parte.

## Limites

- **Uma execução em cada condição** (200m e 400m). As tabelas por fase e por carga mostram o mecanismo, mas não são uma estatística de 30 execuções.
- **A calibração com vídeo usou a carga máxima** (os 3 vídeos, sem leilão), com a variação natural do ABR. Com menos vídeo, a VR precisa de menos.
- **O mecanismo da CPU extra da `upf4` e a origem do resto não foram conferidos.**
- **A figura do artigo com uma execução só fica "serrilhada"** (0 ou 100% a cada 30 s), porque cada período tem 2 ou 3 janelas.
- **Com o `--com-video`, a regra do piso compara intervalos de confiança de 5 medidas,** o que é largo. O 0,25 passa com folga, mas um ponto um pouco pior também passaria.

## O que isso muda no plano

- **Fase 4:**
  - o critério de pronto foi cumprido: uma execução de 15 min gera o `qoe_vr_timeseries.csv` e as figuras com VR, e o Prometheus coleta a VR;
  - o custo para o vídeo (com VR × sem VR) e o tempo até o piso agregado entre execuções dependem dos eventos `VR_JOIN`/`VR_LEAVE` da Fase 5.
- **Fase 5:**
  - `floor_cpu: 0.28` vem do `calibracao.yaml`. A rodada extra tem de levar a `upf4` a 0,28: os 200m do `upf_set_resources.py` só bastam sem vídeo;
  - a cópia do log da VR (`vrlog`) entra no `run_experiment_tmux.sh` quando a VR entrar no YAML.
- **Fase 6:**
  - o agente pode ler o **throttling da `upf4`**, que o Prometheus já tem. Atraso alto **com** throttling: pedir CPU resolve. Atraso alto **sem** throttling: CPU não resolve, e um lance alto só gastaria dinheiro;
  - as métricas da VR estão no Prometheus (`vr_net_p95_us`, `vr_continuity_pct`, `vr_loss_pct`, `vr_health_ok`...). Na pausa, o exporter repete o último valor, com `vr_paused=1`, e o agente tem de ignorar esses valores.
- **Fase 8:**
  - uma pasta por configuração;
  - esperar no máximo ~91% no SLA com o vídeo no máximo, em qualquer configuração. É um limite do testbed, não do leilão.
- **Futuro (decisão do Pedro, 30/10/2026):** mover o `vr-app-01` para um nó sem UEs (risco 3 do plano) e repetir só o 0,4 com os 3 vídeos, para ver se o resto vem do `blacksabbath` lotado. Se vier, o servidor muda de nó e a calibração é refeita lá.
