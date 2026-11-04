// Recebe as poses que o photon_server repassa (ou que o vr/testar_render.py manda no teste).
//
// Formato (docs/plano-render-real.md): PPOS de 128 B como chega do cliente, mais 8 B de recv_ns.
//   cabeçalho !4sIQQI : magic "PPOS", session (u32), seq (u64), t_client_ns (u64), flags (u32)   = 28 B
//   padding   !I7f    : índice da amostra (u32), x y z, qx qy qz qw (f32)                         = 32 B
//   zeros até 128 B, depois recv_ns (u64)
// Tudo big-endian. Só a última pose interessa: o render responde sempre à mais nova.

using System;
using System.Net;
using System.Net.Sockets;
using System.Threading;
using UnityEngine;

public struct PoseVR
{
    public ulong seq, tClientNs, recvNs;
    public uint indice;
    public Vector3 posicao;
    public Quaternion rotacao;
    public double tChegada;          // Time.realtimeSinceStartup não vale fora da thread principal: usamos Stopwatch
}

public static class BE                // leitura big-endian sem depender de BinaryPrimitives
{
    public static uint U32(byte[] b, int o) => (uint)(b[o] << 24 | b[o + 1] << 16 | b[o + 2] << 8 | b[o + 3]);
    public static ulong U64(byte[] b, int o) => (ulong)U32(b, o) << 32 | U32(b, o + 4);
    public static float F32(byte[] b, int o)
    {
        var t = new byte[4] { b[o + 3], b[o + 2], b[o + 1], b[o] };   // inverte para little-endian
        return BitConverter.ToSingle(t, 0);
    }
    public static void PutU32(byte[] b, int o, uint v) { b[o] = (byte)(v >> 24); b[o + 1] = (byte)(v >> 16); b[o + 2] = (byte)(v >> 8); b[o + 3] = (byte)v; }
    public static void PutU64(byte[] b, int o, ulong v) { PutU32(b, o, (uint)(v >> 32)); PutU32(b, o + 4, (uint)v); }
}

public class ReceptorPoses : MonoBehaviour
{
    public int porta = 9300;
    public int posesRecebidas;                      // só leitura, para o medidor

    UdpClient udp;
    Thread thread;
    volatile bool rodando;
    readonly object trava = new object();
    PoseVR ultima;
    bool temPose;
    static readonly System.Diagnostics.Stopwatch relogio = System.Diagnostics.Stopwatch.StartNew();

    public static double Agora => relogio.Elapsed.TotalSeconds;

    void Awake()
    {
        var env = Environment.GetEnvironmentVariable("VR_POSE_PORT");
        if (!string.IsNullOrEmpty(env)) porta = int.Parse(env);
    }

    void OnEnable()
    {
        udp = new UdpClient(new IPEndPoint(IPAddress.Any, porta));
        udp.Client.ReceiveBufferSize = 4 << 20;
        rodando = true;
        thread = new Thread(Laco) { IsBackground = true, Name = "poses" };
        thread.Start();
        Debug.Log($"[poses] ouvindo em :{porta}");
    }

    void OnDisable()
    {
        rodando = false;
        try { udp?.Close(); } catch { }
        thread?.Join(500);
    }

    void Laco()
    {
        var de = new IPEndPoint(IPAddress.Any, 0);
        while (rodando)
        {
            byte[] b;
            try { b = udp.Receive(ref de); }
            catch (Exception) { if (rodando) continue; else break; }
            if (b.Length < 28 + 32 || b[0] != (byte)'P' || b[1] != (byte)'P' || b[2] != (byte)'O' || b[3] != (byte)'S') continue;

            var p = new PoseVR
            {
                seq = BE.U64(b, 8),
                tClientNs = BE.U64(b, 16),
                indice = BE.U32(b, 28),
                posicao = new Vector3(BE.F32(b, 32), BE.F32(b, 36), BE.F32(b, 40)),
                rotacao = new Quaternion(BE.F32(b, 44), BE.F32(b, 48), BE.F32(b, 52), BE.F32(b, 56)),
                recvNs = b.Length >= 136 ? BE.U64(b, b.Length - 8) : 0,   // os 8 B finais são o recv_ns anexado
                tChegada = Agora,
            };
            lock (trava)
            {
                if (!temPose || p.seq >= ultima.seq) { ultima = p; temPose = true; }
                posesRecebidas++;
            }
        }
    }

    /// <summary>Última pose recebida. Devolve false se nenhuma chegou ainda.</summary>
    public bool TentarLer(out PoseVR p)
    {
        lock (trava) { p = ultima; return temPose; }
    }
}
