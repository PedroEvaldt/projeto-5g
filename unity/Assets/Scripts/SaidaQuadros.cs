// Tira o quadro renderizado da GPU, codifica e manda ao PhotonPath no formato PPRF.
//
// Caminho de cada quadro:
//   câmera -> RenderTexture -> AsyncGPUReadback (RGBA, de baixo para cima) -> fila -> thread escritora
//     -> "nvenc"/"x264": stdin de um ffmpeg filho, que devolve H.264 pelo stdout; a thread leitora separa
//        as unidades de acesso pelo AUD (00 00 00 01 09) e manda uma por datagrama PPRF
//     -> "jpeg": ImageConversion.EncodeArrayToJPG na própria thread (sem ffmpeg; plano B no Windows)
//     -> "nenhum": PPRF sem bytes (só para medir o render)
//
// PPRF = !4sQQQII: magic "PPRF", pose_seq, t_pose_ns, recv_ns, render_us, enc_bytes; depois os bytes.
// render_us conta do fim do render do quadro (WaitForEndOfFrame) até o datagrama sair: readback,
// cópia, codificação e fila, tudo o que o PhotonPath vai ver como custo do render.
//
// Precisa do backend Mono (System.Diagnostics.Process não existe no IL2CPP).

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Threading;
using UnityEngine;
using UnityEngine.Experimental.Rendering;
using UnityEngine.Rendering;
using Debug = UnityEngine.Debug;

public class SaidaQuadros : MonoBehaviour
{
    public int largura = 1280, altura = 720;
    public string codificador = "nvenc";              // nvenc | x264 | jpeg | nenhum
    public int bitrateKbps = 7000;                    // abaixo dos 7,74 Mbps que cabem em 14 x 1152 B a 60 fps
    public string destino = "127.0.0.1:9301";
    public float pngCadaS = 0f;                       // > 0: salva um PNG a cada tantos segundos, para conferir o modelo
    public bool readbackSincrono = false;             // true: espera a GPU terminar o quadro na hora (menos latência, alguns ms de espera)
    public string pngDir = "/saida";

    // Contadores para o medidor (lidos da thread principal; int é atômico)
    public int quadrosLidos, quadrosDescartados, quadrosEnviados, quadrosCortados, erroReadback;
    public long bytesEnviados;
    readonly List<float> renderMs = new List<float>();
    readonly object travaMetricas = new object();

    struct Pendente { public PoseVR pose; public double tInicio; public byte[] pixels; }

    RenderTexture rt;
    readonly Queue<Pendente> filaEscrita = new Queue<Pendente>();
    readonly Queue<Pendente> filaOrdem = new Queue<Pendente>();   // quadros dentro do ffmpeg, na ordem de entrada
    readonly Stack<byte[]> pool = new Stack<byte[]>();
    readonly object trava = new object();
    readonly AutoResetEvent sinal = new AutoResetEvent(false);
    Process ffmpeg;
    Stream ffIn;
    Thread escritor, leitor;
    UdpClient udp;
    IPEndPoint dest;
    volatile bool rodando;
    double tUltimoPng = -1e9;
    const int MaxFila = 2;                            // mais que isso é atraso acumulado, não vazão

    public void Iniciar(Camera cam)
    {
        rt = new RenderTexture(largura, altura, 24, RenderTextureFormat.ARGB32) { antiAliasing = 1 };
        rt.Create();
        cam.targetTexture = rt;

        var h = destino.Split(':');
        dest = new IPEndPoint(IPAddress.Parse(h[0]), int.Parse(h[1]));
        udp = new UdpClient();
        udp.Client.SendBufferSize = 8 << 20;

        rodando = true;
        if (codificador == "nvenc" || codificador == "x264") AbrirFfmpeg();
        escritor = new Thread(LacoEscritor) { IsBackground = true, Name = "escritor" };
        escritor.Start();
        Debug.Log($"[saida] {largura}x{altura} codificador={codificador} {bitrateKbps} kbps readback={(readbackSincrono ? "sync" : "async")} -> {destino}" +
                  (pngCadaS > 0 ? $" png a cada {pngCadaS} s em {pngDir}" : ""));
    }

    void AbrirFfmpeg()
    {
        int buf = Math.Max(50, bitrateKbps / 60 * 2);                     // ~2 quadros de buffer: CBR de verdade
        string enc = codificador == "nvenc"
            ? $"-c:v h264_nvenc -preset p1 -tune ull -rc cbr -b:v {bitrateKbps}k -maxrate {bitrateKbps}k -bufsize {buf}k " +
              "-g 999999 -bf 0 -intra-refresh 1 -zerolatency 1 -delay 0"      // -delay 0: sem segurar quadros dentro do NVENC
            : $"-c:v libx264 -preset ultrafast -tune zerolatency -b:v {bitrateKbps}k -maxrate {bitrateKbps}k -bufsize {buf}k " +
              "-x264-params nal-hrd=cbr:keyint=infinite:intra-refresh=1:threads=2";
        // -fflags nobuffer e -flush_packets 1: cada quadro sai assim que é codificado, sem acumular no ffmpeg
        string args = $"-hide_banner -loglevel error -nostats -fflags nobuffer -f rawvideo -pix_fmt rgba -s {largura}x{altura} -r 60 -i - " +
                      $"-vf vflip {enc} -bsf:v h264_metadata=aud=insert -flush_packets 1 -f h264 -";
        ffmpeg = new Process
        {
            StartInfo = new ProcessStartInfo("ffmpeg", args)
            {
                UseShellExecute = false, RedirectStandardInput = true, RedirectStandardOutput = true,
                RedirectStandardError = true, CreateNoWindow = true,
            }
        };
        ffmpeg.ErrorDataReceived += (s, e) => { if (e.Data != null) Debug.LogWarning("[ffmpeg] " + e.Data); };
        ffmpeg.Start();
        ffmpeg.BeginErrorReadLine();
        ffIn = ffmpeg.StandardInput.BaseStream;
        leitor = new Thread(LacoLeitor) { IsBackground = true, Name = "leitor" };
        leitor.Start();
        Debug.Log("[saida] ffmpeg " + args);
    }

    /// <summary>Chamar depois de WaitForEndOfFrame: o RenderTexture já tem o quadro desta pose.</summary>
    public void Capturar(PoseVR pose, double tInicio)
    {
        if (readbackSincrono)
        {
            var r = AsyncGPUReadback.Request(rt, 0, TextureFormat.RGBA32);
            r.WaitForCompletion();                        // espera a GPU: tira 2 a 3 quadros de latência, custa a espera
            AoLer(r, pose, tInicio);
            return;
        }
        AsyncGPUReadback.Request(rt, 0, TextureFormat.RGBA32, r => AoLer(r, pose, tInicio));
    }

    void AoLer(AsyncGPUReadbackRequest r, PoseVR pose, double tInicio)   // na thread principal, alguns quadros depois
    {
        if (r.hasError) { erroReadback++; return; }
        quadrosLidos++;
        byte[] px;
        lock (trava)
        {
            if (filaEscrita.Count >= MaxFila) { quadrosDescartados++; return; }
            px = pool.Count > 0 ? pool.Pop() : new byte[largura * altura * 4];
        }
        r.GetData<byte>().CopyTo(px);
        lock (trava) filaEscrita.Enqueue(new Pendente { pose = pose, tInicio = tInicio, pixels = px });
        sinal.Set();
    }

    void LacoEscritor()
    {
        while (rodando)
        {
            Pendente p;
            bool tem;
            lock (trava)
            {
                tem = filaEscrita.Count > 0;
                p = tem ? filaEscrita.Dequeue() : default;
            }
            if (!tem) { sinal.WaitOne(100); continue; }
            try
            {
                SalvarPngSePrecisar(p.pixels);
                switch (codificador)
                {
                    case "jpeg":
                        Enviar(p, ImageConversion.EncodeArrayToJPG(p.pixels, GraphicsFormat.R8G8B8A8_UNorm, (uint)largura, (uint)altura, 0, 60));
                        break;
                    case "nenhum":
                        Enviar(p, Array.Empty<byte>());
                        break;
                    default:
                        lock (filaOrdem) filaOrdem.Enqueue(p);
                        ffIn.Write(p.pixels, 0, p.pixels.Length);
                        ffIn.Flush();
                        break;
                }
            }
            catch (Exception e) { Debug.LogError("[saida] " + e.Message); }
            lock (trava) pool.Push(p.pixels);
        }
    }

    void LacoLeitor()
    {
        // Junta o que o ffmpeg manda e corta nas fronteiras "00 00 00 01 09" (AUD): cada pedaço é um quadro.
        var saida = ffmpeg.StandardOutput.BaseStream;
        var acumulado = new byte[1 << 20];
        int usado = 0, varrido = 0;
        var pedaco = new byte[64 << 10];
        while (rodando)
        {
            int n;
            try { n = saida.Read(pedaco, 0, pedaco.Length); } catch { break; }
            if (n <= 0) break;
            if (usado + n > acumulado.Length) Array.Resize(ref acumulado, Math.Max(acumulado.Length * 2, usado + n));
            Buffer.BlockCopy(pedaco, 0, acumulado, usado, n);
            usado += n;
            int inicio = 0;
            for (int i = Math.Max(varrido - 4, 0); i + 4 < usado; i++)
            {
                if (acumulado[i] == 0 && acumulado[i + 1] == 0 && acumulado[i + 2] == 0 && acumulado[i + 3] == 1 && (acumulado[i + 4] & 0x1F) == 9)
                {
                    if (i > inicio) EmitirQuadro(acumulado, inicio, i - inicio);
                    inicio = i;
                    i += 4;
                }
            }
            if (inicio > 0) { Buffer.BlockCopy(acumulado, inicio, acumulado, 0, usado - inicio); usado -= inicio; }
            varrido = usado;
        }
        if (rodando) Debug.LogError("[saida] o ffmpeg fechou a saída");
    }

    void EmitirQuadro(byte[] b, int o, int n)
    {
        Pendente p;
        lock (filaOrdem)
        {
            if (filaOrdem.Count == 0) return;                         // não devia: o encoder devolveu mais do que entrou
            p = filaOrdem.Dequeue();
        }
        var dados = new byte[n];
        Buffer.BlockCopy(b, o, dados, 0, n);
        Enviar(p, dados);
    }

    void Enviar(Pendente p, byte[] dados)
    {
        const int MaxDatagrama = 65000 - 36;
        int n = dados.Length;
        if (n > MaxDatagrama) { n = MaxDatagrama; quadrosCortados++; }
        uint renderUs = (uint)Math.Max(0, (ReceptorPoses.Agora - p.tInicio) * 1e6);
        var pkt = new byte[36 + n];
        pkt[0] = (byte)'P'; pkt[1] = (byte)'P'; pkt[2] = (byte)'R'; pkt[3] = (byte)'F';
        BE.PutU64(pkt, 4, p.pose.seq);
        BE.PutU64(pkt, 12, p.pose.tClientNs);
        BE.PutU64(pkt, 20, p.pose.recvNs);
        BE.PutU32(pkt, 28, renderUs);
        BE.PutU32(pkt, 32, (uint)n);
        Buffer.BlockCopy(dados, 0, pkt, 36, n);
        try { udp.Send(pkt, pkt.Length, dest); } catch (Exception e) { Debug.LogWarning("[saida] envio: " + e.Message); return; }
        quadrosEnviados++;
        bytesEnviados += n;
        lock (travaMetricas) renderMs.Add(renderUs / 1000f);
    }

    void SalvarPngSePrecisar(byte[] pixels)
    {
        if (pngCadaS <= 0) return;
        double agora = ReceptorPoses.Agora;
        if (agora - tUltimoPng < pngCadaS) return;
        tUltimoPng = agora;
        Directory.CreateDirectory(pngDir);
        var png = ImageConversion.EncodeArrayToPNG(pixels, GraphicsFormat.R8G8B8A8_UNorm, (uint)largura, (uint)altura);
        File.WriteAllBytes(Path.Combine(pngDir, $"quadro_{DateTime.Now:HHmmss}.png"), png);
    }

    /// <summary>Esvazia e devolve os tempos de render acumulados desde a última chamada (ms).</summary>
    public List<float> ColherRenderMs()
    {
        lock (travaMetricas) { var c = new List<float>(renderMs); renderMs.Clear(); return c; }
    }

    void OnDestroy()
    {
        rodando = false;
        sinal.Set();
        try { ffIn?.Close(); } catch { }
        try { if (ffmpeg != null && !ffmpeg.HasExited) ffmpeg.Kill(); } catch { }
        udp?.Close();
    }
}
