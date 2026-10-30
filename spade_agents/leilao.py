"""
Fase 5: a conta do leilão, separada do resource_agent.py (que fica com as mensagens e o Kubernetes).
Funções puras: entram números, saem números. Assim dá para testar sem cluster (test_leilao.py).

Recursos: "cpu" (núcleos), "bw" (Mbps) e "mem" (Mi; o leilão conta, mas nunca aplica memória).

Pisos por fatia: piso[fatia] = {"cpu": ..., "bw": ..., "mem": ...}. Com todos os pisos iguais aos mínimos
globais, ajustar_inicio e rodada fazem exatamente as contas do leiloeiro original (test_leilao.py confere
contra uma cópia do código antigo). A VR tem piso condicional: só vale enquanto ela está ativa.
"""

RECURSOS = ("cpu", "bw", "mem")


def ajustar_inicio(limites, ordem, pool, piso):
    """Na subida: se a soma dos limites passa do pool, corta das fatias, na ordem em que responderam, até caber,
    sem descer nenhuma abaixo do seu piso.

    limites: {fatia: {"cpu", "bw", "mem"}}; ordem: as fatias na ordem das respostas; pool: {"cpu", "bw", "mem"}.
    Devolve (novos limites, sobra do pool, cortes), com cortes = [(fatia, recurso, novo valor)] na ordem de aplicar.
    """
    novos = {f: dict(v) for f, v in limites.items()}
    livre = {r: pool[r] - sum(novos[f][r] for f in ordem) for r in RECURSOS}
    cortes = []
    for f in ordem:
        for r in RECURSOS:
            if livre[r] < 0 and novos[f][r] > piso[f][r]:
                excesso = min(novos[f][r] - piso[f][r], -livre[r])
                novos[f][r] -= excesso
                livre[r] += excesso
                cortes.append((f, r, novos[f][r]))
        if all(livre[r] >= 0 for r in RECURSOS):
            break
    return novos, livre, cortes


def rodada(lances, livre, piso):
    """Uma rodada do leilão Vickrey: o maior lance vence e paga o segundo maior. O que o vencedor pede sai
    primeiro da sobra do pool e, se não der, das perdedoras em partes iguais, sem descer nenhuma abaixo do piso
    (o que uma perdedora não pode dar é dividido entre as seguintes).

    lances: na ordem de chegada, cada um {"fatia", "lance", "cpu_alvo", "cpu", "bw_alvo", "bw", "mem_alvo", "mem"}.
    Devolve {"vencedor", "preco", "perdedores" (na ordem de aplicar), "novos": {fatia: {"cpu", "bw"}}, "livre"}.
    """
    ordenados = sorted(lances, key=lambda x: x["lance"], reverse=True)   # estável: empate fica na ordem de chegada
    vencedor = ordenados.pop(0)
    n = len(ordenados)
    preco = ordenados[0]["lance"] if n > 0 else vencedor["lance"]

    livre = dict(livre)
    extraido, reducao = {}, {}
    for r in ("cpu", "mem", "bw"):
        pedido = vencedor[f"{r}_alvo"] - vencedor[r]
        if pedido >= livre[r]:
            extraido[r] = livre[r]
            reducao[r] = (pedido - livre[r]) / n if n > 0 else 0
            livre[r] = 0
        else:
            extraido[r] = pedido
            reducao[r] = 0
            livre[r] -= pedido

    # Do menor limite para o maior: as mais pobres chegam ao piso primeiro e repassam a diferença às mais ricas
    perdedores = sorted(ordenados, key=lambda x: (x["bw"], x["cpu"]))
    novos = {}
    for i, p in enumerate(perdedores):
        novo = {}
        for r in ("cpu", "bw"):
            valor = p[r] - reducao[r]
            if valor < piso[p["fatia"]][r]:
                diff = p[r] - piso[p["fatia"]][r]
                extraido[r] += diff
                valor = piso[p["fatia"]][r]
                restantes = n - i - 1
                reducao[r] += (reducao[r] - diff) / restantes if restantes > 0 else reducao[r]
            else:
                extraido[r] += reducao[r]
            novo[r] = valor
        novos[p["fatia"]] = novo
    novos[vencedor["fatia"]] = {"cpu": vencedor["cpu"] + extraido["cpu"], "bw": vencedor["bw"] + extraido["bw"]}
    return {"vencedor": vencedor, "preco": preco, "perdedores": perdedores, "novos": novos, "livre": livre}


def piso_efetivo(pisos, minimos, condicionais, ativas):
    """O piso que vale agora para cada fatia: o dela, menos nas de piso condicional (a VR) que estão fora,
    que ficam no mínimo global."""
    return {f: dict(p) if f not in condicionais or f in ativas else dict(minimos) for f, p in pisos.items()}


def conferir_pisos(pisos, pool):
    """Conferência da subida: a soma dos pisos, todos valendo ao mesmo tempo, cabe no pool?
    Devolve {recurso: (soma, total, cabe)} para cpu e bw."""
    conta = {}
    for r in ("cpu", "bw"):
        soma = round(sum(p[r] for p in pisos.values()), 6)
        conta[r] = (soma, pool[r], soma <= pool[r])
    return conta


def aplicar_piso(fatia, limites, livre, piso):
    """Rodada extra (entrada da VR): leva a fatia ao piso dela na hora. Tira primeiro da sobra do pool e depois
    das outras fatias, em partes iguais, sem descer nenhuma abaixo do seu piso (quem chega ao piso sai da divisão,
    e o resto se divide entre as outras).

    limites: {fatia: {"cpu", "bw"}} atuais; livre: {"cpu", "bw", ...}; piso: o efetivo, {fatia: {"cpu", "bw"}}.
    Devolve (novos limites só das fatias que mudaram, sobra do pool, falta), com falta = {recurso: o que não deu
    para conseguir} (0 quando o piso coube).
    """
    livre = dict(livre)
    novos, falta = {}, {}
    for r in ("cpu", "bw"):
        deficit = piso[fatia][r] - limites[fatia][r]
        falta[r] = 0.0
        if deficit <= 1e-9:
            continue
        do_pool = min(deficit, max(livre[r], 0.0))
        livre[r] -= do_pool
        restante = deficit - do_pool
        sobra = {f: limites[f][r] - piso[f][r] for f in limites if f != fatia and limites[f][r] - piso[f][r] > 1e-9}
        tirado = {f: 0.0 for f in sobra}
        while restante > 1e-9 and sobra:
            parte = restante / len(sobra)
            for f in list(sobra):
                t = min(parte, sobra[f])
                tirado[f] += t
                sobra[f] -= t
                restante -= t
                if sobra[f] <= 1e-9:
                    del sobra[f]
        for f, t in tirado.items():
            if t > 0:
                # quem chegou ao piso fica exatamente nele (sem resto de ponto flutuante abaixo dele)
                valor = piso[f][r] if limites[f][r] - t - piso[f][r] <= 1e-9 else limites[f][r] - t
                novos.setdefault(f, dict(limites[f]))[r] = valor
        novos.setdefault(fatia, dict(limites[fatia]))[r] = (piso[fatia][r] if restante <= 1e-9
                                                             else piso[fatia][r] - restante)
        falta[r] = max(restante, 0.0) if restante > 1e-9 else 0.0
    return novos, livre, falta


def liberar(fatia, limites, livre, minimos):
    """Saída da VR com piso condicional: a fatia volta ao mínimo global, e o que ela tinha acima dele volta para
    a sobra do pool, para o leilão distribuir a partir da próxima rodada.
    Devolve (novo limite da fatia, sobra do pool)."""
    livre = dict(livre)
    novo = dict(limites[fatia])
    for r in ("cpu", "bw"):
        if novo[r] > minimos[r]:
            livre[r] += novo[r] - minimos[r]
            novo[r] = minimos[r]
    return novo, livre
