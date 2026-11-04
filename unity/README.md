# O modelo 3D no Unity, em Docker, no seu PC (etapas 1 e 2 do plano)

**Pergunta:** o seu modelo renderiza a 60 fps, sem monitor, com GPU, dentro de um container, e devolve quadros no formato que o PhotonPath vai consumir?

**O que está aqui:**

| Arquivo | O que faz |
| --- | --- |
| `Assets/Scripts/ControleVR.cs` | vai na Camera: lê a configuração, põe a câmera na pose (rede ou passeio), captura o quadro, resumo a cada 5 s |
| `Assets/Scripts/ReceptorPoses.cs` | recebe as poses repassadas (UDP 9300) |
| `Assets/Scripts/SaidaQuadros.cs` | readback da GPU, `ffmpeg` com NVENC ou x264 em CBR (ou JPEG sem `ffmpeg`), datagramas PPRF (UDP 9301) |
| `Assets/Scripts/Passeio.cs` | lê o CSV do passeio e interpola; `GravadorPasseio` grava um passeio no Editor com WASD e mouse |
| `Dockerfile`, `entrypoint.sh` | a imagem com GPU, VirtualGL e `ffmpeg`; quatro jeitos de "tela" sem monitor |
| `../vr/gerar_passeio.py` | um passeio circular sintético, para testar antes de gravar o seu |
| `../vr/testar_render.py` | faz o papel do PhotonPath: manda poses, recebe quadros, mede fps, render e tamanho |
| `../vr/render_falso.py` | o Unity de mentira, para testar o medidor (e, na etapa 3, o `photon_server`) |

Os dois scripts Python foram testados juntos em loopback: 60 fps, render de 3 ms, zero quadros estourados.

## Antes de começar: o que o seu PC precisa

- **Unity Hub + Unity 2022 LTS ou mais novo**, com o módulo *Linux Build Support (Mono)*. O backend tem de ser **Mono**, não IL2CPP: o `System.Diagnostics.Process` que abre o `ffmpeg` não existe no IL2CPP.
- **Para o teste em Docker com GPU: Linux com placa NVIDIA**, driver instalado, Docker e `nvidia-container-toolkit` (`docker run --rm --gpus all nvidia/cuda:12.2.2-base-ubuntu22.04 nvidia-smi` tem de mostrar a placa).
- **Se o seu PC é Windows:** o Docker Desktop roda em WSL2, e dentro de container no WSL2 não há OpenGL/Vulkan de GPU nem NVENC. Faça as etapas A e B no Windows mesmo (o player roda nativo, com `VR_ENCODER=x264` ou `jpeg`, e o `testar_render.py` roda no WSL ou no Windows), e deixe o Docker com GPU para uma máquina Linux. É a etapa 2 do plano, e ela só é respondida de verdade em Linux.
- `ffmpeg` no PATH se for rodar o player fora do container (`ffmpeg -encoders | grep h264` mostra `h264_nvenc` e `libx264`).

## A. A cena com o modelo (etapa 1)

1. **Converter o modelo.** O Unity não abre `.pln` nem `.skp`. ArchiCAD: *File > Save as* ou *Export* em FBX ou OBJ. SketchUp Pro: *File > Export > 3D Model* em FBX. Sem o Pro: Blender com o complemento *SketchUp Importer* abre o `.skp` e exporta FBX (`File > Export > FBX`, com *Apply Transform* marcado). Deixe as texturas junto (FBX com *Embed Media*, ou a pasta de texturas ao lado do OBJ).
2. **Projeto novo** no Unity Hub, modelo *3D (Built-in Render Pipeline)*, e copie `unity/Assets/Scripts/` para dentro de `Assets/`.
3. **Importar o modelo:** arraste o FBX para `Assets/`, depois para a cena. Na aba *Inspector* do FBX, *Model > Scale Factor* e *Convert Units* costumam resolver um prédio que vem 100 vezes maior ou menor.
4. **Decimar** (a meta é render ≤ 4 ms a 720p): apague mobiliário e detalhe que não aparecem no percurso; texturas com *Max Size* 1024; *Window > Rendering > Lighting*: luzes em *Baked* e *Generate Lighting*; sombras em tempo real desligadas na luz principal; *Window > Rendering > Occlusion Culling > Bake*.
5. **A câmera:** na `Main Camera`, adicione o componente `ControleVR` (ele cria o resto). Apague o `AudioListener` se o Unity reclamar de áudio.
6. **Gravar o passeio:** adicione também `GravadorPasseio` à câmera só para gravar. *Play*, aperte **R**, ande com WASD, olhe com o mouse, Q/E sobe e desce, 2 a 3 minutos passando por lugares diferentes (corredor simples, sala com janelas, área com textura), termine onde começou olhando para o mesmo lado, aperte **R** de novo. O CSV sai em `passeio.csv` na raiz do projeto. Depois, remova o `GravadorPasseio` e copie o CSV para `Assets/StreamingAssets/passeio.csv` (crie a pasta) e para `vr/passeio.csv` no projeto-5g.
   - Enquanto não gravar o seu, gere um circular: `python3 vr/gerar_passeio.py --centro X Z --raio 3 --altura 1.6 --out vr/passeio.csv`, com o centro dentro de um cômodo do modelo.
7. **Medir no Editor:** *Window > Analysis > Profiler*, *Play* com `VR_MODO=autonomo` (no Editor, defina as variáveis no terminal antes de abrir o Unity, ou mude os padrões no topo do `ControleVR.cs`). O console mostra a linha `[medidor]` a cada 5 s. O que importa: `frame_ms p95` ≤ 16 e `render_ms p95` ≤ 8.

**Pronto quando:** a cena roda a 60 fps no Editor com o passeio tocando, e o console mostra `[medidor] fps=60.0`.

## B. O player fora do container (um teste rápido antes do Docker)

Build: *File > Build Settings*, plataforma Linux (ou Windows, para testar no Windows), *Server Build* **desmarcado**, *Player Settings > Other Settings > Scripting Backend = Mono*. Salve em `unity/build/` com o nome `vrmodelo`.

Dois terminais, na raiz do projeto-5g:

```bash
python3 vr/testar_render.py --passeio vr/passeio.csv --duracao 30 --salvar /tmp/modelo.h264   # o "PhotonPath" de teste
```

```bash
VR_MODO=rede VR_ENCODER=x264 VR_PNG_S=5 VR_PNG_DIR=/tmp/quadros unity/build/vrmodelo.x86_64 -batchmode -screen-width 1280 -screen-height 720 -logFile -   # Linux
```

No Windows, o mesmo com `vrmodelo.exe` (ele abre uma janela; tudo bem). O que conferir:

- o medidor imprime `fps=60.0`, `enc_kB` entre 10 e 16, `estourados=0`, `idade_ms p95` abaixo de 25;
- `ffplay /tmp/modelo.h264` mostra o seu modelo com a câmera andando (sem `ffplay`: `ffmpeg -i /tmp/modelo.h264 /tmp/modelo.mp4` e abra o mp4). Com `VR_ENCODER=jpeg`, `ffplay -f mjpeg /tmp/modelo.h264`;
- os PNGs em `/tmp/quadros` são o quadro cru, antes do codificador: se o PNG está certo e o vídeo não, o problema é o `ffmpeg`.

Se a linha `[saida] ffmpeg` aparecer seguida de erro, rode o comando dela à mão no terminal para ver a mensagem inteira.

## C. O container com GPU (etapa 2, em Linux)

```bash
docker build -t vr-render unity/                                                                      # a imagem, com o build em unity/build/
mkdir -p saida
docker run --rm --gpus all --network host -e VR_MODO=autonomo -e VR_PNG_S=5 -v "$PWD/saida:/saida" vr-render   # 1º teste: sozinho, sem poses
```

O log tem de começar com a placa (`nvidia-smi`), a lista de codificadores com `h264_nvenc`, e depois a linha `[controle] ... gpu=NVIDIA ... api=OpenGLCore`. Se `gpu=` mostrar `llvmpipe`, o render está na CPU: a GPU não entrou (ver abaixo).

Depois, com as poses vindo de fora, os dois terminais:

```bash
docker run --rm --gpus all --network host -e VR_MODO=rede -v "$PWD/saida:/saida" vr-render
```

```bash
python3 vr/testar_render.py --passeio vr/passeio.csv --duracao 600 --salvar /tmp/modelo.h264   # 10 min, o critério da etapa 2
```

**Pronto quando:** 10 min com `fps=60.0`, `render_ms p95` ≤ 8 no medidor do Python (inclui readback e codificação), `estourados=0`, `descartados=0` no `[medidor]` do Unity, e `nvidia-smi` no host mostrando o processo usando a GPU.

### Se a GPU não entra

Tente os modos de tela na ordem, mudando `-e MODO_TELA=`:

| Modo | O que é | Quando usar |
| --- | --- | --- |
| `egl` (padrão) | Xvfb para o 2D + VirtualGL mandando o OpenGL para a GPU pela EGL | o caminho que mais vezes funciona em container |
| `direto` | só o player com Vulkan, sem servidor gráfico | vale tentar: algumas versões do Unity renderizam fora de tela |
| `xorg` | um Xorg sem monitor com o driver NVIDIA | exige o módulo X do driver dentro da imagem, da mesma versão do host |
| `cpu` | Xvfb com llvmpipe | só para provar que o resto do pipeline funciona; não dá 60 fps |

Sintomas e causas comuns:

- `[entrypoint] sem nvidia-smi`: faltou `--gpus all` ou o `nvidia-container-toolkit`.
- `vglrun: ... could not open EGL device`: o toolkit não expôs a parte gráfica; confira `NVIDIA_DRIVER_CAPABILITIES=all` (já está no Dockerfile) e a versão do toolkit.
- `h264_nvenc` ausente na lista ou `Cannot load libnvidia-encode.so`: o driver não expôs o NVENC ao container; use `-e VR_ENCODER=x264` para seguir e anote.
- O player fecha na hora com `No supported renderers found`: é o Unity sem GL; tente `direto` ou outra versão do Unity.

## O que levar para o plano

Anote no `docs/plano-render-real.md` (ou num `docs/render-local.md`) o que saiu: versão do Unity, placa, modo de tela que funcionou, `render_ms p50/p95`, `enc_kB` médio e o tamanho da cena depois de decimar. São os números que a etapa 5 vai usar para dimensionar o pod.
