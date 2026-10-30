"""
Fase 6: a decisão do agente da VR por SLA (L2), em funções puras. O VRSliceAgent (slice_agents.py) lê as
métricas e os limites, chama decidir() e manda o lance no mesmo formato do agente de vídeo; o leiloeiro não muda.
Testes sem cluster em test_agente_vr.py.

O lance mede quanto a VR gastou da folga do SLA (a fórmula do plano, lance_vr). O pedido segue o diagnóstico das
Fases 3 e 4:
  - atraso alto com a cota da upf4 esgotando (throttling) -> falta CPU: pede +cpu_passo, até cpu_teto;
  - vazão abaixo do SLA sem throttling -> falta banda: pede +bw_passo;
  - atraso alto sem throttling -> o resto fora das cotas: CPU não resolve, não pede.
Os pedidos vão pelo menos até o piso (se alguém apertou a upf4 à mão e a VR sofre, ela volta direto ao piso).
Sem nada a pedir, o lance é 0: a VR perde a rodada, o piso a protege e o vídeo leva a sobra.
Com a VR fora (vr_paused = 1) ou sem métricas, lance 0 e nada pedido. Janela sem saúde: repete a última decisão.
"""

LANCE_MIN = 0.1     # fração da base: o menor lance da fórmula do plano (d = 0)
# Quem não pede nada não dá lance. Com 0,1 x base (10), a VR vencia sem pedir nada quando os vídeos, recém-reiniciados,
# davam lances de 8, 5 e 3: pagava a rodada e o vídeo perdia a vez (execução hvrl2_02)
SEM_LANCE = 0.0

PARAMETROS = {"cpu_passo": 0.05, "cpu_teto": 0.45, "throttling_min": 0.02, "d_pedir": 0.5, "bw_passo": 1.0}


def distancia(net_p95_ms, cont_pct, perda_pct, sla):
    """Quanto cada métrica gastou da folga do SLA: 0 = perfeito, 1 = no limite, >1 = violando. Devolve o pior,
    limitado a 1,5, e as três parcelas."""
    d_net = max(0.0, net_p95_ms - sla["net_ref_ms"]) / sla["folga_net_ms"]
    d_cont = max(0.0, 100 - cont_pct) / (100 - sla["cont_min_pct"])
    d_perda = max(0.0, perda_pct) / sla["perda_max_pct"]
    return min(max(d_net, d_cont, d_perda), 1.5), {"net": d_net, "cont": d_cont, "perda": d_perda}


def lance_vr(d, base, saldo):
    """A fórmula do plano: d=0 -> 0,1 x base; d=1 -> ~1,03 x base; d=1,5 -> 1,5 x base. Nunca passa do saldo."""
    return min(base * (LANCE_MIN + 1.4 * d / 1.5), saldo)


def decidir(m, atual, piso, sla, base, saldo, anterior=None, parametros=None, minimo=None):
    """Uma decisão por rodada.

    m: métricas da última janela, ou None se não houver: {"paused", "health", "net_ms", "cont_pct", "perda_pct",
       "vazao_mbps", "throttling"} (throttling = fração dos períodos de 100 ms em que a upf4 esgotou a cota).
    atual: limites atuais da upf4, {"cpu", "bw"}; piso: o piso da VR, {"cpu", "bw"}; sla: o bloco sla do calibracao.yaml.
    anterior: a decisão da rodada anterior (para repetir numa janela sem saúde).
    minimo: os mínimos globais do leilão, {"cpu", "bw"}. A upf4 nos dois mínimos quer dizer que o leiloeiro liberou o
    piso (a VR saiu), mesmo que a leitura do Prometheus, de até 5 s atrás, ainda não mostre a pausa.
    Devolve {"situacao", "lance", "cpu_alvo", "bw_alvo", "d"}. Os alvos nunca ficam abaixo dos limites atuais.
    """
    p = {**PARAMETROS, **(parametros or {})}
    nada = {"cpu_alvo": atual["cpu"], "bw_alvo": atual["bw"]}

    if m is None or m.get("paused") is None or m["paused"] == 1:
        return {"situacao": "fora", "lance": SEM_LANCE, "d": 0.0, **nada}
    if minimo and atual["cpu"] <= minimo["cpu"] + 1e-9 and atual["bw"] <= minimo["bw"] + 1e-9:
        return {"situacao": "fora (piso liberado)", "lance": SEM_LANCE, "d": 0.0, **nada}
    if m.get("health") != 1:
        if anterior is None:
            return {"situacao": "sem_saude", "lance": SEM_LANCE, "d": 0.0, **nada}
        return {**anterior, "situacao": "sem_saude (repete)", "lance": min(anterior["lance"], saldo),
                "cpu_alvo": max(anterior["cpu_alvo"], atual["cpu"]), "bw_alvo": max(anterior["bw_alvo"], atual["bw"])}

    d, _ = distancia(m["net_ms"], m["cont_pct"], m["perda_pct"], sla)
    # Janela sem quadros: o exporter zera a continuidade, mas não reescreve a vazão (fica a da janela anterior)
    vazao = 0.0 if m["cont_pct"] <= 0 else m["vazao_mbps"]
    cpu_alvo, bw_alvo, motivos = atual["cpu"], atual["bw"], []
    # Abaixo do piso com a VR bem, o agente não pede nada: quem sabe se o piso vale (VR ativa) é o leiloeiro, e ele
    # devolve ao piso quem está abaixo dele. Pedir aqui fez a VR, recém-pausada e ainda vista como ativa, pegar o
    # piso de volta (execução hvrl2_01). Se a VR sofre, os pedidos abaixo já vão pelo menos até o piso.

    if d >= p["d_pedir"]:
        throttling = m.get("throttling") or 0.0
        if throttling >= p["throttling_min"]:
            # A cota esgota: mais CPU resolve (Fase 4)
            cpu_alvo = max(cpu_alvo, min(max(atual["cpu"] + p["cpu_passo"], piso["cpu"]), p["cpu_teto"]))
            if cpu_alvo > atual["cpu"]:
                motivos.append("cpu")
            elif not motivos:
                # Já no teto de CPU: acima dele mais CPU não melhorou nada na calibração com vídeo (Fase 4)
                return {"situacao": "teto_cpu", "lance": SEM_LANCE, "d": d, **nada}
        elif vazao < sla["vazao_min_mbps"]:
            # O fluxo não passa e a cota não esgota: falta banda
            bw_alvo = max(bw_alvo, piso["bw"], atual["bw"] + p["bw_passo"])
            motivos.append("banda")
        elif not motivos:
            # Atraso alto sem cota esgotando e com o fluxo inteiro: o resto fora das cotas (Fase 4); CPU não resolve
            return {"situacao": "fora_das_cotas", "lance": SEM_LANCE, "d": d, **nada}

    if not motivos:
        return {"situacao": "no_sla", "lance": SEM_LANCE, "d": d, **nada}
    return {"situacao": "+".join(motivos), "lance": lance_vr(d, base, saldo), "d": d,
            "cpu_alvo": max(cpu_alvo, atual["cpu"]), "bw_alvo": max(bw_alvo, atual["bw"])}
