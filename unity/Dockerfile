# Imagem do render: o player Linux do Unity, com GPU NVIDIA, VirtualGL e ffmpeg com NVENC.
#
# Antes do build, exporte o projeto do Unity (File > Build Settings > Linux, Mono, Headless DESLIGADO)
# para unity/build/, de modo que exista unity/build/<nome>.x86_64 e unity/build/<nome>_Data/.
#
#   docker build -t vr-render unity/
#   docker run --rm --gpus all --network host -e VR_MODO=autonomo -e VR_PNG_S=5 -v $PWD/saida:/saida vr-render
#
# A GPU entra pelo nvidia-container-toolkit (--gpus all). Sem toolkit, só o modo de tela "cpu" funciona.

FROM nvidia/cuda:12.2.2-base-ubuntu22.04

ARG VGL_VERSAO=3.1.1
ENV DEBIAN_FRONTEND=noninteractive \
    NVIDIA_DRIVER_CAPABILITIES=all \
    NVIDIA_VISIBLE_DEVICES=all

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates wget xvfb ffmpeg \
        libglvnd0 libgl1 libglx0 libegl1 libgles2 libglu1-mesa \
        libx11-6 libxext6 libxcursor1 libxrandr2 libxi6 libxinerama1 libxxf86vm1 libxss1 \
        libgtk-3-0 libasound2 libpulse0 libnss3 \
        xserver-xorg-core xserver-xorg-video-dummy x11-xserver-utils \
    && wget -q -O /tmp/vgl.deb https://github.com/VirtualGL/virtualgl/releases/download/${VGL_VERSAO}/virtualgl_${VGL_VERSAO}_amd64.deb \
    && apt-get install -y /tmp/vgl.deb && rm /tmp/vgl.deb \
    && rm -rf /var/lib/apt/lists/*

ENV PATH=/opt/VirtualGL/bin:$PATH

COPY build/ /opt/unity/
COPY entrypoint.sh /opt/entrypoint.sh
RUN chmod +x /opt/entrypoint.sh /opt/unity/*.x86_64 && mkdir -p /saida

EXPOSE 9300/udp
ENTRYPOINT ["/opt/entrypoint.sh"]
