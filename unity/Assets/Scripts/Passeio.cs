// A trajetória do usuário simulado: gravar no Editor e reproduzir no player.
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

/// <summary>
/// Só no Editor: voo livre com WASD + mouse (Q/E sobe e desce, Shift corre), gravando a pose da câmera
/// a `hz` por segundo. Aperte R para começar e parar; o CSV sai no caminho indicado. Termine perto de onde
/// começou, olhando para a mesma direção, para o laço emendar sem salto.
/// </summary>
public class GravadorPasseio : MonoBehaviour
{
    public string caminho = "passeio.csv";
    public float hz = 50f;
    public float velocidade = 1.4f;                     // m/s, passo de quem anda
    public float sensibilidade = 2f;

    StreamWriter w;
    float t0, proximo;
    float yaw, pitch;

    void Start() { var e = transform.eulerAngles; yaw = e.y; pitch = e.x; }

    void Update()
    {
        yaw += Input.GetAxis("Mouse X") * sensibilidade;
        pitch = Mathf.Clamp(pitch - Input.GetAxis("Mouse Y") * sensibilidade, -80f, 80f);
        transform.rotation = Quaternion.Euler(pitch, yaw, 0f);
        var v = velocidade * (Input.GetKey(KeyCode.LeftShift) ? 3f : 1f);
        var d = new Vector3(Input.GetAxis("Horizontal"), 0f, Input.GetAxis("Vertical"));
        var plano = Quaternion.Euler(0f, yaw, 0f) * d;                                  // anda no plano, não na direção do olhar
        if (Input.GetKey(KeyCode.Q)) plano.y -= 1f;
        if (Input.GetKey(KeyCode.E)) plano.y += 1f;
        transform.position += plano * v * Time.deltaTime;

        if (Input.GetKeyDown(KeyCode.R))
        {
            if (w == null)
            {
                w = new StreamWriter(caminho);
                w.WriteLine("t_s,x,y,z,qx,qy,qz,qw");
                t0 = Time.time; proximo = 0f;
                Debug.Log($"[gravador] gravando em {caminho}; R de novo para parar");
            }
            else { w.Close(); w = null; Debug.Log("[gravador] parado"); }
        }
        if (w != null && Time.time - t0 >= proximo)
        {
            var ci = CultureInfo.InvariantCulture;
            var p = transform.position; var q = transform.rotation;
            w.WriteLine(string.Format(ci, "{0:F4},{1:F4},{2:F4},{3:F4},{4:F6},{5:F6},{6:F6},{7:F6}",
                                      Time.time - t0, p.x, p.y, p.z, q.x, q.y, q.z, q.w));
            proximo += 1f / hz;
        }
    }

    void OnDisable() { w?.Close(); }
}
