# Ambiente depois da Fase 9: 4 UPFs e VR em sessão contínua

Duas mudanças no testbed, pedidas pelo Pedro em 16/11/2026, depois da tag `v2.2-fase9`. Os commits levam o prefixo
`fase10`. Para voltar ao ambiente das Fases 8 e 9, basta a tag `v2.2-fase9`.

**Atenção:** as duas mudam o ambiente. Resultado novo não se compara direto com as Fases 8 e 9: é preciso uma
referência medida neste ambiente (como os controles da Fase 9).

## 1. O testbed só tem as `upf1` a `upf4`

As `upf5` a `upf10` ficavam de pé, paradas, reservadas para um teste de interferência que não foi feito. Saíram, e com
elas o `smf2`, que só atendia as `upf8` a `upf10`.

| Arquivo | O que mudou |
| --- | --- |
| `5Gcore_files/deploy-upf.sh` | `COUNT=4` |
| `5Gcore_files/recent-op5g.yaml` | o `smf` só lista as `upf1`–`upf4` (PFCP, sub-redes e `s_nssai` das fatias 1 a 4); o bloco do `smf2` saiu |
| `spade_agents/upf_set_resources.py` | devolve ao estado inicial as `upf1`–`upf4` (antes, `upf1`–`upf9`) |
| `scripts/delete_testbeds.sh` | continua apagando até a `upf10` e o `smf2`, para limpar um testbed antigo |

- **O que ficou como estava:** o AMF e o NSSF ainda anunciam as fatias 5 a 10 (`sd` 000005 a 000010). Sem UPF nem SMF
  para elas, nenhum UE as usa. Tirá-las pede reiniciar o AMF, o que derruba todos os UEs, e não muda nada no teste.
- **No cluster (16/11/2026):** os deployments, services, configmaps e servicemonitors das `upf5`–`upf10` e do `smf2`
  foram apagados, o ConfigMap do `smf` foi trocado e o pod do `smf` recriado. As 4 UPFs se associaram de novo em
  seguida, e o UE de VR foi recriado.

```bash
for i in 5 6 7 8 9 10; do kubectl delete -n pfsevaldt deployment/upf$i service/upf$i service/upf$i-gtp configmap/upf$i servicemonitor/upf$i --ignore-not-found; done   # apaga as 6 UPFs paradas
kubectl delete -n pfsevaldt deployment/smf2 service/smf2 configmap/smf2 servicemonitor/smf2 --ignore-not-found   # apaga o smf2
kubectl delete pod -n pfsevaldt -l app=smf                                                                       # recria o smf, depois de aplicar o ConfigMap novo dele
kubectl logs -n pfsevaldt deploy/smf | grep "PFCP associated"                                                    # têm de aparecer as 4 UPFs
```

## 2. A VR é uma sessão contínua

**Antes:** o `vr_exporter.py` subia um `photon_client` novo a cada janela. Cada janela era uma sessão nova, com a
numeração de volta ao começo, 2 s de aquecimento fora da medida (10 s medidos de cada 12) e ~0,7 s sem tráfego até a
seguinte. Uma janela a cada ~12,7 s.

**Agora:** um cliente só, do começo ao fim de cada trecho em que a VR está ativa. Ele imprime um resumo a cada 12 s, e
o exporter publica cada um como uma janela. O tráfego não para entre as janelas, e todos os 12 s são medidos. O
aquecimento de 2 s só existe no começo da sessão (a 1ª janela sai aos 14 s).

| Arquivo | O que mudou |
| --- | --- |
| `photonpath/src/photon_client.py` | opção `--report-interval` (`PP_REPORT_S`): uma sessão, uma linha JSON por intervalo; com ela, `--duration 0` roda até o processo ser encerrado. Registrado no `photonpath/ORIGEM.txt`. Sem a opção, nada muda |
| `vr/vr_exporter.py` | roda o cliente uma vez por trecho ativo e lê as janelas da saída dele. A pausa (USR1) encerra a sessão; a retomada (USR2) abre outra |

Detalhes que importam para ler os números:

- **A janela fecha num quadro inteiro.** O intervalo termina no primeiro pacote de um quadro novo depois dos 12 s. Um
  quadro dividido entre duas janelas contaria como perda nas duas.
- **A perda conta os quadros desde o fim da janela anterior.** Um quadro perdido inteiro na fronteira entra na conta
  (antes, o cliente não tinha como vê-lo).
- **Sem tráfego, a janela fecha 0,5 s depois do prazo**, e sai como `sem_quadros` (ou `servidor_fora_do_ar`), como antes.
- **A continuidade fica em ~99,8–99,9%**, e não em 100%: falta o intervalo entre o último pacote de uma janela e o
  primeiro da seguinte. O SLA pede 98%.
- **Se o túnel cair ou for refeito**, o cliente ficaria preso ao IP antigo. Numa janela sem quadros, o exporter confere
  o IP do `uesimtun0` e, se mudou, abre uma sessão nova.
- **O formato não mudou:** a linha `[VR]` do log, as métricas `vr_*` do `/metrics`, o `qoe_vr_timeseries.csv` e tudo o
  que vem depois (agente, leilão, análise, conferência) seguem iguais. A análise já mede a duração da janela pelo
  intervalo entre elas.

Os ConfigMaps `photonpath` e `vr-exporter` têm de ser atualizados e o UE de VR recriado:

```bash
python3 photonpath/tools/gen-configmap.py --namespace pfsevaldt --out scripts/tmp/photonpath-cm.yaml && kubectl apply -f scripts/tmp/photonpath-cm.yaml   # ConfigMap com o photon_client novo
kubectl create configmap vr-exporter -n pfsevaldt --from-file=vr/vr_exporter.py --dry-run=client -o yaml | kubectl apply -f -                           # ConfigMap com o exporter novo
venv/bin/python scripts/pfcp_upf.py reiniciar-ue ue-vr-07                                                                                               # recria o UE de VR, que passa a usar os dois
kubectl logs -n pfsevaldt deploy/ue-vr-07 -c vr-client --tail=5                                                                                         # uma linha [VR] a cada 12 s, com health=1
```

## Conferência (16/11/2026)

- **No PC**, contra um servidor local: janelas, pausa, retomada, servidor morto no meio e servidor fora do ar desde o
  início. O modo antigo do cliente (sem a opção) continua dando o resumo no fim.
- **No cluster, parado:** janelas a cada 12,0 s, `health=1`, `net_p95` de 1,81 a 1,91 ms (o mesmo de antes, 1,87 a
  1,90), perda 0, vazão de 8,06 Mbps.
- **Uma execução `hvr_l2` inteira** (`hvrl2c_01`, servidor sintético no `molejo`, em `resultados/fase10_teste/`):
  veredito `ok`, 47 janelas (eram 45), 41 no SLA (87,2%), `m2p_p95` de 13,1 ms, perda 0, continuidade mínima de 99,78%.
  As 6 janelas fora do SLA são do `net`, no trecho final (781 a 902 s, 40% no SLA), como nos controles da Fase 9: é a
  disputa de CPU no `molejo` (`docs/render-real.md`, "A causa da queda"), que estas mudanças não tocam.

## Limites

- **Uma execução só.** Ela mostra que o ambiente funciona de ponta a ponta, e não que os números são iguais aos de
  antes. Com a sessão contínua, cada janela mede 12 s em vez de 10 s e não tem o reinício da sessão; o `net_p95` de uma
  janela pode sair um pouco diferente.
- **A calibração não foi refeita.** O SLA e os pisos (`vr/calibracao.yaml`) foram medidos com as janelas antigas. O
  cabeçalho dele pede recalibrar se o perfil da VR mudar; o tráfego é o mesmo (60 quadros/s × 14 pacotes), mas a medida
  mudou. Fica como decisão do Pedro antes de experimentos novos.
- **O render real não foi testado com a sessão contínua** (a GPU está liberada). O `photon_server` e o Unity já tratam
  sessão nova; com a sessão contínua há menos trocas, e os ~0,35% de quadros sem a pose da sessão devem cair.
