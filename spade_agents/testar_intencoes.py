"""
Fase 7: o critério de pronto do LLM. Manda as intenções do tests_configs/intencoes_llm.yaml ao LLM, com o mesmo
prompt e o mesmo provedor do strategist_agent.py, e confere cada resposta:
  - válida: JSON (com ou sem as cercas de markdown, como o dispatcher do artigo aceitava), no formato, só com fatias
    que o LLM pode mudar e prioridades em [1, 10] sem precisar limitar;
  - o que o dispatcher manda depois do politica_llm.validar (o que se aproveita de uma resposta inválida);
  - sentido: depois de aplicar a resposta, as prioridades caem nas faixas esperadas da intenção (quando há).
Por padrão, as intenções vão numa conversa só, em ordem, como os eventos de uma execução (o deploy_test.py usa uma
conversa por teste); com --isoladas, cada uma vai numa conversa nova, a partir das prioridades do YAML.

Uso (na ledzeppelin, de dentro de spade_agents/; precisa do .env com a API_KEY e da rede da UFRGS):
  ../venv/bin/python testar_intencoes.py ../tests_configs/test_config_scvr_l3.yaml    # o prompt da L3
  ../venv/bin/python testar_intencoes.py ../tests_configs/test_config_scvr_l2.yaml    # o do artigo, para comparar
Grava um CSV por rodada em metricas_scripts/resultados/fase7_llm/.
"""

import argparse
import asyncio
import csv
import json
import os
import time
from pathlib import Path

import yaml

AQUI = Path(__file__).resolve().parent
SAIDA = AQUI.parent / "metricas_scripts" / "resultados" / "fase7_llm"


def avaliar(resposta, fatias, politica_llm):
    """(validas, problemas) de uma resposta: o que o dispatcher manda e por que a resposta não é válida de primeira."""
    problemas = []
    limpo = resposta.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        json.loads(limpo)
    except json.JSONDecodeError:
        try:
            politica_llm.extrair_json(resposta)
            problemas.append("texto fora do JSON (o dispatcher do artigo falharia)")
        except json.JSONDecodeError:
            pass                                          # o validar já descarta como "não é JSON"
    validas, descartes = politica_llm.validar(resposta, fatias)
    problemas += [d["motivo"] for d in descartes]
    problemas += [f"{v['fatia']}: prioridade fora de [1, 10] ({v['ajuste']})" for v in validas if v["ajuste"]]
    return validas, problemas


def conferir(estado, esperado, fatias):
    """'-' sem nada a conferir; 'ok'; ou as fatias fora da faixa esperada."""
    faixas = {f: faixa for f, faixa in (esperado or {}).items() if f in fatias}
    if not faixas:
        return "-"
    erros = [f"{f}={estado[f]:g} (esperado {lo:g} a {hi:g})" for f, (lo, hi) in faixas.items() if not lo <= estado[f] <= hi]
    return "ok" if not erros else "não: " + "; ".join(erros)


async def rodar(args):
    os.environ["TEST_FILE"] = str(Path(args.config).resolve())
    import strategist_agent as st                         # lê o YAML do cenário na importação
    import politica_llm
    from spade_llm import LLMProvider
    from spade_llm.context import ContextManager

    with open(args.intencoes) as f:
        intencoes = yaml.safe_load(f)["intencoes"]
    prompt = politica_llm.montar_prompt(st.config, st.KNOWS_VR, st.vr_floors())
    provider = LLMProvider(model=st.MODEL, base_url=st.BASE_URL, api_key=st.API_KEY, temperature=st.TEMPERATURE)
    contexto = ContextManager(system_prompt=prompt)
    padrao = {nome: float(s['initial_priority']) for nome, s in st.config['slices'].items()}
    estado = dict(padrao)
    modo = "isoladas" if args.isoladas else "conversa"
    print(f"Prompt: vr.llm = {st.VR_LLM} | fatias que o LLM pode mudar: {', '.join(st.LLM_SLICES)} | "
          f"{len(intencoes)} intenções, {'cada uma numa conversa nova' if args.isoladas else 'numa conversa só'}\n")

    linhas = []
    for i, it in enumerate(intencoes, 1):
        conversa = f"intencao-{i}" if args.isoladas else "teste"
        if args.isoladas:
            estado = dict(padrao)
        contexto.add_message_dict({"role": "user", "content": it["texto"]}, conversa)
        t0 = time.monotonic()
        try:
            resposta = await provider.get_response(contexto) or ""
            erro = ""
        except Exception as e:                            # a chamada falhou: conta como resposta inválida
            resposta, erro = "", f"erro na chamada: {e}"
        segundos = time.monotonic() - t0
        contexto.add_assistant_message(resposta, conversa)

        validas, problemas = avaliar(resposta, st.LLM_SLICES, politica_llm)
        if erro:
            problemas = [erro]
        for v in validas:
            estado[v["fatia"]] = v["prioridade"]
        sentido = conferir(estado, it.get("esperado"), st.LLM_SLICES)
        enviado = ", ".join(f"{v['fatia']}={v['prioridade']:g}" for v in validas) or "nada"

        print(f"[{i:2}/{len(intencoes)}] {it['texto']}")
        print(f"        LLM ({segundos:.1f} s): {' '.join(resposta.split())[:300]}")
        print(f"        {'válida' if not problemas else 'INVÁLIDA: ' + '; '.join(problemas)} | envia: {enviado} | sentido: {sentido}\n")
        linhas.append({"N": i, "Intencao": it["texto"], "Segundos": f"{segundos:.1f}", "Valida": not problemas,
                       "Problemas": "; ".join(problemas), "Enviado": enviado, "Sentido": sentido,
                       "Estado": json.dumps(estado, ensure_ascii=False), "Resposta": resposta})

    validas_n = sum(l["Valida"] for l in linhas)
    conferidas = [l for l in linhas if l["Sentido"] != "-"]
    print(f"Válidas: {validas_n} de {len(linhas)}" + ("" if validas_n == len(linhas) else
          f" (inválidas: {', '.join('#' + str(l['N']) for l in linhas if not l['Valida'])})"))
    print(f"Sentido esperado: {sum(l['Sentido'] == 'ok' for l in conferidas)} de {len(conferidas)} conferidas"
          + ("" if all(l["Sentido"] == "ok" for l in conferidas) else
             f" (fora: {', '.join('#' + str(l['N']) for l in conferidas if l['Sentido'] != 'ok')})"))

    SAIDA.mkdir(parents=True, exist_ok=True)
    caminho = SAIDA / f"intencoes_{st.VR_LLM}_{modo}_{time.strftime('%Y%m%d_%H%M%S')}.csv"
    with open(caminho, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(linhas[0]))
        w.writeheader()
        w.writerows(linhas)
    print(f"CSV: {caminho}")


def main():
    ap = argparse.ArgumentParser(description="Fase 7: manda as intenções de teste ao LLM e confere as respostas")
    ap.add_argument("config", help="YAML do cenário (o vr.llm dele escolhe o prompt: artigo = L2, vr = L3)")
    ap.add_argument("--intencoes", default=str(AQUI.parent / "tests_configs" / "intencoes_llm.yaml"),
                    help="arquivo com as intenções e as faixas esperadas")
    ap.add_argument("--isoladas", action="store_true", help="cada intenção numa conversa nova")
    asyncio.run(rodar(ap.parse_args()))


if __name__ == "__main__":
    main()
