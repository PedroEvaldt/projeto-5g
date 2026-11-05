# Passagem: o modelo 3D renderizado no servidor de VR (estado em 05/11/2026)

Documento de passagem para quem continuar este trabalho. Resume o que foi decidido, o que foi medido, o que
existe no código e o que falta, em ordem. O plano completo da fase está em `docs/plano-render-real.md`
(ainda na versão com GPU no cluster; a decisão de 05/11 abaixo muda a etapa 5 dele). O passo a passo do
teste no PC está em `unity/README.md`.

**Como o Pedro quer trabalhar:** confirmar com ele antes de escrever ou mudar qualquer código; explicar
de forma didática; respostas curtas. Ele usa Omarchy (Arch Linux + Hyprland), tem uma RTX 4060 Ti e 12
threads no PC, e não conhecia Unity nem Blender antes deste trabalho.

## Onde está o código

- Branch base do projeto: `vr-photonpath`.
- Branch deste trabalho: `claude/elegant-knuth-ut7wgc`, em cima do topo da `vr-photonpath`, 6 commits
  (plano, pacote do Unity, três correções de latência e tela, Xvfb direto). Avanço direto: entra na
  `vr-photonpath` com `git merge --ff-only`.
- Pastas novas: `unity/` (scripts C#, Dockerfile, entrypoint, README) e, em `vr/`, `gerar_passeio.py`,
  `testar_render.py` e `render_falso.py`.
- Nada em `photonpath/`, `spade_agents/`, `client_scripts/`, `scripts/` ou `tests_configs/` foi tocado.
  A Fase 8 (30 execuções × 5 combinações) continua rodando com o PhotonPath sintético, sem mudança.

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
| **05/11** | **Manter o Unity ao vivo no cluster, sem GPU, sem `nodeSelector`, deixando o Kubernetes escolher o nó com mais recursos. Se não sustentar os 60 fps, o Pedro vê uma máquina com GPU** (decisão do Pedro) | Preferência dele por ter o render acontecendo de verdade na execução. A medida de 04/11 (abaixo) diz que sem GPU o modelo rende 3 a 5 fps com 12 threads, então este caminho precisa ser provado no cluster antes de qualquer integração. O render pré-computado (seção "Plano B") fica como alternativa pronta |

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

**O cluster:** `blacksabbath` tem 12 núcleos alocáveis, 32 GB, sem GPU, e já hospeda os UEs, os gNBs,
o `dash-server` e o `vr-app-01`. O `wb-fibre-01` (as 10 UPFs) não foi medido. Nenhum nó tem
`nvidia.com/gpu`.

**O que isso diz sobre a decisão de 05/11:** com o modelo atual a 720p, nenhum nó do cluster chega a 60
fps por software. Para o Unity ao vivo no cluster ter chance, é preciso pelo menos uma destas coisas, e
medir: resolução menor (640×360 dá no máximo ~4×, ainda longe), decimar muito mais (3,8 M → ~0,5 M), ou
uma máquina com GPU no cluster. O teste é barato: a mesma imagem, sem `--gpus all`, com `--cpus=N` igual
ao que o pod teria, e olhar `fps` e `frame_ms p95` no `[medidor]`.

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
   variados), substituindo o circular. Vai para `Assets/StreamingAssets/passeio.csv` e `vr/passeio.csv`.
3. **Provar o caminho sem GPU antes de integrar** (decisão de 05/11): no PC, `docker run` sem `--gpus
   all`, `MODO_TELA=cpu`, `VR_ENCODER=x264`, com `--cpus=4`, `6` e `12`, a 640×360 e 1280×720, e, se
   quiser, um modelo mais decimado. Critério: `fps=60.0` e `frame_ms p95` ≤ 16,7 por 10 min. Se nenhuma
   combinação passar, voltar ao Pedro com os números: ou uma máquina com GPU entra no cluster, ou o
   plano B abaixo.
4. **Etapa 3 do plano, no PhotonPath:** `--render-sock` no `photon_server.py` (repassa poses, lê `PPRF`,
   payload real, métricas `render_source`, `render_frame_age_seconds`, `frames_enc_truncated_total`,
   `enc_bytes_last`; sem `PPRF`, volta ao sintético) e `--pose-trace` no `photon_client.py`. Testar em
   loopback com o `render_falso.py` e depois com o player. Dois commits listados no `ORIGEM.txt`.
5. **Etapa 5, o pod:** `client_scripts/vr-server-render.yaml` com dois containers (`photon-server` com
   `PP_RENDER_SOCK`, `unity-render` com a imagem), **sem `nodeSelector`**, requests = limits nos dois (o
   Unity com a CPU que o passo 3 mostrou ser necessária), imagem num registro que o cluster puxe (Docker
   Hub; privado com `imagePullSecret` se o modelo não puder ser público), ServiceMonitor do `vr-app-01`,
   regra nova no `conferir_execucao.py` (`render_source` e `frames_late_total`), opção `--render-real`
   no `build_testbeds.sh`. Atenção: sem `nodeSelector`, o servidor pode cair num nó diferente do
   `dash-server`, e aí o salto a mais (ponto 4 da tabela do plano) volta e o `net_ref` precisa ser
   remedido.
6. **Etapas 6 e 7** como no plano: conferir pisos e `net_ref`, e as execuções de validação (5 × `hvr_l2`,
   5 × `hvr_l0`) depois da Fase 8.
7. **Atualizar `docs/plano-render-real.md`** com a decisão de 05/11 e as medidas desta passagem.

## Plano B: render pré-computado (pronto para usar se o passo 3 falhar)

O Unity, no PC com GPU, renderiza o passeio uma vez com tempo fixo de 1/60 s por quadro
(`Time.captureFramerate = 60`, determinístico), codifica em CBR e grava um arquivo com um quadro por
instante da trajetória (~60 MB por minuto). O `photon_server` ganha `--render-file`: carrega os quadros na
memória e, para cada pose, escolhe pelo índice do passeio que vem no padding (`!I7f`), sem thread nem
fallback. O custo de render entra pelo `--render-ms` com o valor medido (22 ms é o teto honesto). O
arquivo vai ao cluster por PVC + `kubectl cp` (o namespace já cria PVC para o MongoDB), sem imagem
pública. O `vr-app-01` não muda de nó, o `net_ref` e os pisos continuam valendo, e as 30 execuções veem
exatamente os mesmos bytes. Do ponto de vista do cliente, o quadro recebido é a renderização do modelo na
pose enviada, igual ao ao vivo; o que some é o trabalho de GPU no servidor durante a execução.
