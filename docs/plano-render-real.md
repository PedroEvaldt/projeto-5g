# Plano: o modelo 3D renderizado de verdade no servidor de VR (Fase 9)

**Pergunta:** o resultado do leilão se mantém quando o servidor de VR deixa de ser um gerador sintético e passa a renderizar o modelo 3D do projeto, com um usuário simulado andando por ele?

**Estado:** plano, nada construído ainda (03/11/2026). Entra depois da Fase 8, como fase própria.

## Decisões já tomadas

| Decisão | Por quê |
| --- | --- |
| **O Unity roda no cluster, num nó com GPU, no mesmo pod que o PhotonPath** | O PhotonPath continua sendo quem fala com a rede: ele já tem o espaçamento de pacotes, o `hold_us` por pacote, o timeout de sessão e o `/metrics`. Reescrever isso em C# é o maior risco e não dá nada em troca. O Unity só renderiza e codifica; os bytes reais vão no payload no lugar do padrão `i & 0xFF`. |
| **Taxa constante (CBR): 14 pacotes × 1200 B a 60 fps, como hoje** | É o que o streaming de VR real faz (CloudXR, ALVR): num orçamento de 16 ms por quadro não há buffer para absorver um quadro grande. E mantém a `upf4` vendo o mesmo tráfego da Fase 8, então o SLA, os pisos e o agente continuam valendo. O Unity fica livre na qualidade, não na taxa. |
| **O usuário simulado é uma trajetória gravada, tocada pelo `photon_client`** | O cliente não exibe nada, e o movimento de cabeça de um usuário real não depende da rede. A trajetória vai nos 100 bytes de padding da pose, sem mudar o protocolo nem a imagem do UE. |
| **Exporter, agente SPADE, leilão, `deploy_test.py` e a fila não mudam** | Tudo acima do exporter só enxerga `vr_net_p95_us`, `vr_loss_pct`, `vr_continuity_pct`, `vr_throughput_mbps`, `vr_health_ok`, `vr_paused`, os sinais USR1/USR2 e o `/healthz`. |
| **Fase própria, depois da Fase 8** | A fila exige o mesmo commit nas 150 execuções; trocar o servidor no meio mistura dois sistemas. A Fase 9 compara o render real com o gerador sintético num subconjunto, e isso é um resultado por si só. |
| **Mudanças em `photonpath/` entram na lista do `ORIGEM.txt`, em commits próprios** | Regra de 30/10/2026. As duas mudanças (servidor com fonte de render externa; cliente com trajetória) são candidatas a voltar para o repositório do Carlos como PR. |

## Arquitetura

```
ue-vr-07 (blacksabbath)                            vr-app-01 (nó com GPU)
+------------------------------+                    +------------------------------------------+
| gnb | ue | vr-client         |   poses PPOS       | photon-server (python:3.12-slim)          |
|   vr_exporter.py (igual)     | -----------------> |   rx_loop: guarda a pose e a repassa  --+ |
|   photon_client.py           |   via upf4         |   tx_loop: pega o último quadro         | |
|     --pose-trace passeio.csv | <----------------- |     codificado e manda em 14 pacotes    | |
+------------------------------+   quadros PPFR     |          ^ PPRF (localhost)   PPOS+recv | |
                                                    |          |                              v |
                                                    | unity-render (player Linux + GPU + ffmpeg)|
                                                    |   lê a pose, move a câmera no modelo,     |
                                                    |   renderiza, codifica H.264 CBR, devolve  |
                                                    +------------------------------------------+
```

- **O PhotonPath repassa cada pose** que recebe para o Unity, por UDP em `127.0.0.1`, com o carimbo de chegada (`recv_ns`) anexado.
- **O Unity renderiza no ritmo dele** (60 fps) a câmera na última pose, codifica e devolve ao PhotonPath um datagrama `PPRF` com o quadro e a identificação da pose que ele responde.
- **No instante do quadro, o `tx_loop` usa o último `PPRF` recebido.** O quadro que vai para o fio responde à pose com que foi renderizado (não à última pose que chegou), e `hold_us` é contado desde a chegada dessa pose. Isso é o que torna a decomposição `m2p = net + hold` honesta com render real: o tempo de render e o pipeline ficam em `hold`, e `net_us` continua sendo só o caminho.
- **Sem `PPRF` (Unity caído ou ainda subindo), o servidor volta ao payload sintético** e publica `render_source 0` no `/metrics`. O testbed continua de pé, e o verificador descarta a execução. O `/healthz` continua querendo dizer "o servidor responde", como o exporter espera.

## O contrato entre o PhotonPath e o render

Dois datagramas novos, só em loopback (o MTU do `lo` é 65536, então um quadro de 16 kB cabe inteiro). Tudo big-endian, como o resto do protocolo.

**Pose repassada** (PhotonPath → Unity, `127.0.0.1:9300`): o pacote `PPOS` de 128 B exatamente como chegou, mais 8 bytes `Q` com o `recv_ns` (carimbo do kernel na chegada). O Unity lê `seq`, `t_client_ns` e o padding; devolve os três sem interpretar.

**Padding da pose** (os 100 bytes depois do cabeçalho de 28): `struct.Struct("!I7f")`, 32 bytes: índice da amostra na trajetória, posição `x y z` e quaternion `qx qy qz qw`. O resto continua zero.

**Quadro renderizado** (Unity → PhotonPath, `127.0.0.1:9301`): `struct.Struct("!4sQQQII")`, 36 bytes de cabeçalho, seguidos do quadro codificado.

| Campo | Tipo | Significado |
| --- | --- | --- |
| magic | 4s | `PPRF` |
| pose_seq | Q | `seq` da pose com que o quadro foi renderizado |
| t_pose_ns | Q | `t_client_ns` dessa pose, ecoado |
| recv_ns | Q | `recv_ns` dessa pose, ecoado |
| render_us | I | render + codificação medidos no Unity |
| enc_bytes | I | tamanho do quadro codificado que segue |

**Acabamento no PhotonPath.** Cada quadro no fio tem 14 × (1200 − 48) = 16 128 bytes de payload. O quadro codificado é espalhado por esses 14 payloads na ordem; o que sobra é preenchido com o padrão `i & 0xFF` (não zeros); o que passa é cortado e contado em `frames_enc_truncated_total`. O cliente não decodifica, então o corte não afeta nenhuma medida. O alvo do codificador fica abaixo de 16 128 × 60 × 8 = 7,74 Mbps, com *intra-refresh* no lugar de quadros-chave, para nenhum quadro estourar o orçamento.

**Sessões.** Uma sessão (o `ue-vr-07`). Com mais de uma ativa, o servidor manda o mesmo quadro para todas e avisa no log. Limitação conhecida da fase.

## Etapas

Cada etapa tem um critério de pronto. As etapas 1 e 3 não dependem do cluster e podem andar em paralelo com a Fase 8.

### Etapa 0: o cluster tem o que precisa? (meio dia)

Nada do resto vale a pena sem estas cinco respostas. São só leituras.

```bash
kubectl get nodes -o custom-columns='NO:.metadata.name,GPU:.status.allocatable.nvidia\.com/gpu,TAINTS:.spec.taints[*].key'   # o nó de GPU aparece com nvidia.com/gpu ≥ 1?
kubectl get runtimeclass                                                           # existe "nvidia"? (nem todo cluster precisa)
kubectl auth can-i create pods -n pfsevaldt; kubectl auth can-i create secrets -n pfsevaldt   # agendar no nó e, se a imagem for privada, criar o imagePullSecret
kubectl get resourcequota -n pfsevaldt -o yaml                                     # a cota deixa pedir GPU, mais CPU e mais memória?
kubectl describe node <nó de GPU> | grep -A5 -i taint                              # se tiver taint, o pod precisa de toleration
```

- **Se o nó de GPU não está no cluster**, entrar com ele é tarefa de quem administra (kubeadm join, driver NVIDIA, `nvidia-container-toolkit`, device plugin). Pedir isso cedo.
- **Caminho de volta até o UE.** Subir um pod qualquer (`python:3.12-slim` dormindo) no nó de GPU e, do `vr-client`, dar `ping -I uesimtun0 <IP do pod>`. Se a `upf4` faz NAT, qualquer nó responde; se não faz, o nó de GPU precisa de rota para a faixa dos UEs. A conferência do `run_experiment.sh` já faz esse ping no servidor, então isso tem de funcionar antes de mover o servidor.
- **Salto a mais.** Do mesmo pod, `ping` até um pod na `wb-fibre-01` (onde está a `upf4`), 100 amostras. Anotar p50 e p95. É o que o `net_rtt` da VR vai ganhar em relação à topologia de hoje.
- **Registro de imagens.** O cluster já puxa `python:3.12-slim` e `free5gc/ueransim` do Docker Hub, então um repositório público no Docker Hub ou no GHCR funciona. Se o modelo não pode ser público, repositório privado e `imagePullSecret` no namespace.

**Pronto quando:** GPU alocável, pod agendado no nó, ping do UE chegando, RTT do salto anotado, imagem de teste puxada.

### Etapa 1: o modelo e a trajetória no Unity (2 a 4 dias, no PC)

1. **Converter o modelo.** O Unity não abre `.pln` nem `.skp`. Do ArchiCAD, exportar FBX ou OBJ (IFC se quiser as camadas). Do SketchUp Pro, FBX ou OBJ. Sem o Pro, o Blender com o complemento de importação de SKP lê o arquivo e exporta FBX.
2. **Decimar.** Modelos arquitetônicos vêm com mobiliário e detalhe que não aparecem a 720p. A meta é a cena renderizar em menos de 4 ms por quadro numa GPU modesta. No Unity: *Mesh Simplification*, remover o que não se vê do percurso, texturas em 1024 ou menos, *occlusion culling* ligado, iluminação pré-calculada (*baked*), sem sombras em tempo real.
3. **Projeto Unity** (2022 LTS ou mais novo, com *Linux Build Support*): a cena com o modelo, uma `Camera` a 1280×720, `Application.targetFrameRate = 60`, VSync desligado.
4. **Gravar a trajetória.** Um script que move a câmera por um percurso em laço (uma spline fechada pelo prédio, 2 a 3 min) e grava a 250 Hz um CSV `t_s,x,y,z,qx,qy,qz,qw`. Laço fechado para o cliente repetir sem salto. Variar a cena de propósito: corredor simples, sala com janelas, área com textura densa, para a qualidade (não a taxa) variar.
5. **Medir no Editor**, com o *Profiler*: ms por quadro ao longo do percurso. Se o p95 passa de 4 ms, decimar mais.

**Pronto quando:** `passeio.csv` gerado; a cena roda a 60 fps estáveis no PC com p95 de render ≤ 4 ms.

### Etapa 2: o player renderiza sem tela, com GPU, dentro de um pod (1 a 3 dias; é a aposta)

É a etapa que mais costuma falhar, e tem de vir antes de qualquer integração.

1. **Build Linux** do projeto (player, não editor). Sem `-nographics` depois: isso desliga o render.
2. **Imagem.** Base com driver de usuário da NVIDIA e `ffmpeg` com NVENC (as imagens `nvidia/cuda` servem de base; o `ffmpeg` precisa ser um build com `h264_nvenc`). Copiar o player para `/opt/unity/`. Ponto de entrada: um script que sobe o que o player precisa para ter "tela" e roda `./player.x86_64 -force-vulkan -screen-width 1280 -screen-height 720 -logFile -` (sem `-batchmode`: no player ele pula o render das câmeras).
3. **"Tela" sem monitor**, tentar nesta ordem:
   - (a) direto, só com `-force-vulkan`: algumas versões renderizam fora de tela sem servidor gráfico;
   - (b) um Xorg sem monitor com o driver NVIDIA (`nvidia-xconfig --allow-empty-initial-configuration`, tela virtual), como fazem o VirtualGL e o Selkies; é o caminho que mais vezes funciona com GPU;
   - (c) `xvfb-run`: funciona sempre, mas renderiza na CPU (llvmpipe). Serve só para provar o resto do pipeline, não para os 60 fps.
4. **Manifesto de teste:** `nodeSelector` no nó de GPU, `resources.limits: nvidia.com/gpu: 1`, `runtimeClassName: nvidia` se a etapa 0 mostrou que existe, tolerations se há taint. CPU 4 e memória 4Gi para começar.
5. **Prova:** o player roda 10 min renderizando o percurso e escreve no log o tempo de render por quadro (`Time.deltaTime` e o relógio do `AsyncGPUReadback`). Em paralelo, `nvidia-smi` no pod mostra uso de GPU.

**Pronto quando:** 10 min a 60 fps com p95 de render ≤ 4 ms, GPU em uso, sem tela. Se só (c) funcionar, parar aqui e decidir: outra versão do Unity, outra imagem base, ou ficar na opção A (o Unity só mede os parâmetros do PhotonPath).

### Etapa 3: as duas mudanças no PhotonPath, testadas em loopback (3 a 5 dias, sem GPU)

Pode ser feita no PC ou na ledzeppelin, com um render falso em Python no lugar do Unity.

**No servidor (`photon_server.py`), opção `--render-sock` (`PP_RENDER_SOCK`, vazio = como hoje):**
- `rx_loop`: depois de guardar a pose, repassá-la com o `recv_ns` para `127.0.0.1:9300` (um `sendto` sem bloqueio; perder um repasse não é erro).
- uma thread nova lê `127.0.0.1:9301` e guarda o último `PPRF` (cabeçalho e bytes), com trava.
- `tx_loop`: se há `PPRF`, `send_frame` recebe `seq`, `t_pose_ns`, `recv_ns` e `render_us` do `PPRF`, e não do retrato da sessão; se não há, segue como hoje.
- `send_frame`: copia a fatia do quadro codificado no payload de cada pacote; preenche o resto com `i & 0xFF`; corta e conta se passar.
- métricas novas: `render_source` (0 sintético, 1 real), `render_frame_age_seconds` (idade do `PPRF` usado no instante do quadro), `frames_enc_truncated_total`, `enc_bytes_last`.
- banner: `render=externo sock=127.0.0.1:9301`.

**No cliente (`photon_client.py`), opção `--pose-trace` (`PP_POSE_TRACE`, vazio = padding zero):**
- lê o CSV uma vez, na partida; a cada pose, pega a amostra pelo tempo decorrido (módulo a duração do laço) e empacota `!I7f` no padding.
- nada mais muda: tamanho, taxa, `t_client_ns`, BYE.

**Render falso para testar (`vr/render_falso.py`, fica no projeto):** lê as poses em 9300, espera 3 ms, devolve em 9301 um `PPRF` com `render_us=3000` e um payload qualquer de ~15 kB. Com ele:

```bash
python3 vr/render_falso.py &                                                          # o "Unity" de mentira
python3 vr/pp_launcher.py photon_server.py --bitrate-mbps 8 --fps 60 --pace-duty 0.5 --render-sock 127.0.0.1:9301 &
python3 vr/pp_launcher.py photon_client.py --server 127.0.0.1:9100 --bind 127.0.0.1 --duration 15 --warmup 3 --pose-trace vr/passeio.csv --csv /tmp/t.csv
```

**Pronto quando:** em loopback, `frames_late_total = 0`, `render_source = 1`, `hold_p95` subiu ~3 ms mais a idade do quadro em relação ao sintético e `net_p95` ficou igual (décimos de ms); sem o render falso, o servidor cai no sintético e `render_source = 0`; o CSV do cliente mostra `render_us ≈ 3000`. Os dois commits listados no `photonpath/ORIGEM.txt`.

### Etapa 4: o lado do Unity (3 a 5 dias)

Dentro do player, três peças:

1. **Receptor de poses** (thread de rede, `UdpClient` em 9300): lê o `PPOS` + `recv_ns`, extrai `seq`, `t_client_ns`, `recv_ns` e o `!I7f` do padding, guarda a última.
2. **Câmera e leitura do quadro** (thread principal): a cada `Update`, aplica posição e rotação da última pose; renderiza para um `RenderTexture` 1280×720; `AsyncGPUReadback` para a CPU. Anota `Stopwatch` do início do render à chegada dos pixels.
3. **Codificação e envio:** o player sobe um `ffmpeg` filho e escreve os quadros crus no `stdin` dele; lê o H.264 do `stdout`, separa por unidade de acesso (AUD) e manda cada uma num datagrama `PPRF` para 9301, com `render_us` = render + codificação. Ponto de partida do `ffmpeg` (ajustar):

```bash
ffmpeg -f rawvideo -pix_fmt rgba -s 1280x720 -r 60 -i - \
  -c:v h264_nvenc -preset p1 -tune ull -rc cbr -b:v 7M -maxrate 7M -bufsize 120k \
  -g 999999 -bf 0 -intra-refresh 1 -zerolatency 1 -f h264 -
```

- `-rc cbr -b:v 7M` é o alvo abaixo dos 7,74 Mbps de teto; `-intra-refresh 1` espalha o refresh pelos quadros, sem quadro-chave gigante; `-bf 0` tira os quadros B, que atrasam.
- **Sem NVENC:** `libx264 -preset ultrafast -tune zerolatency -x264-params nal-hrd=cbr:intra-refresh=1:keyint=infinite`, que a 720p60 cabe em 2 a 4 núcleos. Mais lento, mas serve de plano B.
- **Por que o `ffmpeg` e não codificar dentro do Unity:** o Unity no Linux não expõe um codificador H.264 de GPU fora do pacote de WebRTC, e um plugin nativo com NVENC é mais trabalho que o resto da fase junto.

**Pronto quando:** no pod da etapa 2, com o render falso trocado pelo player, o `photon_server` mostra `render_source = 1`, `enc_bytes_last` entre 12 e 16 kB, `frames_enc_truncated_total = 0` e `render_frame_age_seconds` p95 abaixo de 20 ms (um período de pipeline).

### Etapa 5: o pod no testbed (2 a 3 dias)

1. **`client_scripts/vr-server.yaml`:** `nodeSelector` no nó de GPU; o container `photon-server` ganha `PP_RENDER_SOCK`; um segundo container `unity-render` com a imagem da etapa 2, a GPU nos limits e CPU/memória garantidas (requests = limits, para manter o Guaranteed). Os dois compartilham a rede do pod, então o loopback basta. O `Service vr-app-01` não muda, e o exporter continua achando o servidor pelo mesmo nome.
2. **`client_scripts/vrclients-script.sh`:** o `vr_exporter.py` passa a chamar o cliente com `--pose-trace /opt/vr/passeio.csv`; o CSV vai no ConfigMap `vr-launcher` (cabe: 250 Hz × 180 s × ~60 B ≈ 2,7 MB passa do limite de 1 MiB, então gravar a 50 Hz, ~540 kB, e interpolar no cliente, ou guardar só uma volta de 60 s).
3. **`scripts/build_testbeds.sh`:** opção `--render-real` que aplica o manifesto novo em vez do atual. O padrão continua sendo o PhotonPath sintético, para a Fase 8 não mudar.
4. **ServiceMonitor do `vr-app-01`** (o `photonpath/deploy/servicemonitor.yaml` é o modelo), para o Prometheus guardar `render_source`, `frames_late_total` e `render_frame_age_seconds` de cada execução.
5. **`metricas_scripts/conferir_execucao.py`:** regra nova, só quando a execução é de render real: descartar se `render_source` foi 0 em algum instante ou `frames_late_total` subiu.
6. **Teste pelo túnel:** 10 min com os 3 vídeos tocando, lendo as janelas `[VR]`: `health=1`, `thr_mbps ≈ 8,05`, `loss_pct` igual ao de hoje, `hold_p95` maior (render + pipeline, e tem de ser explicável), `net_p95` igual ao de hoje mais o salto medido na etapa 0.
7. **CPU e throttling do pod**, com o amostrador do `docs/vr-no-testbed.md` nos dois containers: throttling 0 nos 10 min.

**Pronto quando:** o teste de 10 min passa com `health=1` em todas as janelas, `frames_late_total = 0`, throttling 0, e o `delete_testbeds.sh` + `build_testbeds.sh --render-real` sobe tudo do zero.

### Etapa 6: remedir o que a topologia muda (1 dia de cluster)

1. **`net_ref` novo:** o `calibrar.py` nos pontos de folga (CPU 0,4 e banda 15), 5 medidas. Esperado: 1,87 ms mais o salto da etapa 0. Esse valor vai num `vr/calibracao_render.yaml`, para o `calibracao.yaml` da Fase 8 continuar intacto.
2. **Pisos conferidos, não refeitos:** CPU em 0,20, 0,25 e 0,30 e banda em 8,5, 9 e 10 Mbps, 3 repetições cada (~1 h por recurso). Como o tráfego na `upf4` é idêntico, a expectativa é que as curvas batam com as das Fases 3 e 4. Se baterem, os pisos ficam os mesmos; se não, a calibração completa (3 h por recurso) e uma explicação.

**Pronto quando:** `calibracao_render.yaml` escrito, com o `net_ref` novo e a conferência dos pisos registrada.

### Etapa 7: a validação (2 a 3 dias de fila)

Um subconjunto da Fase 8, com o servidor de render real: 5 execuções de `hvr_l2` e 5 de `hvr_l0`, que são as combinações que mais contrastam, pela mesma fila (`run_fase8.sh` com os YAMLs apontando para `resultados/fase9/` e para o `calibracao_render.yaml`). Se der tempo, 5 de `scvr_l2`.

A comparação com a Fase 8, por combinação: % de janelas no SLA, rodadas vencidas pela VR, CPU e banda máximas da VR, bitrate dos três vídeos, quedas de PFCP. A pergunta é só uma: as curvas ficam dentro da variação entre execuções da Fase 8? Se sim, o gerador sintético era uma aproximação fiel, e as 150 execuções ganham força. Se não, a diferença é o resultado, e tem de ser explicada (o candidato óbvio é o `hold` maior entrando em alguma decisão).

**Pronto quando:** `docs/render-real.md` escrito no formato dos outros (pergunta, resposta, ambiente, método, resultados, limites, o que muda no plano).

## O que muda no repositório

| Arquivo | Mudança | Etapa |
| --- | --- | --- |
| `photonpath/src/photon_server.py` | `--render-sock`: repasse de poses, leitura do `PPRF`, payload real, 4 métricas | 3 |
| `photonpath/src/photon_client.py` | `--pose-trace`: padding com a trajetória | 3 |
| `photonpath/ORIGEM.txt` | as duas mudanças na lista | 3 |
| `vr/render_falso.py` (novo) | o render de mentira para testar sem GPU | 3 |
| `vr/passeio.csv` (novo) | a trajetória gravada no Unity | 1 |
| `unity/` (novo) | o projeto Unity: cena, scripts C#, `Dockerfile`, `entrypoint.sh` | 1, 2, 4 |
| `client_scripts/vr-server-render.yaml` (novo) | o pod com os dois containers, no nó de GPU | 5 |
| `client_scripts/vrclients-script.sh` | `--pose-trace` na chamada do cliente | 5 |
| `scripts/build_testbeds.sh` | opção `--render-real` | 5 |
| `metricas_scripts/conferir_execucao.py` | regra `render_source` / `frames_late_total` | 5 |
| `vr/calibracao_render.yaml` (novo) | `net_ref` e conferência dos pisos na topologia nova | 6 |
| `tests_configs/test_config_*_render.yaml` (novos) | os YAMLs da Fase 9 | 7 |

## Riscos e o plano B de cada um

| Risco | Sinal | Plano B |
| --- | --- | --- |
| O player não renderiza com GPU sem tela | etapa 2 só passa com `xvfb` | Trocar versão do Unity ou imagem base; se nada, opção A (o Unity só mede `render_ms` e bitrate, fora de linha) |
| O cluster não tem GPU alocável ou o namespace não pode usá-la | etapa 0 | Pedir ao administrador; enquanto isso, etapas 1 e 3 andam sem GPU |
| Render + codificação não cabem nos ~8 ms | `frames_late_total > 0` ou `render_frame_age_seconds` alto | Reduzir resolução (960×540), decimar mais, ou aceitar pipeline de 2 quadros (mais `hold`, `net` intacto) |
| O codificador estoura os 16 128 B em alguns quadros | `frames_enc_truncated_total > 0` | Baixar `-b:v` para 6,5M e apertar `-bufsize`; o corte não afeta as medidas, mas o contador tem de ficar perto de zero para o payload ser "o modelo de verdade" |
| O UE não alcança o nó de GPU pela `upf4` | ping da etapa 0 falha | Rota no nó de GPU para a faixa dos UEs (admin), ou NAT na `upf4` |
| O salto a mais come a folga do SLA | `net_ref` novo passa de ~2,5 ms | O SLA da Fase 9 usa o `net_ref` remedido; a folga de 3 ms é a mesma, e a diferença entra no doc |
| O modelo não pode ir numa imagem pública | decisão sua | Repositório privado e `imagePullSecret` (precisa de `create secrets` no namespace) |

## Ordem e tempo

| Semana | O que anda |
| --- | --- |
| 1 | Etapa 0 (meio dia) e etapa 1 (modelo, cena, trajetória); etapa 3 começa em paralelo |
| 2 | Etapa 2 (a aposta da GPU sem tela) e o fim da etapa 3 em loopback |
| 3 | Etapa 4 (Unity falando o contrato) |
| 4 | Etapa 5 (o pod no testbed) e etapa 6 (remedição) |
| 5 | Etapa 7 (fila) e o doc |

A Fase 8 continua rodando durante as semanas 1 a 3 sem conflito, porque nada do padrão muda até a etapa 5, e a etapa 5 entra por opção (`--render-real`).
