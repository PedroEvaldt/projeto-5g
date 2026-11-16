# Passagem: o modelo 3D renderizado no servidor de VR (estado em 16/11/2026)

Documento de passagem para quem continuar este trabalho. Resume o que foi decidido, o que foi medido, o que
existe no código e o que falta, em ordem. O plano completo da fase está em `docs/plano-render-real.md`
(escrito para GPU no cluster, que voltou a ser o caminho em 09/11). O passo a passo do
teste no PC está em `unity/README.md`.

**Como o Pedro quer trabalhar:** confirmar com ele antes de escrever ou mudar qualquer código; explicar
de forma didática; respostas curtas. Ele usa Omarchy (Arch Linux + Hyprland), tem uma RTX 4060 Ti e 12
threads no PC, e não conhecia Unity nem Blender antes deste trabalho.

## Onde está o código

- Branch: `vr-photonpath` (o trabalho entrou pelo merge `3296e9d6`, de 05/11/2026).
- Pastas novas: `unity/` (scripts C#, Dockerfile, entrypoint, README) e, em `vr/`, `gerar_passeio.py`,
  `testar_render.py` e `render_falso.py`.
- Mudanças fora dessas pastas (09/11/2026), todas desligadas por padrão: `photonpath/src/` (listadas no
  `ORIGEM.txt`), `client_scripts/vr-server-render.yaml` e `vrclients-script.sh`, `scripts/build_testbeds.sh` e
  `delete_testbeds.sh`, `metricas_scripts/fetch_prometheus.py` e `conferir_execucao.py`. Em 12/11, a chave
  `vr.calibracao` (lida em `spade_agents/` e nos scripts de análise, `cb271e00`), a fila da Fase 9 e YAMLs novos em
  `tests_configs/`; os da Fase 8 não mudaram. A Fase 8 (30 execuções × 5 combinações) terminou em 05/11/2026, com o
  PhotonPath sintético.

## Decisões tomadas, na ordem

| Data | Decisão | Por quê |
| --- | --- | --- |
| 03/11 | O PhotonPath continua sendo o servidor que fala com a rede; o Unity só renderiza e codifica, como *sidecar* no mesmo pod, e devolve os bytes do quadro | O PhotonPath já tem espaçamento de pacotes, `hold_us` por pacote, timeout de sessão e `/metrics`; reescrever isso em C# é o maior risco e não dá nada em troca |
| 03/11 | Taxa constante (CBR): 14 pacotes × 1200 B a 60 fps, como hoje; o Unity fica livre só na qualidade | É o que o streaming de VR real faz; mantém a `upf4` vendo o mesmo tráfego da Fase 8, então SLA, pisos e agente continuam valendo. VBR foi discutido e descartado: o leilão (rodada de 30 s) não segue uma demanda que muda a cada 2 s |
| 03/11 | O usuário simulado é uma trajetória gravada, tocada pelo `photon_client` nos 100 B de padding da pose | O cliente não exibe nada e o movimento de cabeça não depende da rede; nada muda no protocolo nem na imagem do UE |
| 03/11 | Exporter, agente SPADE, leilão, `deploy_test.py` e a fila não mudam | Tudo acima do exporter só enxerga as métricas `vr_*`, USR1/USR2 e `/healthz` |
| 03/11 | Fase própria (Fase 9), depois da Fase 8, com um subconjunto de execuções | A fila exige o mesmo commit nas 150 execuções; comparar render real com sintético é um resultado por si |
| 03/11 | Mudanças em `photonpath/` em commits próprios, listadas no `photonpath/ORIGEM.txt` | Regra do Pedro de 30/10 |
| 04/11 | Sem `-batchmode` no player; o `ControleVR` desliga a câmera e chama `cam.Render()` a cada frame | No player, `-batchmode` pula o render de todas as câmeras: `lidos=0` com as poses chegando |
| 04/11 | `VR_READBACK=sync`, `VR_BITRATE_KBPS=6500`, `-delay 0` no NVENC, leitor que emite cada quadro na chegada | Latência pose → datagrama de 218 ms para 22 ms; `estourados=0` |
| 04/11 | No container: Xvfb iniciado direto (não `xvfb-run`) + VirtualGL com EGL + `--gpus all` | O `xvfb-run` travava sem imprimir nada; a sequência à mão funcionou de primeira e o Unity reporta a RTX 4060 Ti como Renderer |
| 05/11 | Manter o Unity ao vivo no cluster, sem GPU, sem `nodeSelector`, deixando o Kubernetes escolher o nó com mais recursos (decisão do Pedro). **Superada em 09/11** | Preferência dele por ter o render acontecendo de verdade na execução. Partiu da informação errada de que nenhum nó tinha GPU |
| 09/11 | UEs, gNBs, `dash-server` e `vr-app-01` saem do `blacksabbath`, que ficou NotReady, para o `molejo`, sem remedir nada (decisão do Pedro) | Os dois nós têm a mesma especificação; a Fase 8 continua como referência |
| **09/11** | **Unity ao vivo com GPU, pedindo `nvidia.com/gpu: 1`, sem plano B. Se der errado, o Pedro vê o que fazer** (decisão do Pedro) | No cluster, 60 fps com GPU e 7 a 7,8 fps só com CPU (medidas abaixo). O render pré-computado saiu do plano |
| 09/11 | A conferência do render real tolera até 0,1% de quadros atrasados no servidor e descarta com qualquer reancoragem do envio (decisão do Pedro) | No segundo ensaio foram 0,016%, sem reancoragem; com a regra antiga (zero), quase toda execução de ~20 min seria descartada. **Corrigido em 12/11:** esses atrasos vinham das minhas leituras (ver "Os quadros atrasados eram das leituras"); a regra ficou, porque não atrapalha |
| **12/11** | **O `vr-app-01` fica sempre no `metallica` (NVIDIA A2), com `nodeSelector`** (decisão do Pedro) | Nos ensaios, a rede ficou no SLA em todas as janelas e o Unity a 60 fps. O preço é o `m2p` ~10 ms maior que no `vanhalen`, e o SLA não olha o `m2p`. Sem o `nodeSelector`, cada build podia cair numa placa diferente |
| 12/11 | Uso da GPU combinado com o administrador do cluster (o Pedro falou com ele) | A placa fica presa ao pod enquanto ele existir, e a fila da Fase 9 leva horas |
| 12/11 | Quadros cortados de ~0,5% aceitos (decisão do Pedro) | O corte não muda o tráfego (os 14 pacotes saem iguais); o quadro perde só o final |
| 12/11 | A Fase 9 tem calibração própria (`vr/calibracao_render.yaml`), escolhida pela chave `vr.calibracao` do YAML | O servidor mudou de nó, e o `net_ref` muda com o caminho; o `vr/calibracao.yaml` da Fase 8 fica intacto |
| 16/11 | Controle: 5 × `hvr_l2` com o servidor sintético da Fase 8 no `metallica` (o Pedro aprovou) | A `hvr_l2` da Fase 9 saiu da faixa da Fase 8, e só o controle diz se foi o render ou a topologia |
| **16/11** | **Fase fechada sem o segundo controle (sintético no `molejo`) e GPU liberada** (decisão do Pedro) | O controle já respondeu a pergunta da fase; saber se a culpa é do `metallica` ou do `molejo` só importa quando um experimento novo for comparado com a Fase 8 |

## O que foi medido (04/11/2026, PC do Pedro: RTX 4060 Ti, 12 threads, Unity 2022.3.62f3, Linux)

**O modelo.** `museu-inf-modelo`, um museu, do `.skp` por STL (SketchUp Free → Blender; sem textura). O
`.pln` do ArchiCAD não foi usado (ArchiCAD não roda em Linux). O importador de SketchUp do Blender não
funciona em Linux (só binários Windows).

| Passo | Tris no Unity | fps no Editor | Observação |
| --- | --- | --- | --- |
| STL cru | 115 M (com sombras) / 70,9 M (sem) | 27 → 43 | `Batches` 38: o problema era triângulos, não chamadas de desenho |
| Luzes e câmeras do Blender importadas por engano | 328 M | 8 | 3 luzes pontuais com sombra; `Import Lights`/`Import Cameras` desmarcados no FBX |
| *Decimate Planar* 5° no Blender, sombras off | **3,8 M** | **60** | Blender reporta 3,5 M; 3 batches; `Shadow casters: 0` |

**O pipeline (player Linux, modo rede, 1280×720, NVENC CBR).**

| Configuração | fps | idade do quadro (p95) | enc kB | estourados |
| --- | --- | --- | --- | --- |
| assíncrono, NVENC padrão, 7000 kbps | 60 | 220 ms | 14,6 | 1 em 300 |
| assíncrono, `-delay 0` | 60 | 73 ms | 14,6 | 1 em 300 |
| `VR_READBACK=sync`, 6500 kbps | 60 | 42 ms | 13,5 | 0 |
| idem, leitor emitindo na chegada | 60 | **22,6 ms** | 13,5 | 0, `fragmentos=0` |

**No container** (`docker run --gpus all --network host`, Xvfb + VirtualGL EGL): Unity reporta
`Renderer: NVIDIA GeForce RTX 4060 Ti`, 60 fps, mesmos 22 ms; 10 min em modo rede sem descarte.

**Sem GPU** (mesmo container, `MODO_TELA=cpu`, llvmpipe no Mesa + `libx264`, 12 threads): **3,4 a 4,6
fps, 220 a 290 ms por quadro**, pipeline de 450 a 600 ms. Quinze vezes mais lento do que o necessário.

## O que foi medido no cluster (09/11/2026)

**O cluster:** desde 09/11, o `molejo` (mesma especificação do `blacksabbath`: 12 núcleos alocáveis, 32 GB,
sem GPU) hospeda os UEs, os gNBs, o `dash-server` e o `vr-app-01`. As 10 UPFs continuam no `wb-fibre-01`. O
`vanhalen` (Threadripper 7980X, 128 threads, ~251 GiB, sem taints) tem 3 RTX 4000 Ada de 20 GB, expostas como
`nvidia.com/gpu: 3`, cada uma inteira para um pod. O usuário `pfsevaldt` não vê os pods desse nó, então só dá
para saber se há GPU livre pedindo uma.

**O teste** (imagem pública `pedroevaldt/vr-render:fase9`, `VR_MODO=autonomo`, 1280×720; nos dois casos o
cluster escolheu o nó):

| Configuração | fps | `frame_ms` p95 | `render_ms` p95 | Consumo | Duração |
| --- | --- | --- | --- | --- | --- |
| Só CPU (`MODO_TELA=cpu`, x264, sem limites), no `vanhalen` | 7,0 a 7,8 | 134 a 196 ms | 265 a 297 ms | 7,4 de 128 núcleos | ~20 s |
| 1 GPU (`egl`, NVENC, `VR_READBACK=sync`, 6500 kbps) | 60,0 em 181 de 182 janelas | 16,67 ms | 22 a 23,4 ms | GPU 10%, codificador 2%, 525 MiB | 15 min |

Com GPU, foram 8 quadros descartados, todos na primeira janela (a partida, com 53,7 fps), em 53.969 quadros;
`cortados`, `fragmentos` e `erro_readback` ficaram em 0, e `kB_medio` em 13,5. O `render_ms` cobre render,
leitura e codificação, e dá o mesmo valor do PC; o tempo de GPU isolado, do critério da etapa 2 do plano
(≤ 4 ms), não é medido, mas os 10% de uso da placa sugerem ~2 ms por quadro. No modo autônomo não há poses de
verdade (`poses=0`) nem PhotonPath.

**O pod que funcionou:** `runtimeClassName: nvidia`, `resources.limits: nvidia.com/gpu: 1`, `restartPolicy:
Never`, sem `nodeSelector` (o pedido de GPU já limita aos nós com placa) e as variáveis `MODO_TELA=egl`,
`VR_ENCODER=nvenc`, `VR_READBACK=sync`, `VR_BITRATE_KBPS=6500` e `VR_MODO=autonomo`.

## A cadeia completa no PC (09/11/2026)

`photon_client` → `photon_server` → Unity (RTX 4060 Ti, `VR_MODO=rede`) → `photon_server` → `photon_client`, em
loopback, 30 s, com 8 Mbps a 60 fps. Referência: o sintético dá `hold` p95 de ~11 ms nas mesmas condições.

| Medida | 6500 kbps | 6000 kbps |
| --- | --- | --- |
| Quadros / perda / continuidade | 1799 / 0,05% / 99,94% | 1799 / 0,02% / 99,89% |
| `hold` p50 / p95 | 33 / 49 ms | 46 / 48 ms |
| `net` p95 | 0,02 ms | 0,02 ms |
| `render_us` mediana | 22,2 ms | 22,3 ms |
| `frames_late_total` | 0 | 0 |
| Quadros cortados (acima de 16.128 B) | 22 de 1800 | 14 de 1800 |

O `hold` soma o render e a codificação (~22 ms), a espera pelo próximo envio do PhotonPath (0 a 16,7 ms) e o
espalhamento dos pacotes (~8 ms). A espera muda de uma execução para outra, provavelmente porque os relógios de 60 Hz
do Unity e do PhotonPath não são sincronizados. Baixar a taxa quase não reduziu os cortes, então a taxa ficou em
6500 kbps. Com o `render_falso.py`, a perda foi 0%: a perda pequena com o Unity real precisa ser olhada no ensaio.

## O primeiro ensaio no cluster (09/11/2026)

Imagem `fase9`, `build_testbeds.sh --render-real`, servidor no `vanhalen`, UEs no `molejo`. **Validou a rede, mas
não o render.** O `ReceptorPoses` só aceitava pose com `seq` maior ou igual ao maior já visto, e o `vr_exporter`
recria o cliente a cada 12 s, com o `seq` de volta a 1. O Unity descartava as poses novas e, depois de 2 s, tocava
o passeio sozinho (274 de 324 medições com `fonte=passeio`). O quadro do passeio não traz `recv_ns`, então o
servidor usava os carimbos do sintético: `hold_p95` de ~11 ms, menos que os ~22 ms do render, com `render_source=1`.
O teste no PC não pegou isso porque era uma sessão única de 30 s, sem o exporter.

| Medida (47 janelas, 3 vídeos tocando, `upf4` em 400m) | Resultado | Vale? |
| --- | --- | --- |
| `net_p95` | 2,58 a 4,61 ms (SLA ≤ 4,87, calibrado sem o salto até o `vanhalen`) | Sim |
| Perda / continuidade / vazão | 0 a 0,27% / 99,66 a 99,91% / 8,03 a 8,06 Mbps | Sim |
| Throttling da `upf4` em 400m | 2 de 1.466 períodos | Sim |
| Unity | 60 fps, `render_ms` p95 ~23 ms, throttling 0 | Sim |
| `hold_p95` ~11 ms e `m2p_p95` ~13 ms | Carimbos do caminho sintético | **Não** |
| Quadros cortados | ~0,18%, com o passeio interno | Não para o render seguindo o cliente |
| `frames_late_total` | 5, 13 e 25 em três leituras; o throttling do `photon-server` quase não subiu | Medir de novo |

Com a `upf4` em 150m (o valor em que o build a deixa), o `net_p95` ficou entre 17 e 149 ms, fora do SLA em todas
as janelas.

**O conserto (imagem `fase9.1`, testada no PC com o cliente recriado a cada 12 s, 6 janelas).** Uma sessão nova
agora recomeça a numeração no `ReceptorPoses`, e o servidor conta os quadros enviados sem a pose da sessão
(`frames_pose_fallback_total`). Resultado: 0,14 a 0,28% de quadros sem pose (100% com a imagem antiga, da 2ª janela em
diante), `hold_p95` de 35 a 39 ms, perda de 0 a 0,13% e `frames_late_total` 0. Os quadros cortados subiram para 117 de 4.320
(2,7%): a causa não foi investigada, e o número precisa ser olhado no cluster.

## O segundo ensaio no cluster (09/11/2026, imagem `fase9.1`)

Mesmo testbed, `upf4` em 400m, 3 vídeos tocando, 45 janelas (~9,5 min). **Validou o render ao vivo com GPU:** o Unity
seguiu o cliente em todas as 122 medições, e a rede ficou dentro do SLA calibrado em todas as janelas.

| Medida | Resultado |
| --- | --- |
| `net_p95` | 2,44 a 3,97 ms (≤ 4,87) |
| `hold_p95` / `m2p_p95` | 36,7 a 49,5 ms / 38,6 a 51,6 ms (sintético: `m2p_p95` de ~13 ms) |
| Perda / continuidade / vazão | 0 a 0,155% / 99,57 a 99,91% / 8,04 a 8,06 Mbps |
| Quadros sem pose | 0,22% (69 de 30.926) |
| Unity | 60 fps, `render_ms` p95 ~23 ms, throttling 0 de 5.446 períodos |
| Throttling da `upf4` / do `photon-server` | 2 de 5.249 períodos / 2 de 5.378 períodos |
| `frames_late_total` / `frames_reanchored_total` | +5 em ~9 min (0,016%) / 0 |
| Quadros cortados | 893 de 30.926 (2,9%) |

O `hold` sobe e desce em ciclos de ~63 s, o que combina com os relógios de 60 Hz do Unity e do PhotonPath não serem
sincronizados. Os 4 núcleos e 4 GiB do `unity-render` e os 300m do `photon-server` bastaram nesse ensaio.

Para repetir, depois de `delete_testbeds.sh` e `build_testbeds.sh --render-real` (o build deixa a `upf4` em 150m):

```bash
POD=$(kubectl get pod -n pfsevaldt -l app=upf4 -o jsonpath='{.items[0].metadata.name}')   # acha o pod da upf4
kubectl patch pod $POD -n pfsevaldt --subresource resize --type json -p '[{"op":"replace","path":"/spec/containers/0/resources/requests/cpu","value":"400m"},{"op":"replace","path":"/spec/containers/0/resources/limits/cpu","value":"400m"}]'   # sobe a CPU da upf4 para 400m, mexendo só nesses dois campos
kubectl exec -n pfsevaldt $POD -c upf4 -- sh -c 'C=/sys/fs/cgroup$(cut -d: -f3 /proc/self/cgroup); cat $C/cpu.max; grep -E "nr_periods|nr_throttled" $C/cpu.stat'   # mostra a cota aplicada (40000 100000 = 400m) e o throttling
```

## O passeio gravado (10/11/2026, imagem `fase9.2`)

O Pedro gravou um passeio pelo museu no Editor, com o `GravadorPasseio`, e ele substituiu o circular: 2 min 16 s
(6.795 amostras a 50 Hz), 197 de caminho numa área de 38 × 57, altura fixa de 1,58, 4,9 s parado (nenhuma parada
de 2 s). O laço fecha a 0,07 do início (o passo entre amostras é de ~0,03), mas com 16° de diferença na direção: a
cada volta, a câmera gira 16° de uma vez, ~6 vezes numa execução de 15 min. Ficou assim, sem regravar.

O `vr/passeio.csv` não vai pelo git (o `.gitignore` ignora `*.csv`): copiar à mão para a ledzeppelin. O mesmo
arquivo está em `Assets/StreamingAssets/passeio.csv` no projeto Unity e dentro do build. Com 477 kB, cabe no
ConfigMap (limite de 1 MiB, ~5 min de passeio a 50 Hz).

Teste no PC com o cliente recriado a cada 12 s (12 janelas, uma volta inteira, `VR_READBACK=sync`, 6500 kbps):

| Medida | Passeio gravado | Circular (09/11) |
| --- | --- | --- |
| Quadros sem pose | 0,28% (24 de 8.640) | 0,14 a 0,28% |
| `frames_late_total` | 0 | 0 |
| Quadros cortados | **0,42%** (36 de 8.640) | 2,7% (117 de 4.320) |
| `hold_p95` / `m2p_p95` | 34 a 50 ms | 35 a 39 ms |
| Perda / vazão | 0 a 0,107% / 8,04 a 8,06 Mbps | 0 a 0,13% / igual |
| Unity | 60 fps, `render_ms` p95 22,6 ms, 13,5 kB, 3 descartados em ~9.000 | igual |

Render, codificador e taxa são os mesmos: a queda dos cortados veio do conteúdo das imagens. O que no circular
gerava quadros maiores não foi investigado. A vazão não muda com o passeio (CBR).

## O terceiro ensaio e os nós com A2 (10 e 11/11/2026, imagem `fase9.2`)

Mesmo testbed dos ensaios anteriores (`upf4` em 400m, 3 vídeos tocando, passeio gravado), com o `vr-app-01` fixado
em cada nó por `nodeSelector`. Além do `vanhalen`, dois nós têm uma NVIDIA A2 (16 GB, driver 580.105, GPU operator,
`gpu.mode=compute`): o `whitesnake` (Dell R740, Xeon Silver 4114, 20 núcleos) e o `metallica` (Dell R630, Xeon
E5-2640 v4, 40 núcleos, PCIe gen3 x8). EGL e NVENC funcionam na A2. Só o Unity (modo autônomo, ~10 min): 60 fps nos
dois, `render_ms` p95 de ~36 ms no `whitesnake` e ~33 ms no `metallica`, com 39–40% da placa.

| Medida | `vanhalen` (RTX 4000 Ada) | `whitesnake` (A2) | `metallica` (A2) |
| --- | --- | --- | --- |
| Janelas analisadas | 52 | 46 | 46 |
| `net_p95` | 2,15 a 4,97 ms (1 acima de 4,87) | 2,13 a 4,91 ms (1 acima) | 2,12 a 4,32 ms (nenhuma acima) |
| `m2p_p95` | 36,5 a 52,0 ms | 51,4 a 65,6 ms (mediana 60,9) | 48,8 a 62,3 ms (mediana 55,8) |
| `render_ms` p95 do Unity | ~23 ms | ~37 ms | ~33,6 ms |
| Perda / continuidade | 0 a 0,155% / ≥ 99,65% | 0 a 0,155% / ≥ 99,66% | 0 a 0,155% / ≥ 99,67% |
| Quadros cortados / sem pose | 0,48% / 0,27% | 0,42% / 0,36% | 0,48% / 0,34% |

A rede é a mesma nos três: o `net` não depende da placa. O que muda é o `hold`, e com ele o `m2p`: a A2 leva ~10 ms
a mais por quadro, e o `m2p` sobe de ~45 para ~56 ms no `metallica`. O SLA da VR (e o agente por SLA) não olha o
`m2p`. Throttling no `metallica`: 2 de 5.783 períodos na `upf4` e 0 no `unity-render`. O passeio gravado levou os
cortados de 2,9% (circular, segundo ensaio) para ~0,5% no cluster.

### Os quadros atrasados eram das leituras

Nos ensaios, eu lia os contadores do servidor com `kubectl exec ... python3` dentro do container `photon-server`. O
Python que sobe para a leitura gasta CPU da cota de 300m do container, o CFS segura o container e o laço de envio
para por até 100 ms: é isso que aparecia como `frames_late_total` e `frames_reanchored_total`, e como janelas ruins na
mesma hora. Pelos horários do Prometheus, os contadores só subiram nos instantes das leituras; fora delas, ficaram em
zero nos três nós. Os 0,016% do segundo ensaio, que embasaram a tolerância de 0,1% (09/11), eram esse efeito. A regra
ficou como está, porque não atrapalha. Daqui em diante, os contadores do servidor saem do Prometheus (ServiceMonitor
do `vr-app-01`), nunca de um `exec` no `photon-server`. Na fila, nada entra nesse container: o `fetch_prometheus.py`
lê o Prometheus, e a conferência dos clientes pinga do UE.

## A calibração no metallica (etapa 6, 12/11/2026)

Calibração inteira, não só a conferência do plano, com o `vr/calibrar.py` e o `vr/graficos_calibracao.py` das Fases 3
e 4: o testbed do `build_testbeds.sh --render-real`, leilão desligado, passeio no cliente e 5 repetições de 9 janelas por
ponto. CSVs, figuras e `LEIAME.md` em `metricas_scripts/resultados/calibracao_render/` (fora do git). Resultado no
`vr/calibracao_render.yaml`:

| | Fase 8 (`calibracao.yaml`) | Fase 9 (`calibracao_render.yaml`) |
| --- | --- | --- |
| `net_ref` | 1,87 ms | 1,92 ms |
| Limite do `net_p95` | 4,87 ms | 4,92 ms |
| Banda: corte | entre 8,5 e 9 Mbps | entre 8,5 (0%) e 9 Mbps (100%) |
| CPU sem vídeo | 0,2 passa | 0,15 (0%, `net` ~31 ms) e 0,2 (100%) |
| CPU com os 3 vídeos | 0,25 a 0,45: ~91% (teto) | 0,2: 75,6%; 0,25 a 0,4: 100% |
| Pisos (CPU / banda / CPU sem vídeo) | 0,28 / 9,9 / 0,22 | 0,28 / 9,9 / 0,22 |

- O salto a mais até o `metallica` custa 0,05 ms no `net_ref`, e os pisos não mudam: a Fase 9 é comparável com a 8.
- **O teto de ~91% com vídeo sumiu.** Na Fase 4, o `vr-app-01` dividia o nó com os 3 Chromes e os UERANSIM, e o `net`
  passava do limite de vez em quando mesmo com CPU de sobra ("o resto fora das cotas", `docs/coleta-vr.md`). Com o
  servidor no `metallica`, de 0,25 núcleo para cima, as 135 janelas ficaram no SLA, com `net_p95` de ~2,3 a 2,5 ms. É
  o teste "mover o `vr-app-01` para um nó sem UEs", anotado no plano, feito de tabela. O nó dos UEs também mudou desde
  a Fase 4 (`blacksabbath` -> `molejo`), então as duas causas não se separam. Na validação, a `hvr_l2` caiu mesmo
  assim, com o gold no alto, uma situação que a calibração não cobre (sem leilão, as UPFs de vídeo ficam nos 200m).
  Ver "A validação e o controle".
- Com banda folgada, o passeio dá `m2p_p95` mediano de ~56 ms (máximo 62), igual ao do terceiro ensaio.
- Um tropeço do roteiro, não da VR: a série de CPU começou 16 s depois do fim da de banda e o `calibrar.py` recusou
  a janela da retomada (`health=0`). Basta esperar duas janelas boas entre as séries.

## A validação e o controle (etapa 7, 12, 13 e 16/11/2026)

Resultado completo em `docs/render-real.md`. Em resumo:

- **Validação (15 execuções, 15 válidas):** a `hvr_l0` e a `scvr_l2` ficaram dentro da variação da Fase 8. O `m2p`
  subiu ~46 ms nas três (o render está todo no `hold`).
- **A `hvr_l2` caiu de 97,9% para 79,6%** das janelas no SLA (p = 0,03), sempre pelo `net`, quase só depois dos
  671 s. Nesse trecho os três vídeos tocam e o gold sobe para 14–15 Mbps.
- **Controle (16/11, 5 × `hvr_l2`, servidor sintético no `metallica`):** 77,8%, igual ao render (p = 0,82). Fora do
  `m2p`, nada difere.
- **Conclusão:** a queda é da topologia (servidor no `metallica` e/ou UEs no `molejo`), e o render não muda o que o
  leilão vê.
- **Separar as duas causas** pede um segundo controle: o sintético no `molejo`. O Pedro decidiu fechar a fase sem ele
  (16/11), porque ele não muda a resposta. Fica para quando um experimento novo for comparado com a Fase 8.

Depois do controle, o `vr-app-01` voltou ao render real e foi conferido: 60 fps com a GPU e `health=1`. Na mesma tarde
(16/11), com a fase fechada, o Deployment foi apagado para liberar a GPU do `metallica`. O Service e o ServiceMonitor
ficaram. Para subir de novo: `scripts/trocar_servidor_vr.sh render` (ou `build_testbeds.sh --render-real`).

## O que existe no código

| Arquivo | O que faz |
| --- | --- |
| `unity/Assets/Scripts/ControleVR.cs` | Vai na `Main Camera`. Lê as variáveis `VR_*`, põe a câmera na pose (rede ou passeio), chama `cam.Render()`, captura, imprime `[medidor]` a cada 5 s |
| `unity/Assets/Scripts/ReceptorPoses.cs` | UDP 9300: recebe o `PPOS` de 128 B + `recv_ns`, guarda a última pose; uma sessão nova recomeça a numeração |
| `unity/Assets/Scripts/SaidaQuadros.cs` | `RenderTexture` → `AsyncGPUReadback` (sync ou async) → `ffmpeg` filho (NVENC ou x264, CBR, `-delay 0`, sem buffer) → separa por AUD → datagrama `PPRF` em UDP 9301. Modos `jpeg` e `nenhum` para teste. PNG a cada N s |
| `unity/Assets/Scripts/Passeio.cs` | Lê o CSV do passeio e interpola em laço |
| `unity/Assets/Scripts/GravadorPasseio.cs` | Grava um passeio no Editor (WASD, mouse, tecla R); enquanto estiver na câmera, o `ControleVR` fica desligado |
| `unity/Dockerfile`, `unity/entrypoint.sh` | `nvidia/cuda` base + Xvfb + VirtualGL 3.1.1 + `ffmpeg`; modos de tela `egl` (confirmado), `direto`, `xorg`, `cpu` |
| `unity/README.md` | Passo a passo no PC, variáveis, sintomas e medidas |
| `vr/gerar_passeio.py` | Passeio circular sintético (usado até 10/11/2026: centro 0 0, raio 3, altura 1,6, 60 s) |
| `vr/testar_render.py` | Faz o papel do PhotonPath: manda poses a 250 Hz, recebe `PPRF`, mede fps, render, tamanho, idade; `--salvar` grava o H.264 |
| `vr/render_falso.py` | O Unity de mentira, para testar o medidor e, na etapa 3, o `photon_server` |
| `photonpath/src/photon_client.py` (`--pose-trace`) | Cada pose leva a amostra do passeio, escolhida pelo relógio de parede, porque o exporter reinicia o cliente a cada 12 s |
| `photonpath/src/photon_server.py` (`--render-sock`) | Repassa as poses, lê o `PPRF` e põe os bytes nos pacotes, com os carimbos da pose do quadro. Sem quadro novo por 0,5 s, volta ao sintético. Métricas `render_source`, `render_frame_age_seconds`, `frames_enc_truncated_total`, `enc_bytes_last` e `frames_pose_fallback_total` |
| `client_scripts/vr-server-render.yaml` | O pod com `photon-server` e `unity-render`, sempre no `metallica`, o Service e o ServiceMonitor |
| `scripts/build_testbeds.sh --render-real` | Sobe esse pod, põe o passeio no ConfigMap `vr-launcher` e liga o `PP_POSE_TRACE` no UE de VR |
| `metricas_scripts/fetch_prometheus.py`, `conferir_execucao.py` | Com `vr.render: real` no YAML: grava o `render_timeseries.csv` e descarta a execução se `render_source` cair a 0, mais de 0,1% dos quadros atrasarem no servidor, o envio for reancorado ou mais de 5% dos quadros saírem sem a pose da sessão |
| Chave `vr.calibracao` do YAML | O arquivo do SLA e dos pisos da VR (padrão: `vr/calibracao.yaml`). Leem a chave o leiloeiro, os agentes da fatia e o estrategista (`spade_agents/`) e, pelo `config.yaml` da execução, a análise (`metricas_scripts/calibracao_vr.py`) |
| `vr/graficos_calibracao.py --yaml --descricao` | Grava a calibração em outro arquivo (o `vr/calibracao_render.yaml` da Fase 9), com o cabeçalho dizendo qual é |
| `run_fase9.sh` e `scripts/fila_fase8.py` com `FILA_FASE=9` | A fila da Fase 9: `hvr_l0`, `hvr_l2` e `scvr_l2` com os `tests_configs/test_config_*_render.yaml`, saída em `resultados/fase9/`, estado em `resultados/fase9_fila.csv`. Confere `vr.render`, `vr.calibracao` e se o `vr-app-01` é o do render real no `metallica` |
| `scripts/trocar_servidor_vr.sh render\|sintetico` | Troca o `vr-app-01` entre o render real e o PhotonPath sintético da Fase 8, os dois no `metallica`, e recria o UE de VR. Recusa com uma execução aberta |
| `FILA_FASE=9s ./run_fase9.sh` e `tests_configs/test_config_hvr_l2_sintetico.yaml` | A fila do controle: só a `hvr_l2`, com o servidor sintético e a calibração da Fase 9, saída em `resultados/fase9s/`. A fila confere que o `vr-app-01` não tem o `unity-render` |
| `metricas_scripts/analise_fase9.py [pasta] [por_execucao.csv da referência] [saída] [rótulo] [rótulo da referência]` | Compara uma pasta de execuções com a Fase 8 ou com outra análise dele (ex.: render × controle). Roda no PC: o venv da ledzeppelin não tem `scipy` |
| `scripts/pfcp_upf.py` (`reiniciar-ue`) | Com o render real no ar, o conserto recria o UE de VR com o passeio (sem ele, o Unity não seguiria o cliente) |

**O contrato PhotonPath ↔ Unity** (em `docs/plano-render-real.md`): pose repassada = `PPOS` 128 B +
`!Q recv_ns` (porta 9300); padding da pose = `!I7f` (índice, x y z, qx qy qz qw); quadro = `!4sQQQII`
(`PPRF`, pose_seq, t_pose_ns, recv_ns, render_us, enc_bytes) + bytes (porta 9301). O `render_us` conta da
aplicação da pose até o datagrama sair.

**Projeto Unity no PC do Pedro:** `~/unity/vrmodelo` (criado por `Unity -createProject`, porque o Hub não
lia o template no Linux), cena `museu-cena-1`, `Assets/museu-inf-modelo.fbx` (decimado), scripts copiados
de `unity/Assets/Scripts/`, passeio em `Assets/StreamingAssets/passeio.csv`. Build em
`projeto-5g/unity/build/vrmodelo.x86_64`. Dependências do Arch que faltaram: `libxml2-legacy`,
`libxcrypt-compat`.

**Pegadinhas já pagas, para não repetir:** mudanças feitas com o Play ligado são descartadas ao parar;
`-batchmode` não renderiza câmeras no player; o Blender exporta luzes e câmeras junto (usar *Limit to:
Selected Objects* ou desmarcar `Import Lights`/`Import Cameras` no Unity); `xvfb-run` trava no container;
com `--network host`, containers deixados vivos (Ctrl+C num `docker run` sem `-it` não os mata) seguram o
display `:99` para todos (`docker ps -q --filter ancestor=vr-render | xargs -r docker kill`); `render_source=1` só
diz que chegam quadros do Unity, não que ele segue o cliente; `kubectl patch --type merge` em `containers` troca a
lista inteira (usar `--type json`, como o `upf_set_resources.py`); a UPF é privilegiada e o `/sys/fs/cgroup` dela é a
raiz do nó (`nr_periods 0`), então o throttling se lê no caminho de `/proc/self/cgroup`, como abaixo.

## O que falta, em ordem

1. ~~**Etapa 6**~~: feita em 12/11 (seção "A calibração no metallica").
2. ~~**Etapa 7**~~: feita em 12 e 13/11, com o controle em 16/11 (seção "A validação e o controle").
3. ~~**`docs/render-real.md`**~~: escrito em 16/11, e o `docs/plano-render-real.md` atualizado.
4. ~~**Fechar a fase**~~: fechada em 16/11, sem o segundo controle, e com a GPU liberada (decisão do Pedro). Se um dia
   o controle for preciso (sintético no `molejo`, 5 × `hvr_l2`, ~1 h 30 min), o `trocar_servidor_vr.sh` tem de aceitar
   o nó.
5. Trazer `resultados/fase9*` e as análises para o PC (`fase9_analise`, `fase9s_analise`, `fase9_vs_9s`), atualizar
   o `CLAUDE.md` e, no fim, o fast-forward da `pedro/6443-pfsevaldt` (passo do Pedro).

Para regravar o passeio: `unity/README.md`, etapa A, item 6. O CSV vai para `Assets/StreamingAssets/passeio.csv`
e `vr/passeio.csv`; depois, recompilar o player (a lista de cenas do build está vazia: o Unity compila a cena
aberta, que tem de ser a `museu-cena-1`) e publicar uma imagem com tag nova.
