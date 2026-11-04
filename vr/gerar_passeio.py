#!/usr/bin/env python3
"""
Gera uma trajetória sintética para testar o render antes de existir um passeio gravado no Unity.

A câmera anda num círculo na altura dos olhos, olhando para a frente (tangente ao círculo), com um
leve balanço de cabeça. É um laço fechado: o último ponto emenda no primeiro, então o cliente pode
repetir sem salto. O formato é o mesmo que o GravadorPasseio.cs grava no Unity:

    t_s,x,y,z,qx,qy,qz,qw

Eixos do Unity: y para cima, z para a frente, mão esquerda. O quaternion é a rotação da câmera.

Uso:
  python3 vr/gerar_passeio.py --centro 0 0 --raio 3 --altura 1.6 --duracao 60 --hz 50 --out vr/passeio.csv
Ajuste --centro, --raio e --altura para o círculo cair dentro do seu modelo.
"""

import argparse
import math


def quaternion_yaw_pitch(yaw, pitch):
    """Quaternion (x, y, z, w) de uma rotação yaw em torno de y e pitch em torno de x, como o Unity faz."""
    cy, sy = math.cos(yaw / 2), math.sin(yaw / 2)
    cp, sp = math.cos(pitch / 2), math.sin(pitch / 2)
    # q = q_yaw * q_pitch
    return (sp * cy, cp * sy, -sp * sy, cp * cy)


def main():
    p = argparse.ArgumentParser(description="trajetória circular para o teste do render")
    p.add_argument("--centro", type=float, nargs=2, default=(0.0, 0.0), metavar=("X", "Z"))
    p.add_argument("--raio", type=float, default=3.0)
    p.add_argument("--altura", type=float, default=1.6, help="altura dos olhos (y)")
    p.add_argument("--duracao", type=float, default=60.0, help="segundos de uma volta")
    p.add_argument("--hz", type=float, default=50.0, help="amostras por segundo (o cliente interpola)")
    p.add_argument("--balanco", type=float, default=3.0, help="amplitude do balanço de cabeça em graus")
    p.add_argument("--out", default="vr/passeio.csv")
    a = p.parse_args()

    n = int(a.duracao * a.hz)
    cx, cz = a.centro
    with open(a.out, "w") as f:
        f.write("t_s,x,y,z,qx,qy,qz,qw\n")
        for i in range(n):
            t = i / a.hz
            ang = 2 * math.pi * t / a.duracao
            x = cx + a.raio * math.cos(ang)
            z = cz + a.raio * math.sin(ang)
            # Olha para a tangente do círculo. No Unity, yaw 0 olha para +z e cresce no sentido horário visto de cima.
            yaw = math.atan2(-math.sin(ang), math.cos(ang)) + math.pi / 2
            pitch = math.radians(a.balanco) * math.sin(2 * math.pi * t / 4.0)   # balança a cada 4 s
            qx, qy, qz, qw = quaternion_yaw_pitch(yaw, pitch)
            f.write(f"{t:.4f},{x:.4f},{a.altura:.4f},{z:.4f},{qx:.6f},{qy:.6f},{qz:.6f},{qw:.6f}\n")
    print(f"{a.out}: {n} amostras, {a.duracao:.0f} s por volta, {a.hz:.0f} Hz")


if __name__ == "__main__":
    main()
