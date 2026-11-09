# Passagem: o modelo 3D renderizado no servidor de VR (estado em 09/11/2026)

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
  `delete_testbeds.sh`, `metricas_scripts/fetch_prometheus.py` e `conferir_execucao.py`. Nada em `spade_agents/`
  ou `tests_configs/` foi tocado. A Fase 8 (30 execuções × 5 combinações) terminou em 05/11/2026, com o
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

## O que existe no código

| Arquivo | O que faz |
| --- | --- |
| `unity/Assets/Scripts/ControleVR.cs` | Vai na `Main Camera`. Lê as variáveis `VR_*`, põe a câmera na pose (rede ou passeio), chama `cam.Render()`, captura, imprime `[medidor]` a cada 5 s |
| `unity/Assets/Scripts/ReceptorPoses.cs` | UDP 9300: recebe o `PPOS` de 128 B + `recv_ns`, guarda a última pose; uma sessão nova recomeça a numeração |
| `unity/Assets/Scripts/SaidaQuadros.cs` | `RenderTexture` → `AsyncGPUReadback` (sync ou async) → `ffmpeg` filho (NVENC ou x264, CBR, `-delay 0`, sem buffer) → separa por AUD → datagrama `PPRF` em UDP 9301. Modos `jpeg` e `nenhum` para teste. PNG a cada N s |
| `unity/Assets/Scripts/Passeio.cs` | Lê o CSV do passeio e interpola em laço; `GravadorPasseio` grava um passeio no Editor (WASD, mouse, tecla R) |
| `unity/Dockerfile`, `unity/entrypoint.sh` | `nvidia/cuda` base + Xvfb + VirtualGL 3.1.1 + `ffmpeg`; modos de tela `egl` (confirmado), `direto`, `xorg`, `cpu` |
| `unity/README.md` | Passo a passo no PC, variáveis, sintomas e medidas |
| `vr/gerar_passeio.py` | Passeio circular sintético (o que está em uso: centro 0 0, raio 3, altura 1,6, 60 s) |
| `vr/testar_render.py` | Faz o papel do PhotonPath: manda poses a 250 Hz, recebe `PPRF`, mede fps, render, tamanho, idade; `--salvar` grava o H.264 |
| `vr/render_falso.py` | O Unity de mentira, para testar o medidor e, na etapa 3, o `photon_server` |
| `photonpath/src/photon_client.py` (`--pose-trace`) | Cada pose leva a amostra do passeio, escolhida pelo relógio de parede, porque o exporter reinicia o cliente a cada 12 s |
| `photonpath/src/photon_server.py` (`--render-sock`) | Repassa as poses, lê o `PPRF` e põe os bytes nos pacotes, com os carimbos da pose do quadro. Sem quadro novo por 0,5 s, volta ao sintético. Métricas `render_source`, `render_frame_age_seconds`, `frames_enc_truncated_total`, `enc_bytes_last` e `frames_pose_fallback_total` |
| `client_scripts/vr-server-render.yaml` | O pod com `photon-server` e `unity-render`, o Service e o ServiceMonitor |
| `scripts/build_testbeds.sh --render-real` | Sobe esse pod, põe o passeio no ConfigMap `vr-launcher` e liga o `PP_POSE_TRACE` no UE de VR |
| `metricas_scripts/fetch_prometheus.py`, `conferir_execucao.py` | Com `vr.render: real` no YAML: grava o `render_timeseries.csv` e descarta a execução se `render_source` cair a 0, `frames_late_total` subir ou mais de 5% dos quadros saírem sem a pose da sessão |

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

1. **Repetir o ensaio no cluster com a imagem `fase9.1` (etapa 5 do plano).** Na ledzeppelin, depois do `git pull`:
   `delete_testbeds.sh`, `build_testbeds.sh --render-real` e a `upf4` em 400m (comandos abaixo). Critério: 10 min com
   os 3 vídeos tocando, `health=1`, `render_source=1`, `frames_pose_fallback_total` abaixo de 5% de
   `frames_sent_total`, `frames_late_total=0`, `frames_enc_truncated_total` perto de zero e throttling 0 nos dois
   containers. Se os atrasos continuarem, decidir entre manter o zero e aceitar os descartes ou tolerar um limite
   pequeno. Os limites do pod (`unity-render` com 4 núcleos e 4 GiB, `photon-server` com 300m) são os valores
   iniciais do plano e saem desse ensaio. Atenção: o servidor fica num nó diferente do `dash-server` e dos UEs
   (`molejo`), então o salto a mais (ponto 4 da tabela do plano) volta.

   ```bash
   POD=$(kubectl get pod -n pfsevaldt -l app=upf4 -o jsonpath='{.items[0].metadata.name}')   # acha o pod da upf4
   kubectl patch pod $POD -n pfsevaldt --subresource resize --type json -p '[{"op":"replace","path":"/spec/containers/0/resources/requests/cpu","value":"400m"},{"op":"replace","path":"/spec/containers/0/resources/limits/cpu","value":"400m"}]'   # sobe a CPU da upf4 para 400m, mexendo só nesses dois campos
   kubectl exec -n pfsevaldt $POD -c upf4 -- sh -c 'C=/sys/fs/cgroup$(cut -d: -f3 /proc/self/cgroup); cat $C/cpu.max; grep -E "nr_periods|nr_throttled" $C/cpu.stat'   # mostra a cota aplicada (40000 100000 = 400m) e o throttling
   ```
2. **Gravar o passeio de verdade** pelo museu (2 a 3 min, laço fechado, ambientes variados), substituindo o
   circular. Antes, ajustar o `GravadorPasseio`: o `ControleVR` reposiciona a câmera a cada quadro e desliga a câmera
   no `Awake`, mesmo com o componente desmarcado, então os dois não funcionam juntos como o `unity/README.md` descreve. O
   CSV vai para `Assets/StreamingAssets/passeio.csv` e `vr/passeio.csv`. Depois, copiar `unity/Assets/Scripts/` para o
   projeto, recompilar o player e publicar uma imagem com tag nova. A lista de cenas do build está vazia: o Unity
   compila a cena aberta, que tem de ser a `museu-cena-1`.
3. **Combinar o uso da GPU com quem administra o cluster** antes das execuções longas: a placa fica exclusiva
   do pod enquanto ele existir.
4. **Etapas 6 e 7** como no plano: `net_ref` e pisos com o servidor no nó de GPU e as execuções de validação
   (5 × `hvr_l2`, 5 × `hvr_l0`), com YAMLs novos que tenham `vr.render: real`.
5. **Atualizar `docs/plano-render-real.md`** com as decisões de 09/11 e as medidas desta passagem (ele ainda
   cita o `blacksabbath` e um `nodeSelector` no nó de GPU).
