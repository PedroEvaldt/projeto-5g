// A trajetória do usuário simulado, reproduzida no player (a gravação fica no GravadorPasseio.cs).
//
// CSV: t_s,x,y,z,qx,qy,qz,qw (o mesmo que vr/gerar_passeio.py escreve e que vr/testar_render.py lê).
// Laço fechado: depois da última amostra volta à primeira.

using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using UnityEngine;

public class Passeio
{
    readonly List<float> t = new List<float>();
    readonly List<Vector3> pos = new List<Vector3>();
    readonly List<Quaternion> rot = new List<Quaternion>();
    public float Duracao { get; private set; }
    public int Amostras => t.Count;

    public static Passeio Carregar(string caminho)
    {
        var p = new Passeio();
        var ci = CultureInfo.InvariantCulture;
        foreach (var linha in File.ReadAllLines(caminho))
        {
            if (linha.StartsWith("t_s") || linha.Trim().Length == 0) continue;
            var c = linha.Split(',');
            if (c.Length < 8) continue;
            p.t.Add(float.Parse(c[0], ci));
            p.pos.Add(new Vector3(float.Parse(c[1], ci), float.Parse(c[2], ci), float.Parse(c[3], ci)));
            p.rot.Add(new Quaternion(float.Parse(c[4], ci), float.Parse(c[5], ci), float.Parse(c[6], ci), float.Parse(c[7], ci)));
        }
        if (p.t.Count < 2) throw new Exception($"{caminho}: precisa de pelo menos 2 amostras");
        p.Duracao = p.t[p.t.Count - 1] + (p.t[p.t.Count - 1] - p.t[p.t.Count - 2]);   // a última emenda na primeira
        return p;
    }

    /// <summary>Pose interpolada no tempo tempo (segundos), em laço.</summary>
    public void Amostra(float tempo, out Vector3 posicao, out Quaternion rotacao)
    {
        tempo = Mathf.Repeat(tempo, Duracao);
        int lo = 0, hi = t.Count - 1;
        while (lo < hi)                                   // último t <= tempo
        {
            int m = (lo + hi + 1) / 2;
            if (t[m] <= tempo) lo = m; else hi = m - 1;
        }
        int prox = lo + 1 < t.Count ? lo + 1 : 0;
        float tProx = lo + 1 < t.Count ? t[prox] : Duracao;
        float f = tProx == t[lo] ? 0f : (tempo - t[lo]) / (tProx - t[lo]);
        posicao = Vector3.Lerp(pos[lo], pos[prox], f);
        rotacao = Quaternion.Slerp(rot[lo], rot[prox], f);
    }
}
