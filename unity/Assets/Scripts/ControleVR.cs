// O componente principal: vai na Camera da cena. Lê a configuração do ambiente, põe a câmera na pose
// (da rede ou do passeio), captura o quadro no fim de cada frame e escreve um resumo a cada 5 s.
//
// Variáveis de ambiente (todas opcionais):
//   VR_MODO          rede (padrão) | autonomo. Em "rede", sem pose por 2 s, cai no passeio sozinho e avisa.
//   VR_PASSEIO       caminho do CSV do passeio (padrão: StreamingAssets/passeio.csv)
//   VR_LARGURA, VR_ALTURA, VR_FPS     1280, 720, 60
//   VR_ENCODER       nvenc (padrão) | x264 | jpeg | nenhum
//   VR_BITRATE_KBPS  7000
//   VR_POSE_PORT     9300 (onde as poses chegam)
//   VR_RENDER_DEST   127.0.0.1:9301 (para onde os quadros vão)
//   VR_PNG_S         0 (> 0 salva um PNG a cada tantos segundos em VR_PNG_DIR, padrão /saida)
//   VR_READBACK      async (padrão) | sync. sync espera a GPU terminar o quadro na hora: menos latência, alguns ms de espera

using System;
using System.Collections.Generic;
using System.IO;
using UnityEngine;

[RequireComponent(typeof(Camera))]
public class ControleVR : MonoBehaviour
{
    public string modo = "rede";
    public string passeioCaminho = "";

    ReceptorPoses receptor;
    SaidaQuadros saida;
    Passeio passeio;
    Camera cam;
    PoseVR poseAtual;
    bool usandoPasseio, avisouSemPasseio;
    float tPasseio;
    double tPoseAplicada;
    int quadros;
    readonly List<float> frameMs = new List<float>();
    double tRelatorio;

    static string Env(string nome, string padrao) { var v = Environment.GetEnvironmentVariable(nome); return string.IsNullOrEmpty(v) ? padrao : v; }

    void Awake()
    {
        // Gravando um passeio no Editor, a câmera é do GravadorPasseio. Sem isto, o resto do Awake (que roda mesmo com
        // o componente desmarcado) desligaria a câmera, e o Update a reposicionaria a cada frame.
        if (Application.isEditor && GetComponent<GravadorPasseio>() != null) { enabled = false; return; }

        modo = Env("VR_MODO", modo);
        passeioCaminho = Env("VR_PASSEIO", string.IsNullOrEmpty(passeioCaminho) ? Path.Combine(Application.streamingAssetsPath, "passeio.csv") : passeioCaminho);
        int fps = int.Parse(Env("VR_FPS", "60"));
        QualitySettings.vSyncCount = 0;
        Application.targetFrameRate = fps;
        Application.runInBackground = true;

        var cam = GetComponent<Camera>();
        saida = gameObject.AddComponent<SaidaQuadros>();
        saida.largura = int.Parse(Env("VR_LARGURA", "1280"));
        saida.altura = int.Parse(Env("VR_ALTURA", "720"));
        saida.codificador = Env("VR_ENCODER", "nvenc");
        saida.bitrateKbps = int.Parse(Env("VR_BITRATE_KBPS", "7000"));
        saida.destino = Env("VR_RENDER_DEST", "127.0.0.1:9301");
        saida.pngCadaS = float.Parse(Env("VR_PNG_S", "0"), System.Globalization.CultureInfo.InvariantCulture);
        saida.pngDir = Env("VR_PNG_DIR", "/saida");
        saida.readbackSincrono = Env("VR_READBACK", "async") == "sync";
        saida.Iniciar(cam);
        // A câmera não renderiza sozinha: o Update chama cam.Render() depois de aplicar a pose. Assim o
        // render acontece sempre, inclusive com -batchmode, em que o player pula as câmeras automáticas.
        cam.enabled = false;
        this.cam = cam;

        if (modo == "rede") receptor = gameObject.AddComponent<ReceptorPoses>();
        if (File.Exists(passeioCaminho))
        {
            passeio = Passeio.Carregar(passeioCaminho);
            Debug.Log($"[controle] passeio {passeioCaminho}: {passeio.Amostras} amostras, {passeio.Duracao:F1} s por volta");
        }
        else if (modo == "autonomo") Debug.LogError($"[controle] modo autonomo sem passeio: {passeioCaminho} não existe");
        Debug.Log($"[controle] modo={modo} fps={fps} unity={Application.unityVersion} gpu={SystemInfo.graphicsDeviceName} api={SystemInfo.graphicsDeviceType}");
        tRelatorio = ReceptorPoses.Agora;
    }

    void Update()
    {
        double agora = ReceptorPoses.Agora;
        tPoseAplicada = agora;                                 // o custo do render conta a partir daqui
        frameMs.Add(Time.unscaledDeltaTime * 1000f);

        PoseVR p = default;
        bool rede = receptor != null && receptor.TentarLer(out p) && agora - p.tChegada < 2.0;
        if (rede)
        {
            if (usandoPasseio) Debug.Log("[controle] as poses voltaram");
            usandoPasseio = false;
            poseAtual = p;
            transform.SetPositionAndRotation(p.posicao, p.rotacao.normalized);
        }
        else
        {
            if (passeio == null)
            {
                if (!avisouSemPasseio) { Debug.LogWarning("[controle] sem poses e sem passeio: nada para renderizar"); avisouSemPasseio = true; }
                return;
            }
            if (!usandoPasseio) Debug.Log(modo == "rede" ? "[controle] sem poses há 2 s: tocando o passeio sozinho" : "[controle] tocando o passeio");
            usandoPasseio = true;
            tPasseio += Time.unscaledDeltaTime;
            passeio.Amostra(tPasseio, out var pos, out var rot);
            transform.SetPositionAndRotation(pos, rot);
            poseAtual = new PoseVR { seq = 0, tClientNs = 0, recvNs = 0, posicao = pos, rotacao = rot, tChegada = agora };
        }

        cam.Render();                                          // renderiza esta pose no RenderTexture, agora
        quadros++;
        saida.Capturar(poseAtual, tPoseAplicada);              // o readback espera a GPU: o tempo dela entra no render_us

        if (agora - tRelatorio >= 5.0) { Relatorio(agora - tRelatorio); tRelatorio = agora; }
    }

    static float Pct(List<float> v, float q)
    {
        if (v.Count == 0) return 0f;
        v.Sort();
        float k = (v.Count - 1) * q;
        int lo = (int)k, hi = Math.Min(lo + 1, v.Count - 1);
        return v[lo] + (v[hi] - v[lo]) * (k - lo);
    }

    void Relatorio(double dt)
    {
        var render = saida.ColherRenderMs();
        var f = new List<float>(frameMs); frameMs.Clear();
        Debug.Log($"[medidor] fps={quadros / dt:F1} frame_ms p50={Pct(f, .5f):F2} p95={Pct(f, .95f):F2} " +
                  $"render_ms p50={Pct(render, .5f):F2} p95={Pct(render, .95f):F2} " +
                  $"lidos={saida.quadrosLidos} enviados={saida.quadrosEnviados} descartados={saida.quadrosDescartados} " +
                  $"cortados={saida.quadrosCortados} fragmentos={saida.fragmentosDescartados} erro_readback={saida.erroReadback} kB_medio={(saida.quadrosEnviados > 0 ? saida.bytesEnviados / 1000.0 / saida.quadrosEnviados : 0):F1} " +
                  $"poses={(receptor != null ? receptor.posesRecebidas : 0)} fonte={(usandoPasseio ? "passeio" : "rede")}");
        quadros = 0;
    }
}
