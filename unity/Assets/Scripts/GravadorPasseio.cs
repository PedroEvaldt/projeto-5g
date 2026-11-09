// Só no Editor: voo livre com WASD + mouse (Q/E sobe e desce, Shift corre), gravando a pose da câmera a `hz` por
// segundo. Aperte R para começar e parar; o CSV (formato em Passeio.cs) sai no caminho indicado. Termine perto de onde
// começou, olhando para a mesma direção, para o laço emendar sem salto.
//
// Vai na Main Camera, junto do ControleVR, que fica desligado enquanto este componente estiver lá. Fora do Editor,
// quem se desliga é este, para um gravador esquecido na cena não atrapalhar o player.

using System.Globalization;
using System.IO;
using UnityEngine;

public class GravadorPasseio : MonoBehaviour
{
    public string caminho = "passeio.csv";
    public float hz = 50f;
    public float velocidade = 1.4f;                     // m/s, passo de quem anda
    public float sensibilidade = 2f;
    public bool travarCursor = true;                    // prende o mouse, para girar sem bater na borda da tela (Esc solta)

    StreamWriter w;
    float t0, proximo;
    float yaw, pitch;

    void Start()
    {
        if (!Application.isEditor) { enabled = false; return; }
        var e = transform.eulerAngles;
        yaw = e.y;
        pitch = Mathf.DeltaAngle(0f, e.x);              // eulerAngles dá 350 para -10: sem isto, a câmera saltaria no 1º frame
        if (travarCursor) { Cursor.lockState = CursorLockMode.Locked; Cursor.visible = false; }
    }

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
