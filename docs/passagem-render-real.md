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
- Nada em `photonpath/`, `spade_agents/`, `client_scripts/`, `scripts/` ou `tests_configs/` foi tocado por
  este trabalho. A Fase 8 (30 execuções × 5 combinações) terminou em 05/11/2026, com o PhotonPath sintético.

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

## O que existe no código

| Arquivo | O que faz |
| --- | --- |
| `unity/Assets/Scripts/ControleVR.cs` | Vai na `Main Camera`. Lê as variáveis `VR_*`, põe a câmera na pose (rede ou passeio), chama `cam.Render()`, captura, imprime `[medidor]` a cada 5 s |
| `unity/Assets/Scripts/ReceptorPoses.cs` | UDP 9300: recebe o `PPOS` de 128 B + `recv_ns`, guarda a última pose |
| `unity/Assets/Scripts/SaidaQuadros.cs` | `RenderTexture` → `AsyncGPUReadback` (sync ou async) → `ffmpeg` filho (NVENC ou x264, CBR, `-delay 0`, sem buffer) → separa por AUD → datagrama `PPRF` em UDP 9301. Modos `jpeg` e `nenhum` para teste. PNG a cada N s |
| `unity/Assets/Scripts/Passeio.cs` | Lê o CSV do passeio e interpola em laço; `GravadorPasseio` grava um passeio no Editor (WASD, mouse, tecla R) |
| `unity/Dockerfile`, `unity/entrypoint.sh` | `nvidia/cuda` base + Xvfb + VirtualGL 3.1.1 + `ffmpeg`; modos de tela `egl` (confirmado), `direto`, `xorg`, `cpu` |
| `unity/README.md` | Passo a passo no PC, variáveis, sintomas e medidas |
| `vr/gerar_passeio.py` | Passeio circular sintético (o que está em uso: centro 0 0, raio 3, altura 1,6, 60 s) |
| `vr/testar_render.py` | Faz o papel do PhotonPath: manda poses a 250 Hz, recebe `PPRF`, mede fps, render, tamanho, idade; `--salvar` grava o H.264 |
| `vr/render_falso.py` | O Unity de mentira, para testar o medidor e, na etapa 3, o `photon_server` |

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
display `:99` para todos (`docker ps -q --filter ancestor=vr-render | xargs -r docker kill`).

## O que falta, em ordem

1. **Pequenos, já propostos e aprovados em princípio, não aplicados:** no `entrypoint.sh`, Xvfb com
   `-nolisten local` e display `:77` (evita a briga pelo socket abstrato com `--network host`); limpar o
   aviso CS0414 (`temPose` sem uso) no `ControleVR.cs`.
2. **Gravar o passeio de verdade** pelo museu com o `GravadorPasseio` (2 a 3 min, laço fechado, ambientes
   variados), substituindo o circular. Vai para `Assets/StreamingAssets/passeio.csv` e `vr/passeio.csv`. A
   imagem `fase9` tem o circular: depois disso, gerar e publicar uma imagem nova.
3. **Etapa 3 do plano, no PhotonPath:** `--render-sock` no `photon_server.py` (repassa poses, lê `PPRF`,
   payload real, métricas `render_source`, `render_frame_age_seconds`, `frames_enc_truncated_total`,
   `enc_bytes_last`; sem `PPRF`, volta ao sintético) e `--pose-trace` no `photon_client.py`. Testar em
   loopback com o `render_falso.py` e depois com o player. Dois commits listados no `ORIGEM.txt`.
4. **Etapa 5, o pod:** `client_scripts/vr-server-render.yaml` com dois containers (`photon-server` com
   `PP_RENDER_SOCK`, `unity-render` com a imagem), `runtimeClassName: nvidia`, `limits: nvidia.com/gpu: 1`
   no `unity-render`, **sem `nodeSelector`**, requests = limits de CPU e memória nos dois (medir quanto o
   Unity gasta: o teste de 09/11 rodou sem limite de CPU), imagem no Docker Hub, ServiceMonitor do
   `vr-app-01`, regra nova no `conferir_execucao.py` (`render_source` e `frames_late_total`), opção
   `--render-real` no `build_testbeds.sh`. Atenção: o servidor fica num nó diferente do `dash-server` e dos
   UEs (`molejo`), então o salto a mais (ponto 4 da tabela do plano) volta e o `net_ref` precisa ser
   remedido.
5. **Combinar o uso da GPU com quem administra o cluster** antes das execuções longas: a placa fica exclusiva
   do pod enquanto ele existir.
6. **Etapas 6 e 7** como no plano: conferir pisos e `net_ref`, e as execuções de validação (5 × `hvr_l2`,
   5 × `hvr_l0`).
7. **Atualizar `docs/plano-render-real.md`** com as decisões de 09/11 e as medidas desta passagem (ele ainda
   cita o `blacksabbath` e um `nodeSelector` no nó de GPU).
