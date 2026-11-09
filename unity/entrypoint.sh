#!/bin/bash
# Sobe o player do Unity sem monitor, com GPU se houver. Escolha pela variável MODO_TELA:
#   egl    (padrão) Xvfb para a parte 2D + VirtualGL mandando o OpenGL para a GPU pela EGL. Precisa de --gpus all.
#          Confirmado em 04/11/2026 numa RTX 4060 Ti: o Unity reporta a placa como Renderer.
#   xorg   um Xorg sem monitor com o driver NVIDIA (exige o módulo nvidia_drv.so do mesmo driver do host; raro em container).
#   direto o player sozinho, com Vulkan, sem servidor gráfico. Funciona em algumas versões do Unity; vale tentar.
#   cpu    Xvfb com o OpenGL por software (llvmpipe). Funciona sempre, mas não chega a 60 fps: só para provar o resto.
# As variáveis VR_* vão direto para o player (ver Assets/Scripts/ControleVR.cs).

set -euo pipefail

MODO_TELA="${MODO_TELA:-egl}"
LARGURA="${VR_LARGURA:-1280}"
ALTURA="${VR_ALTURA:-720}"
PLAYER="$(ls /opt/unity/*.x86_64 | head -1)"
ARGS=(-screen-width "$LARGURA" -screen-height "$ALTURA" -screen-fullscreen 0 -logFile -)   # sem -batchmode nem -nographics: os dois desligam o render das câmeras

# Tela virtual. Xvfb direto, e não xvfb-run: o xvfb-run travava sem imprimir nada dentro do container
# (04/11/2026), enquanto o Xvfb iniciado à mão, com o DISPLAY exportado, funcionou de primeira.
# -nolisten local: sem o socket abstrato, que com --network host é o mesmo para todos os containers da máquina.
tela_virtual() {
    Xvfb :77 -screen 0 "${LARGURA}x${ALTURA}x24" -nolisten tcp -nolisten local >/tmp/xvfb.log 2>&1 &
    for _ in $(seq 1 50); do [ -S /tmp/.X11-unix/X77 ] && break; sleep 0.1; done
    [ -S /tmp/.X11-unix/X77 ] || { echo "[entrypoint] o Xvfb não abriu:"; cat /tmp/xvfb.log; exit 1; }
    export DISPLAY=:77
}

echo "[entrypoint] player=$PLAYER tela=$MODO_TELA ${LARGURA}x${ALTURA}"
command -v nvidia-smi >/dev/null && nvidia-smi --query-gpu=name,driver_version --format=csv,noheader || echo "[entrypoint] sem nvidia-smi: a GPU não entrou no container"
ffmpeg -hide_banner -encoders 2>/dev/null | grep -E "h264_nvenc|libx264" || true

case "$MODO_TELA" in
  egl)
    tela_virtual
    exec vglrun -d egl "$PLAYER" -force-glcore "${ARGS[@]}"
    ;;
  xorg)
    cat > /tmp/xorg.conf <<EOF
Section "Device"
    Identifier "gpu"
    Driver "nvidia"
    Option "AllowEmptyInitialConfiguration" "true"
    Option "UseDisplayDevice" "none"
EndSection
Section "Screen"
    Identifier "tela"
    Device "gpu"
    DefaultDepth 24
    SubSection "Display"
        Virtual ${LARGURA} ${ALTURA}
    EndSubSection
EndSection
EOF
    Xorg :99 -config /tmp/xorg.conf -noreset -nolisten tcp &
    sleep 2
    export DISPLAY=:99
    exec "$PLAYER" -force-glcore "${ARGS[@]}"
    ;;
  direto)
    exec "$PLAYER" -force-vulkan "${ARGS[@]}"
    ;;
  cpu)
    tela_virtual
    exec "$PLAYER" -force-glcore "${ARGS[@]}"
    ;;
  *)
    echo "MODO_TELA desconhecido: $MODO_TELA (egl|xorg|direto|cpu)"; exit 1
    ;;
esac
