# O modelo 3D renderizado de verdade no servidor de VR (Fase 9)

**Pergunta:** as 150 execuções da Fase 8 usaram um servidor de VR que manda quadros sintéticos, do tamanho certo mas sem
imagem. Com o modelo 3D renderizado de verdade (Unity com GPU, seguindo a pose do cliente), o leilão e a VR se comportam
do mesmo jeito? Ou seja: o gerador sintético era uma aproximação fiel?

**Resposta:** sim, no que o leilão enxerga. O render real não mudou nada que a rede, o leilão ou os vídeos vejam:

- **Contra um servidor sintético no mesmo nó** (o controle), a `hvr_l2` com render ficou com o mesmo SLA da VR
  (79,6% × 77,8% das janelas, p = 0,82). Também ficaram iguais o `net`, a perda, a vazão, os lances, a CPU e a banda da
  VR, os vídeos e as quedas de PFCP.
- **Só o `m2p` mudou**, de ~13 para ~59 ms, e a diferença está toda no `hold`. Nem o SLA nem o agente olham o `m2p`.
- **Contra a Fase 8**, a `hvr_l0` e a `scvr_l2` ficaram dentro da variação das 30 execuções. A `hvr_l2` caiu de 97,9%
  para 79,6% das janelas no SLA (p = 0,03), mas o controle caiu igual. A queda vem, portanto, da topologia de hoje
  (servidor no `metallica`, UEs no `molejo`), e não do render.
  - A VR sai do SLA pelo `net` no último terço do teste, quando o gold toca a 14–15 Mbps.

Ou seja: o gerador sintético era uma aproximação fiel. O que deixou de valer, na `hvr_l2`, é a Fase 8 como referência
neste testbed.

**A causa da queda (investigação de 16/11/2026, depois do fechamento):** a causa provável é a falta de CPU no nó dos
UEs (`molejo`), provocada pelo player de vídeo do gold.

- **Um segundo controle** (servidor sintético no `molejo`, junto dos UEs) também cai: 89,8% das janelas no SLA, com 2 de
  5 execuções claramente abaixo de qualquer uma das 30 da Fase 8. Então basta o lado dos UEs.
- **Sondas de ping** no caminho da VR mostram o atraso inteiro no túnel 5G simulado até a `upf4`. A rede dos pods e o
  servidor não mudam.
- **O Prometheus** mostra o `molejo` com os núcleos disputados: com o gold a 15 Mbps, o Chrome dele gasta ~4 dos 12
  núcleos, e o UE e o gNB da VR (UERANSIM, no mesmo nó) esperam na fila do escalonador. A `upf4` e o `wb-fibre-01`
  ficam iguais nas janelas boas e ruins.
- **O `molejo` e o `blacksabbath` têm o mesmo hardware, mas não o mesmo software** (conferência de 17/11). A Fase 8
  rodou com uma versão do UERANSIM (o simulador do UE e do gNB, por onde passa o túnel), e a Fase 9 com outra: a imagem
  `latest` mudou entre a montagem de um nó e a do outro. Com a mesma carga, o kernel do `molejo` sofre mais. Nó e
  versão mudaram juntos, e os dados não separam os dois. A versão ficou fixa desde então (`docs/ambiente-continuo.md`).
- **É correlação, não prova.** Os testes que provam (prioridade de CPU ao `ue-vr-07`; o UERANSIM da Fase 8 no
  `molejo`) não foram feitos: o Pedro decidiu parar na correlação (16/11) e não rodar mais experimentos por ora (17/11).
- **Para o texto:** a queda é uma limitação do testbed (UEs e gNBs simulados no mesmo nó que os players de vídeo), não
  do leilão nem do render. Há um parágrafo pronto na seção "Como descrever no texto".

## Ambiente

- 12 e 13/11/2026 (validação) e 16/11/2026 (controle), namespace `pfsevaldt`.
- Servidor de VR (`vr-app-01`) sempre no `metallica`, com o `photon-server` (PhotonPath, 300m) e o `unity-render`
  (imagem `pedroevaldt/vr-render:fase9.2`, 4 núcleos, 4 GiB e 1 GPU) no mesmo pod. O `metallica` é um Dell R630 com
  uma NVIDIA A2 e um Xeon E5-2640 v4 (40 núcleos). Decisão do Pedro (12/11/2026): o nó é fixo, porque em outra placa
  o tempo de render e o `m2p` mudam.
- UEs, gNBs e o `dash-server` no `molejo` desde 09/11. Na Fase 8, ficavam no `blacksabbath`, junto com o servidor de VR.
  A `upf4` no `wb-fibre-01`, como na Fase 8.
- UERANSIM: as Fases 8 e 9 pediram a mesma imagem (`free5gc/ueransim:latest`), mas ela mudou entre as duas. O
  `blacksabbath` tinha a criada em 19/10/2026 (digest `8215d043…`); o `molejo` baixou a de 08/11 (`da47add1…`) em 09/11.
  kubelet, containerd e Ubuntu também diferem (1.34.10 × 1.34.13, 2.3.3 × 2.4.2, 24.04.4 × 24.04.5).
- O cliente de VR (`ue-vr-07`) manda a pose do passeio gravado pelo museu (`vr/passeio.csv`, 2 min 16 s, em laço). O
  `unity-render` desenha cada quadro nessa pose e o codifica; o `photon-server` manda o quadro codificado no lugar do
  payload sintético, no mesmo perfil de pacotes (60 quadros/s × 14 pacotes de 1200 B).
- Testbed montado com `scripts/build_testbeds.sh --render-real`.
- O segundo controle e as sondas (16/11/2026, 18:57 a 20:24) rodaram com o servidor sintético no `molejo`, sem GPU.

## Método

### Calibração (etapa 6)

A topologia mudou desde a Fase 3. O servidor de VR saiu do nó dos UEs e foi para o `metallica`, e por isso o `net_ref`
tinha de ser medido de novo. A calibração foi refeita inteira, com o mesmo `vr/calibrar.py` e o mesmo
`vr/graficos_calibracao.py` das Fases 3 e 4, e gravada à parte, no `vr/calibracao_render.yaml`. O `vr/calibracao.yaml`
da Fase 8 não mudou.

```bash
venv/bin/python vr/calibrar.py --recurso banda --pontos 8 8.5 9 10 15 --repeticoes 5 --saida metricas_scripts/resultados/calibracao_render     # banda, sem vídeo (~57 min)
venv/bin/python vr/calibrar.py --recurso cpu --pontos 0.15 0.2 0.4 --repeticoes 5 --saida metricas_scripts/resultados/calibracao_render       # CPU, sem vídeo (~34 min)
venv/bin/python vr/calibrar.py --recurso cpu --pontos 0.2 0.25 0.3 0.4 --repeticoes 5 --saida metricas_scripts/resultados/calibracao_render  # CPU com os 3 vídeos tocando (~45 min)
venv/bin/python vr/graficos_calibracao.py <banda> <cpu sem vídeo> --com-video <cpu com vídeo> --yaml vr/calibracao_render.yaml \
    --descricao "Fase 9: servidor de VR com o render real no metallica"                                                                    # figuras e o vr/calibracao_render.yaml
```

### Validação (etapa 7)

Três combinações da Fase 8, 5 execuções de cada, intercaladas, pela mesma fila (`./run_fase9.sh`, que é o
`scripts/fila_fase8.py` com `FILA_FASE=9`):

- `hvr_l0` e `hvr_l2`, as duas que mais contrastam no H-VR;
- `scvr_l2`, para ter um cenário SC-VR.

Os YAMLs `tests_configs/test_config_*_render.yaml` são cópias dos da Fase 8 com três mudanças: `vr.render: real`,
`vr.calibracao: vr/calibracao_render.yaml` e a saída em `resultados/fase9/`. As regras de descarte são as da Fase 8,
mais as do render: o render caiu, mais de 0,1% de quadros atrasados, envio reancorado ou mais de 5% dos quadros sem a
pose da sessão.

```bash
./run_fase9.sh --ciclos 5                                          # 5 ciclos de 3 execuções, no tmux fase9 da ledzeppelin
cd metricas_scripts && ../venv/bin/python analise_fase9.py         # compara com o por_execucao.csv da Fase 8 -> resultados/fase9_analise/
```

A comparação é por combinação. Cada métrica da Fase 8 tem a média, o IC de 95% e a faixa das 30 execuções. Para a Fase 9,
conta-se quantas das 5 execuções caem nessa faixa, e a diferença das médias é testada pelo t de Welch. A análise roda
no PC, com as pastas trazidas da ledzeppelin, porque o venv da ledzeppelin não tem `scipy`.

### Controle (16/11/2026)

A `hvr_l2` saiu da faixa da Fase 8, mas entre as duas fases mudaram duas coisas: o servidor e a topologia. O controle
separa as duas. São 5 execuções de `hvr_l2` com o PhotonPath sintético da Fase 8 (`client_scripts/vr-server.yaml`, só
com o nó trocado) no mesmo `metallica`, no mesmo testbed e com a mesma calibração. O YAML
`tests_configs/test_config_hvr_l2_sintetico.yaml` é o da `hvr_l2` da Fase 8 com a calibração da Fase 9 e a saída em
`resultados/fase9s/`.

```bash
scripts/trocar_servidor_vr.sh sintetico     # troca o vr-app-01 pelo PhotonPath sintético, no metallica, e recria o UE de VR
FILA_FASE=9s ./run_fase9.sh --ciclos 5      # 5 × hvr_l2 com o servidor sintético -> resultados/fase9s/ (tmux fase9)
scripts/trocar_servidor_vr.sh render        # volta o render real (Unity + PhotonPath) no metallica
cd metricas_scripts
../venv/bin/python analise_fase9.py resultados/fase9s resultados/fase8_analise/por_execucao.csv resultados/fase9s_analise Controle    # controle × Fase 8
../venv/bin/python analise_fase9.py resultados/fase9 resultados/fase9s_analise/por_execucao.csv resultados/fase9_vs_9s "Fase 9" Controle   # render × controle
```

A leitura é direta:

- **Se o controle ficar na faixa da Fase 8**, a queda é do render.
- **Se cair junto com a Fase 9**, a queda é da topologia, e o render não muda nada.

### Segundo controle e sondas (16/11/2026, depois do fechamento)

Com o controle no `metallica` a queda continuou, e o Pedro quis saber a causa. Duas medidas novas, com a GPU já liberada.

**O segundo controle** põe o servidor sintético no `molejo`, no mesmo nó dos UEs: é a Fase 8 com só o nó dos UEs
trocado (do `blacksabbath` para o `molejo`). O YAML `tests_configs/test_config_hvr_l2_molejo.yaml` é o da `hvr_l2` da
Fase 8, com a saída em `resultados/fase9m/`. Como o servidor está no nó dos UEs, a calibração é a da Fase 8.

**As sondas** (`scripts/sonda_rede.sh`) rodam dentro do `ue-vr-07` durante a fila: 5 pings por segundo, em 4 destinos,
cada um medindo um pedaço do caminho da VR:

| Sonda | Do UE até | Por onde |
| --- | --- | --- |
| `upf4_rede` | `upf4` | rede dos pods (sem o 5G) |
| `upf4_tunel` | `upf4` | túnel 5G (`uesimtun0`: UERANSIM, GTP) |
| `servidor_rede` | `vr-app-01` | rede dos pods |
| `servidor_tunel` | `vr-app-01` | túnel 5G (o caminho dos pacotes da VR) |

O `analise_sondas.py` cruza os pings com as janelas da VR pela hora do pod, e divide as janelas em boas e ruins pelo
limite do `net_p95`. A CPU dos containers e dos nós veio do Prometheus (`prom_cpu_no_hvr_l2.py`, com os horários de
início de `t0_execucoes.py`), para as 45 execuções `hvr_l2` (30 da Fase 8, 5 de cada controle e da Fase 9).

```bash
scripts/trocar_servidor_vr.sh sintetico molejo                                                  # põe o servidor sintético no molejo e recria o UE de VR
tmux new-session -d -s sonda "scripts/sonda_rede.sh metricas_scripts/resultados/fase9m_sonda"   # liga as sondas (tmux kill-session -t sonda para parar)
FILA_FASE=9m ./run_fase9.sh --ciclos 5                                                          # 5 × hvr_l2 -> resultados/fase9m/ (tmux fase9)
cd metricas_scripts
../venv/bin/python analise_sondas.py resultados/fase9m_sonda resultados/fase9m/hvr_l2          # sondas × janelas da VR -> fase9m_sonda/janelas.csv
../venv/bin/python t0_execucoes.py fase8 "resultados/hvr_l2/*/qoe_vr_timeseries.csv" fase9m "resultados/fase9m/hvr_l2/*/qoe_vr_timeseries.csv" \
    | ../venv/bin/python prom_cpu_no_hvr_l2.py > resultados/fase9m_prom/prom_fases.csv          # CPU do nó dos UEs por execução, do Prometheus (na ledzeppelin)
```

### Conferência dos dois nós (17/11/2026)

O `molejo` esperava o dobro do `blacksabbath` com a mesma carga, e os dois têm a mesma especificação. Para achar a
diferença, sem rodar nada novo:

- **O nó núcleo a núcleo** (`prom_nucleos_hvr_l2.py`), nas 46 execuções `hvr_l2`: as 30 da Fase 8, as 15 do `molejo` e
  a `hvrl2c_01` do ambiente novo (`docs/ambiente-continuo.md`). Frequência real, temperatura, núcleo mais ocupado, como
  os pacotes de rede se dividem entre os núcleos, softnet squeezed e a espera do núcleo que mais espera. O script
  também grava a versão (digest) do UERANSIM de cada execução.
- **O hardware e as versões dos dois nós**, do Prometheus, no horário de uma execução de cada: núcleos físicos,
  memória e placas de rede (node-exporter); kubelet, containerd, Ubuntu e as imagens dos containers
  (`kube_node_info` e `kube_pod_container_info`).

```bash
cd metricas_scripts
../venv/bin/python t0_execucoes.py fase8 "resultados/hvr_l2/*/qoe_vr_timeseries.csv" fase9 "resultados/fase9/hvr_l2/*/qoe_vr_timeseries.csv" \
    fase9s "resultados/fase9s/hvr_l2/*/qoe_vr_timeseries.csv" fase9m "resultados/fase9m/hvr_l2/*/qoe_vr_timeseries.csv" \
    fase10 "resultados/fase10_teste/hvr_l2/*/qoe_vr_timeseries.csv" \
    | ../venv/bin/python prom_nucleos_hvr_l2.py > resultados/fase9m_prom/prom_nucleos.csv      # o nó dos UEs núcleo a núcleo e o UERANSIM, por execução (na ledzeppelin)
```

## Resultados

### Calibração

Os CSVs, as figuras e o `LEIAME.md` ficam em `metricas_scripts/resultados/calibracao_render/`, fora do git. Nenhuma
medida foi descartada, e as 540 janelas vieram com `health=1`.

| | Fase 8 (`vr/calibracao.yaml`) | Fase 9 (`vr/calibracao_render.yaml`) |
| --- | --- | --- |
| `net_ref` | 1,87 ms | 1,92 ms |
| Limite do `net_p95` | 4,87 ms | 4,92 ms |
| Banda, sem vídeo | corte entre 8,5 e 9 Mbps | 8,5 Mbps: 0%; 9 Mbps: 100% |
| CPU, sem vídeo | 0,17: 40%; 0,2: 100% | 0,15: 0% (`net` ~31 ms); 0,2: 100% |
| CPU, com os 3 vídeos | 0,25 a 0,45: 87 a 100% (91% na série) | 0,2: 75,6%; 0,25 a 0,4: 100% |
| **Pisos** (CPU / banda / CPU sem vídeo) | **0,28 / 9,9 / 0,22** | **0,28 / 9,9 / 0,22** |

- **O caminho até o `metallica` custa 0,05 ms.** O `net_ref` foi de 1,87 para 1,92 ms, e o limite do SLA subiu o mesmo
  tanto. As curvas de banda e de CPU caem nos mesmos pontos, e os pisos são os mesmos. Assim, o leilão da Fase 9 tem as
  mesmas regras da Fase 8.
- **Com os vídeos, o teto de ~91% sumiu.** Na Fase 4, mesmo com CPU de sobra, a VR ficava em ~91% das janelas no SLA
  com os 3 vídeos tocando. Era "o resto fora das cotas" (`docs/coleta-vr.md`): o `vr-app-01` dividia o nó com os
  Chromes dos players e com os UERANSIM. Com o servidor no `metallica`, as 135 janelas de 0,25 núcleo para cima
  ficaram no SLA, com `net_p95` de ~2,3 a 2,5 ms.
  - Isso responde, de tabela, ao teste anotado no plano ("mover o `vr-app-01` para um nó sem UEs").
  - Não dá para separar as causas. Além do servidor, o nó dos UEs também mudou desde a Fase 4 (`blacksabbath` ->
    `molejo`).
- **O `m2p` é o do render.** Com banda folgada, o `m2p_p95` mediano foi de ~56 ms (máximo de 62). Na calibração da
  Fase 3, com os quadros sintéticos, era de 12,8 ms. A diferença está toda no `hold`: ~54 ms contra ~11 ms. O valor
  bate com o terceiro ensaio. Nem o SLA nem o agente olham o `m2p`.

### Validação

As 15 execuções (12 e 13/11) saíram válidas na primeira tentativa. Houve um conserto automático: a `upf3` estava sem
PFCP antes da `hvrl0r_03`, e o pod foi recriado. Seis execuções saíram com aviso:

- **`hvr_l0`, as 5:** a VR ficou ativa sem janela por 68 a 96 s. Ela entra numa `upf4` sem piso, como na Fase 8.
- **`hvrl0r_04`:** a `upf4` ficou sem PFCP por 62 s.
- **`hvrl0r_03`:** o bronze ficou com os quadros parados por 325 s.
- **`scvrl2r_03`:** a `upf3` ficou sem PFCP do T+418 s até o fim, com a sessão do cliente de pé.

Tabelas completas em `metricas_scripts/resultados/fase9_analise/tabelas.md`.

| Fase 8 → Fase 9 (média de cada fase; na faixa = execuções da Fase 9 dentro do mínimo–máximo da Fase 8) | H-VR L0 | H-VR L2 | SC-VR L2 |
| --- | --- | --- | --- |
| VR: janelas no SLA (%) | 68,1 → 63,2 (4 de 5; p = 0,56) | **97,9 → 79,6 (1 de 5; p = 0,03)** | 99,6 → 99,4 (5 de 5; p = 0,78) |
| VR: `net_p95`, mediana das janelas (ms) | 2,59 → 3,17 | 2,27 → 2,42 | 2,46 → 2,55 |
| VR: `m2p_p95`, mediana das janelas (ms) | **13,5 → 60,3** | **13,1 → 59,1** | **13,1 → 57,5** |
| VR: perda média (%) | 3,98 → 5,21 | 0,050 → 0,047 | 0,050 → 0,052 |
| Rodadas vencidas pela VR | 14,5 → 14,6 | 0 → 0 | 0 → 0 |
| CPU / banda da VR ativa (núcleos / Mbps) | 0,279 / 12,8 → 0,278 / 13,3 | 0,28 / 9,9 → igual | 0,28 / 9,9 → igual |
| Vídeos: bitrate com a VR ativa | sem diferença (p ≥ 0,39) | sem diferença (p ≥ 0,32) | sem diferença (p ≥ 0,19) |
| Quedas de PFCP por execução | 1,69 → 2,20 | 0,07 → 0 | 0,03 → 0,20 |

- **O `m2p` subiu ~46 ms nas três, e é a única medida que muda em todas.** É o render: o `hold` foi de ~11 para ~56 ms.
  O `net` e a perda ficaram dentro da variação da Fase 8, menos na `hvr_l2` (abaixo).
- **A `hvr_l0` e a `scvr_l2` ficaram na faixa.** Isso vale para o SLA da VR, os lances, a CPU e a banda, os vídeos e o
  PFCP. Uma ressalva de leitura: com valores iguais e variância quase zero, o Welch dá p < 0,001 em diferenças de
  0,000 (CPU e banda no piso) ou de 0,001 (vazão), e a CPU da SC-VR aparece "0 de 5 na faixa" pelo arredondamento de
  0,280. Nada disso é diferença de verdade.
- **A `hvr_l2` caiu, e só pelo `net`.** As janelas reprovaram todas por `net` (perda, vazão e continuidade passaram),
  e quase todas no último terço do teste. Aos 661 s o silver volta (o bronze só aos 781 s), e o gold, a fatia de
  maior prioridade no leilão, sobe para 14–15 Mbps. A conta usa 671 s, o início da fase seguinte no resumo da execução. A partir daí o `net_p95` vai a 8–10 ms e cai aos poucos:

| `hvr_l2` | Fase 8 (30) | Fase 9 (5) | Controle (5) |
| --- | --- | --- | --- |
| Janelas fora do SLA antes dos 671 s | 4 de 810 (0,5%) | 6 de 135 (4,4%) | 5 de 135 (3,7%) |
| Janelas fora do SLA dos 671 s ao fim | 24 de 540 (4,4%) | 40 de 90 (44%) | 45 de 90 (50%) |
| Execuções com `net_p95` > 4,92 ms depois dos 671 s | 10 de 30 | 5 de 5 | 5 de 5 |

- A 2ª entrada da `hvrl2r_01` levou 102 s até o SLA (nas outras, 12,8 s): ela caiu bem num desses picos (615 a 731 s).
- **O que foi descartado** na `hvrl2r_01`, pelo Prometheus:
  - **O servidor estava normal no trecho:** idade do quadro, quadros enviados, cortados e sem pose normais, e zero
    atrasados. O `photon-server` usava ~0,07 núcleo, sem throttling, e o `unity-render` ~1,3.
  - **O agente e a `upf4` não foram a causa:** o agente aplicou o piso em 2,9 s, e a `upf4` não teve throttling.
  - **A carga do `molejo` não explica:** ficou em ~50% tanto no trecho ruim quanto no bom, e o gNB e o UE da VR
    gastaram o mesmo nos dois. O Chrome do gold usava 3,8 a 4,7 dos 12 núcleos com o gold em 13–15 Mbps.
  - **Na Fase 8, o mesmo gold não afetou a VR:** na `hvrl2_05` ele também foi a 13–15 Mbps, e o `net` da VR ficou em
    1,9–2,5 ms.

### O controle

| `hvr_l2` | Fase 8 | Controle (sintético no `metallica`) | Fase 9 (render no `metallica`) |
| --- | --- | --- | --- |
| VR: janelas no SLA (%) | 97,9 ± 1,0 | 77,8 ± 14,9 | 79,6 ± 15,5 |
| Por execução | 93,3 a 100 | 66,7; 93,3; 66,7; 86,7; 75,6 | 82,2; 64,4; 88,9; 68,9; 93,3 |
| Diferença para a Fase 8 (p) | — | −20,1 (0,019) | −18,4 (0,030) |
| VR: `net_p95`, mediana das janelas (ms) | 2,27 | 2,43 | 2,42 |
| VR: `m2p_p95`, mediana das janelas (ms) | 13,12 | 13,25 | 59,14 |
| gold: bitrate com a VR ativa (kbps) | 12.401 | 12.905 | 12.945 |

As 5 execuções (16/11) saíram válidas, sem aviso nem conserto. Tabelas em
`metricas_scripts/resultados/fase9s_analise/` (controle × Fase 8) e `fase9_vs_9s/` (render × controle).

- **Render × controle: nada difere além do `m2p`.** O SLA dá p = 0,82, e as outras medidas p ≥ 0,31 (`net`, perda,
  vazão, entradas, vídeos); lances, CPU, banda e PFCP ficaram idênticos. O `m2p` subiu 45,9 ms (IC de 44,5 a 47,3).
- **Controle × Fase 8: a mesma queda** (p = 0,019), no mesmo trecho do teste. Ela vem do que o controle e a Fase 9
  têm em comum e a Fase 8 não tinha:
  - o servidor no `metallica`, no lugar do nó dos UEs;
  - os UEs no `molejo`, no lugar do `blacksabbath`.
- **A calibração não pegou o efeito.** Nela, a VR ficou 100% no SLA com os 3 vídeos, mas sem leilão: as UPFs de vídeo
  ficavam nos 200m do `upf_set_resources.py`. No leilão, o gold ganha CPU e banda e chega a 14–15 Mbps, e é esse
  trecho que falha.

### A causa da queda (investigação de 16/11/2026)

#### O segundo controle: o servidor no `molejo`

| `hvr_l2` | Fase 8 (`blacksabbath`) | Servidor no `molejo` | Servidor no `metallica` (controle) |
| --- | --- | --- | --- |
| VR: janelas no SLA (%) | 97,9 ± 1,0 | 89,8 ± 16,4 | 77,8 ± 14,9 |
| Por execução | 93,3 a 100 | 84,4; 97,8; 68,9; 100; 97,8 | 66,7; 93,3; 66,7; 86,7; 75,6 |
| Diferença para a Fase 8 (p) | — | −8,1 (0,24) | −20,1 (0,019) |
| Janelas fora do SLA dos 671 s ao fim | 24 de 540 (4,4%) | 22 de 90 (24%) | 45 de 90 (50%) |

As 5 execuções saíram válidas, sem aviso. A queda também aparece com o servidor ao lado dos UEs, em 2 das 5 execuções
(a 03, com 68,9%, e a 01, com 84,4%, ficam abaixo de todas as 30 da Fase 8). Então o servidor no `metallica`
não é a causa única: o lado dos UEs (o `molejo`) basta. Contra o controle no `metallica`, a diferença de +12 pontos
não é separável com 5 execuções (p = 0,17).

#### As sondas: o atraso está no túnel 5G

225 janelas das 5 execuções: 202 boas e 23 ruins (`net_p95` acima de 4,87 ms). Nenhum ping se perdeu.

| Medida (p95 mediano, ms) | Janelas boas | Janelas ruins | Spearman com o `net_p95` |
| --- | --- | --- | --- |
| `net_p95` da VR | 2,29 | 6,73 | — |
| `upf4_rede` | 0,58 | 0,66 | 0,52 |
| `upf4_tunel` | 1,87 | 6,56 | 0,67 |
| `servidor_rede` | 0,13 | 0,28 | 0,29 |
| `servidor_tunel` | 2,32 | 6,65 | 0,66 |

- **A rede física está inocente:** pela rede dos pods, o tempo quase não muda.
- **O servidor está inocente:** o atraso já aparece inteiro até a `upf4`, antes do servidor.
- **O atraso está no que só o túnel tem:** o UE e o gNB do UERANSIM (no `molejo`), o GTP, ou o programa da `upf4` (no
  `wb-fibre-01`). As sondas não separam esses três.

#### O Prometheus: a CPU do nó dos UEs

Para as mesmas janelas, cruzando a CPU dos containers e dos nós:

| Janelas da VR (servidor no `molejo`) | Boas | Ruins |
| --- | --- | --- |
| CPU do player do gold (núcleos) | 1,1 | 3,8 |
| Espera do escalonador no `molejo` (s/s) | 0,14 | 2,16 |
| CPU da `upf4` (núcleos) | 0,16 | 0,17 |

A "espera do escalonador" (`node_schedstat_waiting_seconds_total`) é quantos segundos, a cada segundo, os processos do nó
ficam na fila esperando um núcleo. A `upf4` quase não tem throttling (0,3% dos períodos de CPU, em média; máximo de 4,4%). A `upf4` e o `wb-fibre-01`
ficam iguais nas janelas boas e ruins. O Chrome do gold a 15 Mbps gasta ~4 dos 12 núcleos do `molejo`, e o UE e o gNB
da VR (0,17 núcleo cada) precisam de CPU na hora para repassar cada pacote.

Comparando as 45 execuções `hvr_l2` no trecho final (791 a 901 s, com o gold a 15 Mbps em quase todas):

| Nó dos UEs | CPU do player do gold | Espera do escalonador |
| --- | --- | --- |
| `blacksabbath` (Fase 8, 30 execuções) | 3,4 núcleos | 1,1 s/s (0,9 a 1,6) |
| `molejo` (15 execuções: render, controle e segundo controle) | 4,1 núcleos | 2,5 s/s (2,1 a 3,3) |

- **A disputa de CPU já existia na Fase 8**, mas pela metade: a VR ficava logo abaixo do limite (10 das 30 execuções
  passaram do limite depois dos 671 s, 4,4% das janelas).
- **O `molejo` espera o dobro com a mesma carga.** O motivo não está no hardware: mesmo kernel (6.8.0-136), 12 núcleos,
  mesma frequência máxima (2,4 GHz). O `molejo` ficou com o governador `schedutil`, e a frequência média dele sobe com a
  carga (1,4 a 2,2 GHz nas medidas); a do `blacksabbath` foi de 1,3 a 2,2 GHz. O que muda está na seção seguinte.
- **A única execução do `molejo` fora da faixa** é a `hvrl2m_05` (0,55 s/s), em que o gold ficou em ~9,5 Mbps.
- **O gold no alto não decide sozinho se a VR cruza o limite.** Nas execuções 02 e 04 o gold ficou a 14–15 Mbps e a
  VR terminou em 98 e 100%, mas o ping pelo túnel também subiu (de 1,8 para 6,8 ms): a VR só ficou abaixo dos 4,87 ms. Por
  isso o resultado parece intermitente.

#### O `blacksabbath` e o `molejo`: o que muda (conferência de 17/11/2026)

No trecho final (851 a 901 s, com o gold no alto), mediana das execuções e, entre parênteses, a faixa:

| | `blacksabbath` (Fase 8, 30) | `molejo` (Fase 9 e controles, 15) |
| --- | --- | --- |
| Núcleos, memória, placa de rede, kernel | 6 físicos (12 com hyperthreading), 31 GB, `eno1` a 1 Gbit/s, 6.8.0-136 | iguais |
| Frequência real (média dos núcleos) | 2,2 GHz | 2,2 GHz |
| Temperatura máxima e throttling por calor | 69 °C, nenhum | 52 °C, nenhum |
| Pacotes tratados pelo kernel | 21,6 mil/s | 19,9 mil/s |
| Parte deles no núcleo que trata mais | 54% | 54% |
| CPU do UE e do gNB do gold (UERANSIM) | 0,26 e 0,27 núcleo | 0,27 e 0,29 núcleo |
| Softnet squeezed | 2,4/s (1,5 a 3,6) | 12,6/s (2,4 a 17,7) |
| Espera do núcleo que mais espera | 0,16 s/s (0,12 a 0,22) | 0,38 s/s (0,08 a 0,53) |

O softnet squeezed conta as vezes em que o kernel parou de tratar os pacotes que chegaram porque o tempo do lote
acabou, com pacotes ainda na fila. Os pacotes da VR passam por essa fila e pelo UE e o gNB do UERANSIM.

- **O hardware é igual, e roda igual.** Mesma frequência, nenhum throttling (o `molejo` é até mais frio), a rede dividida
  entre os núcleos do mesmo jeito. Não é CPU mais lenta nem placa de rede diferente.
- **Com a mesma carga, o kernel do `molejo` sofre mais:** ~5 vezes mais squeezed, e o núcleo que mais espera espera mais
  que o dobro. As faixas quase não se cruzam (o mínimo do `molejo` é a `hvrl2m_05`, com o gold em ~9,5 Mbps).
- **O software não é igual:**

  | | `blacksabbath` (Fase 8) | `molejo` (Fase 9) |
  | --- | --- | --- |
  | UERANSIM (`free5gc/ueransim:latest`) | imagem de 19/10/2026 (`8215d043…`) | imagem de 08/11/2026 (`da47add1…`) |
  | kubelet / containerd / Ubuntu | 1.34.10 / 2.3.3 / 24.04.4 | 1.34.13 / 2.4.2 / 24.04.5 |

  O UERANSIM mudou porque os manifests pediam a tag `latest` com `imagePullPolicy: IfNotPresent`: cada nó fica com a
  versão do dia em que a baixou pela primeira vez, e o `molejo` baixou a dele quando os UEs foram para lá (09/11).
  Todas as 30 execuções da Fase 8 usaram a versão antiga, e todas as 16 do `molejo`, a nova. A versão nova não gasta
  mais CPU (UE e gNB do gold na tabela acima).
- **O player do gold gastar mais não explica sozinho.** Na `hvrl2c_01` (ambiente novo, 16/11), ele gastou 3,2 núcleos,
  menos que na Fase 8 (3,4), e a VR caiu do mesmo jeito no trecho final (40% no SLA). O squeezed ficou em 7,8/s.
- **O que não dá para separar:** o nó e a versão do UERANSIM mudaram juntos. A diferença pode ser a versão do UERANSIM,
  as do kubelet, containerd ou Ubuntu, ou uma configuração do nó que o Prometheus não mostra (BIOS, ajustes da placa de
  rede).

#### O que se conclui, e o que falta

- **Conclusão provável:** o player do gold, a 15 Mbps, ocupa CPU do `molejo`; o UE e o gNB da VR esperam na fila do
  escalonador; o atraso aparece no túnel 5G; a VR passa do limite do `net`, às vezes.
- **O leilão não enxerga isso:** ele controla a CPU e a banda das UPFs. Por isso o agente da VR, em 37 de 38 lances
  com o `net` acima do limite, diagnosticava `fora_das_cotas`: a `upf4` tinha CPU e banda de sobra.
- **O `molejo` agrava a disputa:** com a mesma carga, o kernel dele trata pior os pacotes e os processos esperam mais.
  O motivo está no software (UERANSIM, kubelet, containerd ou Ubuntu) ou na configuração do nó, não no hardware.
- **Os testes que provariam**, cada um com 3 × `hvr_l2` no `molejo`, contra 3 sem mudança (~2 h 30 min no total):
  - **prioridade de CPU ao `ue-vr-07`**, para o mecanismo. Hoje os pods dos UEs não pedem CPU: são BestEffort, a classe
    de menor prioridade do nó, e a VR disputa na mesma fila que o player do gold;
  - **o UERANSIM da Fase 8 no `molejo`**, para saber se a diferença entre os nós é a versão. O digest antigo ainda baixa
    do Docker Hub (17/11).

  Não foram feitos: o Pedro decidiu parar na correlação (16/11/2026) e não rodar mais experimentos por ora (17/11).
- Os dados do Prometheus da Fase 8 ficam disponíveis por 30 dias (até ~02/12/2026). Os CSVs desta análise estão em
  `metricas_scripts/resultados/fase9m_prom/`, fora do git.

#### Como descrever no texto

Um parágrafo para a dissertação, com o que os dados sustentam:

> Na Fase 9, o cenário H-VR em L2 ficou abaixo da Fase 8 (de 97,9% para cerca de 80% das janelas com a VR no SLA). O
> mesmo aconteceu com o servidor de VR sintético da Fase 8, então a queda não vem do render. Ela é uma limitação do
> testbed: os UEs e os gNBs são simulados pelo UERANSIM no mesmo nó que os players de vídeo, e quando o player da fatia
> gold toca a 15 Mbps, ele disputa CPU com o UE e o gNB da VR. O atraso aparece no túnel 5G simulado, com a CPU e a
> banda da UPF da VR folgadas, e por isso o leilão, que reparte esses dois recursos, não tem como corrigi-lo. Numa rede real, cada UE é um
> aparelho, e essa disputa não existe. Entre as duas fases, o nó dos UEs mudou e, com ele, a versão do UERANSIM; o
> efeito é maior no nó novo, mas os dados não separam as duas mudanças.

## Limites

- **5 execuções por combinação, contra 30 da Fase 8.** Os ICs da Fase 9 são largos (±15 pontos no SLA da `hvr_l2`), e
  só diferenças grandes aparecem. "Na faixa" é um critério frouxo: a faixa das 30 vai do pior ao melhor caso.
- **O controle cobre só a `hvr_l2`.** Na `hvr_l0` e na `scvr_l2` não houve diferença a explicar.
- **O primeiro controle não separa as duas mudanças da topologia** (servidor no `metallica` × UEs no `molejo`); o segundo
  controle separa (seção "A causa").
- **A causa não está provada.** Nenhum experimento mexeu na CPU do nó dos UEs. Só dá para afirmar que a espera do
  escalonador no `molejo` sobe junto com o `net` da VR, e que o `molejo` espera o dobro do `blacksabbath` com a
  mesma carga. O hardware dos dois é igual; o software não (a versão do UERANSIM e as do kubelet, containerd e
  Ubuntu), e o nó e a versão do UERANSIM mudaram juntos. Foi medido em execuções de ~15 min, não em microssegundos.
- **O segundo controle tem 5 execuções.** A diferença para o primeiro (2 de 5 abaixo da faixa contra 10 de 10) não é
  estatisticamente separável (p = 0,17).
- **Um render, uma placa e um passeio:** imagem `fase9.2`, a A2 do `metallica`, o passeio pelo museu. Outra placa muda
  o `hold` e o `m2p`: o render leva ~33 ms na A2 e ~23 ms na RTX 4000 Ada do `vanhalen`. A rede não muda, porque o
  tráfego é CBR.
- **O `m2p` de ~59 ms está longe dos ~20 ms que se costumam pedir para VR.** O SLA deste trabalho é o da rede (`net`,
  perda, continuidade, vazão) e não usa o `m2p`. Um SLA de ponta a ponta reprovaria este render em qualquer janela.
- **Do render, aceitos:**
  - **~0,45–0,58% de quadros cortados** (decisão do Pedro, 12/11): o quadro perde o final, mas os 14 pacotes saem
    iguais.
  - **~0,35% de quadros sem a pose da sessão**, nas trocas de sessão do exporter a cada 12 s.
  - Atrasados e reancoragens: zero.

## O que isso muda no plano

- **O gerador sintético fica validado** como modelo do servidor de VR para a rede e o leilão. Os resultados das 150
  execuções da Fase 8 sobre o leilão continuam valendo com um servidor que renderiza de verdade. O que o sintético
  não representa é o `m2p`.
- **A Fase 8 deixa de ser referência para a `hvr_l2` neste testbed.** A decisão de 09/11 (mudar os UEs para o `molejo`
  sem remedir) partiu de os dois nós terem a mesma especificação. O hardware é mesmo igual, mas o software não (o
  UERANSIM mudou de versão junto com o nó), e a `hvr_l2` mudou: o `molejo` espera mais CPU que o `blacksabbath` com a
  mesma carga, e isso aparece no `net` da VR. Comparações novas precisam de uma referência sintética medida na mesma
  topologia, como os dois controles.
- **A versão do UERANSIM ficou fixa** (17/11/2026), a da Fase 9, para não mudar de novo sem aviso
  (`docs/ambiente-continuo.md`).
- **O testbed tem uma limitação própria:** os UEs simulados e os players de vídeo dividem o nó, e com o gold a 15 Mbps
  o player disputa CPU com o UE e o gNB da VR. É uma propriedade do testbed, não do leilão nem do render. Se a VR
  precisar de um SLA mais estável, o caminho é reservar CPU ao `ue-vr-07` ou separar os nós (não feito).
- **Fica em aberto, por decisão do Pedro (16 e 17/11/2026):** a investigação parou na conferência dos dois nós, sem
  os testes que provam (descritos acima). Sem experimentos novos por ora.
