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

## Ambiente

- 12 e 13/11/2026 (validação) e 16/11/2026 (controle), namespace `pfsevaldt`.
- Servidor de VR (`vr-app-01`) sempre no `metallica`, com o `photon-server` (PhotonPath, 300m) e o `unity-render`
  (imagem `pedroevaldt/vr-render:fase9.2`, 4 núcleos, 4 GiB e 1 GPU) no mesmo pod. O `metallica` é um Dell R630 com
  uma NVIDIA A2 e um Xeon E5-2640 v4 (40 núcleos). Decisão do Pedro (12/11/2026): o nó é fixo, porque em outra placa
  o tempo de render e o `m2p` mudam.
- UEs, gNBs e o `dash-server` no `molejo` desde 09/11. Na Fase 8, ficavam no `blacksabbath`, junto com o servidor de VR.
  A `upf4` no `wb-fibre-01`, como na Fase 8.
- O cliente de VR (`ue-vr-07`) manda a pose do passeio gravado pelo museu (`vr/passeio.csv`, 2 min 16 s, em laço). O
  `unity-render` desenha cada quadro nessa pose e o codifica; o `photon-server` manda o quadro codificado no lugar do
  payload sintético, no mesmo perfil de pacotes (60 quadros/s × 14 pacotes de 1200 B).
- Testbed montado com `scripts/build_testbeds.sh --render-real`.

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

## Limites

- **5 execuções por combinação, contra 30 da Fase 8.** Os ICs da Fase 9 são largos (±15 pontos no SLA da `hvr_l2`), e
  só diferenças grandes aparecem. "Na faixa" é um critério frouxo: a faixa das 30 vai do pior ao melhor caso.
- **O controle cobre só a `hvr_l2`.** Na `hvr_l0` e na `scvr_l2` não houve diferença a explicar.
- **O controle não separa as duas mudanças da topologia** (servidor no `metallica` × UEs no `molejo`). Fica a hipótese,
  sem prova, de que o tráfego do gold no alto e o da VR se atrapalham em algum ponto do caminho, porque o `net` sobe
  com o gold e a CPU não explica. Separar as duas pede um segundo controle (abaixo).
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
  sem remedir) partiu de os dois nós terem a mesma especificação, mas a `hvr_l2` mudou. Comparações novas precisam de
  uma referência sintética medida na mesma topologia, como o controle.
- **Fica em aberto, por decisão do Pedro (16/11/2026):** a fase fecha sem o segundo controle, porque ele não muda a
  resposta da Fase 9. O segundo controle passa a valer quando um experimento novo for comparado com a Fase 8 neste
  testbed. Seria o servidor sintético no `molejo` (o `vr-server.yaml` como está), 5 × `hvr_l2`, ~1 h 30 min: é a Fase 8
  com só o nó dos UEs trocado, e separa as duas mudanças:
  - **Se a queda aparecer**, a causa é o `molejo` (ou o caminho até ele), e qualquer execução nova da Fase 8 neste
    testbed já sai diferente.
  - **Se não aparecer**, a causa é o servidor longe dos UEs.

  O `scripts/trocar_servidor_vr.sh` só conhece o `metallica` e teria de aceitar o nó como argumento.
