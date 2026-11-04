#!/bin/bash
# Sobe o player do Unity sem monitor, com GPU se houver. Escolha pela variável MODO_TELA:
#   egl    (padrão) Xvfb para a parte 2D + VirtualGL mandando o OpenGL para a GPU pela EGL. Precisa de --gpus all.
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

echo "[entrypoint] player=$PLAYER tela=$MODO_TELA ${LARGURA}x${ALTURA}"
command -v nvidia-smi >/dev/null && nvidia-smi --query-gpu=name,driver_version --format=csv,noheader || echo "[entrypoint] sem nvidia-smi: a GPU não entrou no container"
ffmpeg -hide_banner -encoders 2>/dev/null | grep -E "h264_nvenc|libx264" || true

case "$MODO_TELA" in
  egl)
    exec xvfb-run -a -s "-screen 0 ${LARGURA}x${ALTURA}x24" vglrun -d egl "$PLAYER" -force-glcore "${ARGS[@]}"
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
    exec xvfb-run -a -s "-screen 0 ${LARGURA}x${ALTURA}x24" "$PLAYER" -force-glcore "${ARGS[@]}"
    ;;
  *)
    echo "MODO_TELA desconhecido: $MODO_TELA (egl|xorg|direto|cpu)"; exit 1
    ;;
esac
