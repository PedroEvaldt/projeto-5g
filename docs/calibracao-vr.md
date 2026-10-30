# Calibração da VR (Fase 3)

**Pergunta:** quanto de CPU e de banda a `upf4` precisa dar para a VR ficar no SLA? Esses números viram o piso da VR no leilão (Fase 5) e o SLA do agente de VR (Fase 6).

**Resposta:** piso de **0,22 núcleo** e de **9,9 Mbps**, gravados no `vr/calibracao.yaml` junto com o SLA. Os dois recursos falham de jeitos diferentes:

- **Banda:** precipício entre 8,5 e 9 Mbps, logo acima do que a VR ocupa na saída da `upf4` (~8,8 Mbps).
- **CPU:** a vazão só quebra abaixo de ~0,13 núcleo, mas o atraso só volta ao normal em 0,2. Entre os dois, o limite do CFS congela a UPF por pedaços de cada período de 100 ms.

> **Atualização da Fase 4 (30/10/2026):** os números deste doc valem para a VR sozinha. Com os 3 vídeos tocando, a `upf4` precisa de mais CPU, e o piso de CPU do `calibracao.yaml` passou a **0,28 núcleo**. O 0,22 ficou só como referência. O SLA e o piso de banda não mudaram. Ver `docs/coleta-vr.md`.

## Ambiente

- 27 e 28/10/2026, namespace `pfsevaldt`. `ue-vr-07` e `vr-app-01` no `blacksabbath`, `upf4` no `wb-fibre-01`.
- Leilão desligado (nenhuma sessão `exp_` no tmux da ledzeppelin) e vídeos parados.
- Só a VR, com uma sessão do perfil vr8: 60 quadros/s × 14 pacotes de 1200 B = 8,05 Mbps.
- A `upf4` foi fixada à mão pelo `vr/calibrar.py`, com as mesmas chamadas do leiloeiro: resize com `requests = limits` e as anotações do Calico.
- No fim, o script devolve o estado original. No template, isso é CPU com limit de 150m (request de 100m), 15M de banda e nenhum burst. O estado foi conferido depois das séries.

```bash
venv/bin/python vr/calibrar.py --recurso banda                                        # banda: 7 pontos × 5 repetições (~75 min)
venv/bin/python vr/calibrar.py --recurso cpu                                          # CPU, pontos do plano: 6 pontos × 5 repetições (~65 min)
venv/bin/python vr/calibrar.py --recurso cpu --pontos 0.11 0.12 0.13 0.14 0.15 0.17   # CPU refinada perto do limiar (~65 min)
venv/bin/python vr/graficos_calibracao.py <os 3 CSVs que valem>                       # figuras, tabelas e o vr/calibracao.yaml
```

Os CSVs, as figuras (`figuras/calib_cpu.pdf` e `figuras/calib_banda.pdf`) e as anotações de cada arquivo (`LEIAME.md`) ficam em `metricas_scripts/resultados/calibracao/`, fora do git. Valem três CSVs:

- `calib_cpu_20261027_184453`;
- `calib_cpu_20261028_133110`;
- `calib_banda_20261028_121544`.

## Método

Cada **medida** é um ponto (um valor de um recurso) numa repetição:

1. Pausa a VR (`USR1` no exporter).
2. Põe a `upf4` na folga (CPU 0,4 e banda 15) por 3 s, para esvaziar a fila da medida anterior. Sem isso, um ponto quebrado contaminaria o seguinte: com 10m de CPU, ficam ~11 s de fila.
3. Aplica o valor do ponto. O outro recurso fica na folga.
4. Retoma (`USR2`) e coleta 9 janelas de 12 s (2 s de aquecimento e 10 s de estatística), ~2 min.

São 5 repetições por ponto, com a ordem dos pontos sorteada de novo a cada repetição. As janelas são lidas do log do exporter a partir da marca `[COMANDO] retomada`, e não por horário, porque os relógios das máquinas diferem (ver os achados).

**Burst.** O script aplica `ingressBurst`/`egressBurst` de 1M, como o `upf_set_resources.py` faz nos experimentos. Sem essa anotação, o limite de banda não age (ver os achados).

**SLA por janela.** Uma janela está no SLA quando cumpre os quatro critérios:

| Critério     | Limite                         | Por quê                                                                       |
| ------------ | ------------------------------ | ----------------------------------------------------------------------------- |
| `net_p95`    | ≤ `net_ref` + 3 ms = 4,87 ms   | `net_ref` = 1,87 ms, a mediana das janelas nos pontos de folga                |
| perda        | ≤ 1%                           | bem acima do artefato do BYE (até ~0,15% por janela, Fase 2)                  |
| continuidade | ≥ 98%                          | a borda da janela já tira ~0,3%                                               |
| vazão        | ≥ 7,65 Mbps (95% do fluxo)     | a perda e a continuidade não veem um fluxo que chega pingando (ver os achados) |

Uma janela sem quadros fica fora do SLA. Uma janela com `health=0` é falha do instrumento e fica de fora da conta; nas séries, não houve nenhuma.

**Estatística.** A unidade de repetição é a medida, não a janela. As 9 janelas de uma medida são a mesma sessão e a mesma fila; tratá-las como repetições estreitaria o intervalo de confiança à toa (pseudorreplicação).

- **Por medida:** a mediana das janelas e a % de janelas no SLA.
- **Por ponto:** a média das 5 medidas, com IC de 95% pela t de Student (4 graus de liberdade).

**Piso.** É o menor ponto com ≥ 95% das janelas no SLA, desde que todos os pontos acima dele também passem, mais 10%. O valor é arredondado para cima: a CPU em 10m, porque o systemd arredonda a cota assim de qualquer jeito (Fase 1), e a banda em 0,1 Mbps.

## Banda

CPU fixa em 0,4. A figura é a `calib_banda.pdf`.

| Banda (Mbps)  | `net_p95`                      | Vazão (Mbps) | Continuidade | Janelas no SLA |
| ------------- | ------------------------------ | ------------ | ------------ | -------------- |
| 6             | 34 de 45 janelas sem quadros   | 0            | 0%           | 0%             |
| 8             | 3 973 ms                       | 4,57         | 64%          | 0%             |
| 8,5           | 721 ms                         | 7,48         | 98%          | 0%             |
| 9, 10, 12, 15 | 1,87 ms                        | 8,05         | 99,7%        | 100%           |

`net_p95` e vazão são a média das 5 medidas, com IC de no máximo 2,4 ms e 0,01 Mbps. A continuidade é a mediana das janelas.

- **O corte fica entre 8,5 e 9 Mbps.** A anotação limita cada sentido separadamente. Na saída da `upf4` passam:
  - a descida com GTP-U: 1264 B × 840 pacotes/s ≈ 8,5 Mbps;
  - as poses que sobem: 156 B × 250/s ≈ 0,3 Mbps.

  O total é ~8,8 Mbps. Com 8,5, entra mais do que sai, e a fila enche; com 9, sobram ~2%. O plano esperava o degrau perto de 9 Mbps.
- **É um precipício:** de 9 para 8,5 Mbps, o atraso vai de 1,87 para 721 ms. Não há faixa intermediária.
- **Em 6 Mbps, a sessão quase não sobrevive.** A primeira janela de cada medida entrega ~5,4 Mbps, com 5,8 s de atraso. Depois, janelas sem quadros se alternam com janelas de ~1,7 Mbps e 32% de continuidade.
- **Em aberto:** os segundos de atraso em 6 e 8 Mbps. A fila do limitador é curta: se o Calico seguir o plugin bandwidth do CNI, são 25 ms mais o burst, ~0,2 s em 6 Mbps. Os segundos vêm de outro lugar, ainda não identificado. Uma pista: em 8 Mbps, as 5 medidas, em ordem sorteada, deram 3 973 ± 2,4 ms. Um atraso tão repetível sugere um limite de tamanho fixo (um buffer de tantos bytes, por exemplo), e não congestionamento aleatório. O piso não muda, porque esses pontos falham de qualquer jeito.

## CPU

Banda fixa em 15 Mbps. Juntam-se duas séries: a do plano (0,01 a 0,4) e a refinada (0,11 a 0,17). A figura é a `calib_cpu.pdf`.

| CPU (núcleo) | `net_p95`                    | Vazão (Mbps) | Janelas no SLA |
| ------------ | ---------------------------- | ------------ | -------------- |
| 0,01         | 40 de 45 janelas sem quadros | 0            | 0%             |
| 0,02         | 23 de 45 janelas sem quadros | 0,17         | 0%             |
| 0,05         | 2 624 ms                     | 1,77         | 0%             |
| 0,1          | 785 ms                       | 6,18         | 0%             |
| 0,11         | 682 ms                       | 7,05         | 0%             |
| 0,12         | 265 ms                       | 7,89         | 0%             |
| 0,13         | 229 ms                       | 8,04         | 0%             |
| 0,14         | 51 ms                        | 8,05         | 0%             |
| 0,15         | 29 ms                        | 8,05         | 0%             |
| 0,17         | 8 ms (± 5)                   | 8,05         | 40% (± 18)     |
| 0,2          | 1,86 ms                      | 8,05         | 100%           |
| 0,4          | 1,87 ms                      | 8,05         | 100%           |

Médias das 5 medidas. Fora do 0,17, o IC do `net_p95` fica abaixo de 30% da média.

**O limite de CPU não deixa a UPF mais lenta: ele dá uma cota.** Com 0,15 núcleo, a UPF pode usar 15 ms de CPU em cada período de 100 ms do CFS. Se gastar a cota antes do fim do período, o kernel a congela até o próximo período, e os pacotes que chegam nesse meio-tempo esperam. A VR precisa de ~0,13 núcleo em média: em 0,1, a UPF entrega 6,18 de 8,05 Mbps, e 0,1 / 0,77 ≈ 0,13. Isso dá três zonas:

1. **Até 0,11, falta CPU na média.** A UPF processa menos do que chega, a fila cresce, e a vazão cai. Na vazão, é um precipício, como na banda.
2. **De 0,12 a 0,17, a média cabe, mas a UPF congela.** A vazão é completa, mas o atraso desce aos poucos: 265 → 229 → 51 → 29 → 8 ms. É o efeito do CFS no `net` p95 que o plano previa.
3. **De 0,2 para cima, a cota quase nunca acaba,** e o atraso fica em 1,86 ms.

- **Em 0,17, as janelas se dividem em dois grupos:** 18 entre 1,9 e 4,6 ms (no SLA) e 27 entre 6,3 e 12,1 ms, a maioria perto de 11. Hipótese, não conferida: 100 ms são exatamente 6 quadros a 60 fps, então o congelamento cai sempre na mesma posição do período. Esse alinhamento escorrega devagar, e cada janela de 12 s pega uma fase boa ou ruim. Daí o IC largo.
- **O congelamento também segura as poses que sobem.** O `hold_p95` (tempo da pose dentro do servidor) sobe de ~11 ms para até ~70 ms entre 0,1 e 0,15, e o servidor não está apertado. A leitura provável: sem pose nova, o servidor desenha com a pose antiga. O `m2p`, que é o atraso que o usuário sente, sobe junto: 51 ms em 0,15 e 22 ms em 0,17, contra ~13 ms com folga. O SLA olha só o `net`, que já reprova esses pontos.
- **A série do plano rodou sem burst, e a refinada com o burst de 1M.** Em 28/10/2026, com a `upf4` no template (150m, sem burst), 10 janelas do exporter deram `net_p95` de 27,7 a 30,2 ms, o mesmo que o 0,15 com burst (29 ms). O atraso vem da CPU, e as duas séries podem ser juntadas.

## SLA e pisos (`vr/calibracao.yaml`)

|                                  | CPU                     | Banda                  |
| -------------------------------- | ----------------------- | ---------------------- |
| Menor ponto que passa            | 0,2 núcleo (100%)       | 9 Mbps (100%)          |
| Ponto medido logo abaixo         | 0,17 (40%)              | 8,5 (0%)               |
| **Piso (+10%)**                  | **0,22 (220m)**         | **9,9 Mbps**           |
| Pool: piso + 3 × mínimo do vídeo | 0,25 ≤ 0,8              | 12,9 ≤ 35              |

- O `vr/calibracao.yaml` é gravado pelo `graficos_calibracao.py` e não deve ser editado à mão.
- O pool e os mínimos do vídeo vêm da seção `auction` do `tests_configs/test_config.yaml`.
- Os nomes do bloco `sla` são os mesmos do `lance_vr` do plano (`net_ref_ms`, `folga_net_ms`, `cont_min_pct`, `perda_max_pct`), mais o `vazao_min_mbps`.

## Achados que não estavam no plano

- **Sem burst, a anotação de banda não limita.** O template da UPF (`5Gcore_files/deploy-upf.sh`) não tem `ingressBurst`/`egressBurst`, e aí o Calico usa um balde de 4 Gibit. Com o limite em 6 Mbps, a VR levaria quase meia hora para esvaziá-lo.
  - A primeira série de banda, sem burst, não mostrou efeito nenhum de 6 a 15 Mbps. Ficou como controle negativo.
  - Os experimentos não sofrem disso, porque o `upf_set_resources.py` aplica 1M no início. Mas qualquer teste fora deles tem de aplicar o burst.
- **A perda e a continuidade do PhotonPath não veem um fluxo que chega pingando.** Em 0,01 núcleo, houve janelas com perda de 0% e continuidade de 98,5%, mas só 4,5% da vazão.
  - A perda só conta os buracos entre o primeiro e o último quadro recebidos, e a fila entrega os quadros em ordem.
  - Por isso a vazão entrou no SLA. Nas séries, ela nunca foi o único critério a reprovar uma janela (o `net` reprovou junto), então os pisos seriam os mesmos sem ela. Fica como rede de segurança.
- **No template, a `upf4` deixa a VR fora do SLA.** Com 150m, o `net_p95` fica em ~29 ms. Na Fase 2, a VR "funcionava" em vazão e `health`, mas o atraso não foi olhado com a CPU do template.
- **Os relógios das máquinas diferem.** O nome de cada CSV traz o horário de início pelo relógio da ledzeppelin, e a coluna `hora` traz o carimbo do `blacksabbath` (`kubectl logs --timestamps`).
  - A série refinada de CPU começou depois que a de banda terminou (rodaram encadeadas). Mesmo assim, o nome do CSV dela marca 13:31:10, e a última janela da banda marca 13:33:53.
  - Logo, o `blacksabbath` está pelo menos 2 min 43 s adiantado.

## Limites

- **Uma sessão de VR.** Com duas, a demanda na `upf4` muda, e a calibração tem de ser refeita.
- **Sem vídeo e sem interferência.** Num nó carregado, a UPF pode esperar pela CPU mesmo com cota sobrando. Não foi testado.
- **O trecho de CPU entre 0,17 e 0,2 não foi medido.** Se 0,18 passasse, o piso cairia no máximo 20m.
- **Duas explicações estão em aberto:** a hipótese dos dois grupos em 0,17 não foi conferida, e os segundos de atraso na banda não estão explicados.
- **A perda do cliente é um piso.** Quadros perdidos inteiros na borda da janela não entram na conta (Fase 2).
- **A equivalência das séries de CPU com e sem burst foi conferida num ponto só** (150m, 10 janelas).

## O que isso muda no plano

- **Fase 3:** o critério de pronto foi cumprido: há um gráfico por recurso, com IC, e o `calibracao.yaml` com o SLA e os pisos.
- **Fase 4:**
  - o coletor marca `SLA_OK` com os quatro critérios do `calibracao.yaml`;
  - os horários do `blacksabbath` e da ledzeppelin não podem ser cruzados sem corrigir a diferença de minutos (ou sincronizar os relógios).
- **Fase 5:**
  - `floor_cpu: 0.22` e `floor_bw: 9.9` vêm do `calibracao.yaml`. O exemplo do plano tinha 0,1 de CPU, e com 0,1 a VR desaba.
  - O `upf_set_resources.py` começa os experimentos com a `upf4` em 200m, abaixo do piso. A rodada extra na entrada da VR tem de subir a CPU também, não só a banda.
- **Fase 6:**
  - o `lance_vr` lê o bloco `sla` do `calibracao.yaml`;
  - um termo de vazão no lance é opcional, porque o `net` reage antes;
  - para decidir o que pedir, as assinaturas ajudam. Vazão completa com `net` alto é falta de CPU (zona de 0,12 a 0,17). Vazão abaixo do fluxo pode ser falta de banda ou de CPU, e a primeira tentativa é a banda, seguindo a regra do plano.
- **Recalibrar** se mudar o perfil da VR, o número de sessões ou o nó da `upf4`.
