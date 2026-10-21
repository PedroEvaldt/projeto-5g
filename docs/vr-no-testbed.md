# VR ponta a ponta no testbed (Fase 2)

**Pergunta:** a fatia de VR funciona pelo 5G, lado a lado com os 3 vídeos, com medições em que se pode confiar?

**Resposta: sim.** O UE de VR (`ue-vr-07`, slice04, `upf4`) recebe o fluxo do PhotonPath pelo túnel, e o exporter mede em janelas de 12 s com `health=1` em todas as janelas, com os 3 vídeos tocando. Pausa e retomada funcionam. Nem o servidor de VR nem o nó são gargalo: o servidor usa ~108m dos 300m que tem e nunca foi segurado pelo limite. Daqui para frente, uma piora na VR vem do caminho que o leilão controla (a `upf4`).

## Ambiente

- 20 e 21/10/2026, namespace `pfsevaldt`. UEs, `dash-server` e `vr-app-01` no nó `blacksabbath`; as 10 UPFs no `wb-fibre-01`.
- Leilão desligado (nenhuma sessão `exp_` no tmux). As UPFs nos valores do template ou do `upf_set_resources.py`.
- PhotonPath: cópia em `photonpath/` (commit 7291c4b), perfil vr8: 8 Mbps, 60 fps, `PP_PACE_DUTY=0.5`, sem render.

## Como subir e operar

```bash
bash scripts/delete_testbeds.sh                               # apaga o testbed inteiro (pede confirmação; sem --manter-banco, o banco também)
kubectl get pvc -n pfsevaldt                                  # espere o disco do MongoDB sumir antes do build
bash scripts/build_testbeds.sh                                # sobe banco, 10 UPFs, core, assinantes 01–08, vídeo e VR (--sem-vr pula a VR)
kubectl logs -n pfsevaldt deploy/ue-vr-07 -c vr-client -f     # acompanha as janelas da VR (Ctrl+C só para de mostrar)
kubectl exec -n pfsevaldt deploy/ue-vr-07 -c vr-client -- sh -c 'kill -USR1 1'   # pausa: o tráfego de VR para em até 2 s
kubectl exec -n pfsevaldt deploy/ue-vr-07 -c vr-client -- sh -c 'kill -USR2 1'   # retoma
```

Para reiniciar só o UE de VR (por exemplo, depois de mudar o exporter):

```bash
kubectl create configmap vr-exporter -n pfsevaldt --from-file=vr/vr_exporter.py --dry-run=client -o yaml | kubectl apply -f -   # atualiza o ConfigMap que já existe
kubectl delete deployment ue-vr-07 -n pfsevaldt               # derruba o UE de VR
kubectl wait --for=delete pod -l app=ue-vr-07 -n pfsevaldt --timeout=60s   # espera o pod antigo sumir (até ~30 s)
bash client_scripts/vrclients-script.sh                       # recria o ue-vr-07
```

Três cuidados que a prática mostrou:

- **Sem `kubectl scale`:** o usuário `pfsevaldt` não tem permissão no sub-recurso `deployments/scale`.
- **Sem `rollout restart` nem reaplicar o script com o pod de pé:** com 1 réplica, o Kubernetes sobe o pod novo antes de matar o antigo, e o IMSI 07 fica registrado duas vezes por até 30 s (o `sleep` e o UERANSIM ignoram o TERM).
- **`sh -c 'kill ...'`:** a imagem `python:3.12-slim` não tem o programa `kill`, só o do `sh`.

## Como funciona

| Peça | Arquivo | O que faz |
| --- | --- | --- |
| Servidor | `client_scripts/vr-server.yaml` | `vr-app-01`: 300m e 256Mi Guaranteed, NET_ADMIN, UDP 9100 (fluxo) e TCP 9101 (`/healthz`, `/metrics`) |
| Lançador | `vr/pp_launcher.py` | troca o `photon_clock.buffers` por `SO_RCVBUFFORCE`/`SO_SNDBUFFORCE` e roda o script pedido, sem editar `photonpath/` |
| UE | `client_scripts/vrclients-script.sh` | `ue-vr-07`: containers `gnb`, `ue` e `vr-client` (NET_ADMIN, porta 9200) |
| Exporter | `vr/vr_exporter.py` | PID 1 do `vr-client`: roda o cliente em janelas, escreve a linha `[VR]` e publica `/metrics` |

**Buffers.** O `blacksabbath` tem `rmem_max` no padrão (212992 bytes). O PhotonPath pede 8 MB; pela via comum, o kernel cortaria o pedido para esse teto. Com NET_ADMIN, a via forçada ignora o teto, e o kernel concede o dobro do pedido: `sock_rcvbuf_bytes = 16777216`. O banner do servidor confirma: `rcvbuf=16384 kB sndbuf=16384 kB kernel_stamp=yes`.

**Roteamento.** O UERANSIM cria a regra `from <IP do UE> lookup rt_uesimtun0`. O cliente usa `--bind` com o IP do `uesimtun0`, então o tráfego de VR vai pelo túnel sem rota manual, e o DNS continua pela rede do pod.

**Exporter.** A cada ciclo: lê o IP do `uesimtun0`, roda o cliente por 12 s (2 s de aquecimento) pelo lançador, lê o JSON e escreve uma linha no formato abaixo (números da linha de base, só para ilustrar):

```
[T+13.2s] [VR] m2p_p95_ms=12.97 net_p95_ms=1.84 hold_p95_ms=... loss_pct=0.0 cont_pct=99.89 thr_mbps=8.06 ctx_inv=0 health=1
```

- **`health`** mede o instrumento, não a rede. Vale 1 quando o cliente rodou com carimbo de tempo do kernel, buffer de 16 MB e nenhum descarte nos sockets do pod (`dl_sock_drops` e `udp_rcvbuf_errors` em 0). Perda e continuidade ficam fora, porque são o efeito que o leilão vai mexer.
- **Cliente que não mediu** (DNS, erro) gera `falha_cliente ... health=0`. Uma **janela sem quadros** pergunta ao `/healthz` do servidor: fora do ar dá `servidor_fora_do_ar health=0` (falha do testbed); de pé dá `sem_quadros loss_pct=100` (a fatia sem serviço).
- **Sinais:** USR1 encerra a janela em andamento (descartada, sem linha) e para de gerar tráfego. O servidor para de mandar quando a sessão expira (2 s), porque o cliente morto não manda o BYE. USR2 retoma. TERM encerra.
- **Erro não previsto** na medida derruba o processo (código 1), e o Kubernetes reinicia o container (aparece em RESTARTS), em vez de o `/metrics` seguir servindo a última janela.

## Linha de base (rodada manual de 30 s, 20/10/2026)

Cliente rodado à mão no `vr-client`, antes do exporter. Não foi conferido se os vídeos estavam tocando nessa rodada.

| Medida | Valor |
| --- | --- |
| Quadros recebidos / nas estatísticas | 1800 / 1499 |
| Perda de pacotes | 0 |
| Vazão (conteúdo) | 8,059 Mbps |
| Continuidade | 99,89% (efeito de borda; nunca dá 100) |
| `net_rtt` p50 / p95 / p99 / máx | 1,56 / 1,84 / 2,41 / 10,58 ms |
| `m2p` p50 / p95 | 10,96 / 12,97 ms |
| `hold` médio | 9,56 ms |
| `jitter_net` | 76 µs |
| Saúde | `sock_rcvbuf_bytes` 16777216, descartes 0, `ctx_inv` 0, deriva do relógio 4,9 µs |

- **O `m2p` é quase todo `hold`**, o tempo dentro do servidor: a espera pelo próximo quadro mais o espaçamento dos pacotes (7 rajadas de 2 pacotes a cada 1,19 ms, em 8,3 ms dos 16,7 ms do quadro). O que mostra a rede é o `net_rtt`. Em loopback, sem rede nenhuma, o `net_p95` dá 0,02–0,05 ms; no testbed, 1,84 ms.
- **No túnel:** 25200 pacotes recebidos em 30 s (14 por quadro × 60 fps), de 1228 B cada (1200 de conteúdo + UDP/IP). São ~8,25 Mbps em IP e **~8,5 Mbps com o GTP-U** entre a `upf4` e o gNB. Na subida, 7500 poses de 156 B e 1 BYE de 56 B.

## Capacidade (10 min com os 3 vídeos, 20/10/2026)

Sem metrics-server, a CPU foi lida por um amostrador rodado dentro do `vr-app-01`: o `/proc/stat` mostra o nó inteiro (não é isolado por container), e o `/sys/fs/cgroup/cpu.stat` mostra só o container do servidor.

```bash
kubectl exec -i -n pfsevaldt deploy/vr-app-01 -- python3 - <<'PY'   # 10 linhas, uma por minuto: CPU do nó, do servidor e vezes em que o limite segurou o servidor
import os, time
def no():                                   # CPU do nó inteiro: o /proc/stat não é isolado por container
    v = list(map(int, open("/proc/stat").readline().split()[1:]))
    return sum(v), v[3] + v[4]              # total e ocioso (idle + iowait)
def srv():                                  # CPU só deste container, pelo cgroup
    d = dict(l.split() for l in open("/sys/fs/cgroup/cpu.stat"))
    return int(d["usage_usec"]), int(d.get("nr_throttled", 0)), int(d.get("throttled_usec", 0))
print(f"núcleos do nó: {os.cpu_count()}", flush=True)
t0, (tot0, oc0), (u0, n0, th0) = time.time(), no(), srv()
for _ in range(10):
    time.sleep(60)
    t1, (tot1, oc1), (u1, n1, th1) = time.time(), no(), srv()
    print(f"{time.strftime('%H:%M:%S')}  nó={100 * (1 - (oc1 - oc0) / (tot1 - tot0)):.0f}%  "
          f"servidor={(u1 - u0) / 1000 / (t1 - t0):.0f}m  throttled={n1 - n0}x/{(th1 - th0) / 1000:.0f}ms", flush=True)
    t0, tot0, oc0, u0, n0, th0 = t1, tot1, oc1, u1, n1, th1
PY
```

| Medida | Resultado |
| --- | --- |
| CPU do nó `blacksabbath` | 8–9% em todos os minutos |
| CPU do `vr-app-01` | ~108m, de 300m |
| Throttling do `vr-app-01` | 0 em todos os minutos |
| Janelas da VR | todas com `health=1` |
| Vídeos | tocando do começo ao fim |

Não foram registrados os contadores `frames_late_total` do servidor nem a faixa do `net_p95` nesses 10 min. Com throttling zero, o servidor não tinha como atrasar quadros por falta de CPU.

**Conclusão:** o servidor fica no `blacksabbath`. Os 108m são de **uma** sessão; o servidor divide o mesmo ciclo entre as sessões, então dois UEs de VR no mesmo `vr-app-01` chegariam perto de ~200m.

## Reconstrução do testbed (21/10/2026)

Ao reiniciar o `ue-vr-07`, o AMF recusou o registro com `FIVEG_SERVICES_NOT_ALLOWED`, e o UERANSIM desistiu de tentar. A causa provável era o IMSI 07 fora do MongoDB: o `build_testbeds.sh` só registrava os IMSIs 01–06. Isso não foi conferido no banco antes da reconstrução. O build e o delete passaram a incluir a VR, e o ciclo completo (`delete_testbeds.sh` + `build_testbeds.sh`) deixou os 8 assinantes `ok`, o `ue-vr-07` registrado e as janelas com `health=1`.

## Limites conhecidos

- **Artefato de perda de até ~0,15% por janela.** No fim da janela, o cliente manda o BYE, e o servidor apaga a sessão na hora. Se isso cai no meio do envio espaçado de um quadro, o resto do quadro não sai, e o cliente conta como perda. Em testes locais, a perda só apareceu com exatamente 1 quadro parcial, sem descarte em socket. Um quadro parcial em 10 s de estatísticas (8400 pacotes) dá no máximo ~0,15%.
- **Cobertura de ~77% do tempo.** Cada ciclo dura ~13 s (12 s de janela mais ~1 s entre janelas), e as estatísticas cobrem 10 s.
- **Na pausa, o `/metrics` mantém a última janela**, com `vr_paused 1`. Quem ler as métricas tem de olhar o `vr_paused` antes.
- **Continuidade nunca dá 100%** (~99,9%, efeito de borda), por isso ela não entra no `health`.

## O que isso muda no plano

- **Fase 3 (SLA da VR):** o limite de perda tem de ficar bem acima de 0,15%, ou a perda deve vir do contador do servidor (`frame_packets_total`), que não tem o artefato.
- **Piso de banda da VR:** a `upf4` precisa de ~8,5 Mbps no túnel (com GTP-U), e não de 8.
- **Fase 4 (Prometheus):** o `ue-vr-07` ainda não tem Service; o Prometheus vai precisar de um Service para a porta `metrics` (9200) e de um ServiceMonitor, e as consultas têm de filtrar `vr_paused`.
