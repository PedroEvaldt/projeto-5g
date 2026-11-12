#!/bin/bash
# =========================================================================
# Troca o servidor de VR (vr-app-01) entre o render real (Unity + PhotonPath) e o PhotonPath sintético da Fase 8,
# os dois no metallica, e recria o UE de VR para ele abrir a sessão com o servidor novo. É o controle da Fase 9: o
# mesmo testbed (build_testbeds.sh --render-real), só o servidor muda. O resto do testbed fica como está.
#
# Uso (da raiz do repositório, na ledzeppelin, sem execução rodando):
#   scripts/trocar_servidor_vr.sh sintetico   # PhotonPath sintético (client_scripts/vr-server.yaml) no metallica
#   scripts/trocar_servidor_vr.sh render      # de volta ao render real (client_scripts/vr-server-render.yaml)
# =========================================================================
set -euo pipefail
REPO=$(cd "$(dirname "$0")/.." && pwd)
NS=pfsevaldt
NO=metallica     # o nó do vr-app-01 na Fase 9 (decisão do Pedro, 12/11/2026)

case "${1:-}" in
    render)    MANIFESTO=$(cat "$REPO/client_scripts/vr-server-render.yaml") ;;
    # O manifesto da Fase 8 com o nó trocado; o resto (perfil vr8, 300m, NET_ADMIN) fica igual
    sintetico) MANIFESTO=$(sed "s|kubernetes.io/hostname: molejo|kubernetes.io/hostname: $NO|" "$REPO/client_scripts/vr-server.yaml") ;;
    *) echo "uso: $0 render|sintetico"; exit 1 ;;
esac
grep -q "kubernetes.io/hostname: $NO" <<< "$MANIFESTO" || { echo "[ERRO] o manifesto não fixa o vr-app-01 no $NO"; exit 1; }
if tmux ls 2>/dev/null | grep -q '^exp_'; then echo "[ERRO] há uma execução aberta (tmux ls mostra exp_...)"; exit 1; fi

# Delete antes do apply: o pod do render prende a GPU, e os dois manifestos têm o mesmo Deployment
kubectl delete deployment vr-app-01 -n "$NS" --ignore-not-found
kubectl wait --for=delete pod -l app=vr-app-01 -n "$NS" --timeout=180s || true
kubectl apply -f - <<< "$MANIFESTO"
kubectl rollout status deployment/vr-app-01 -n "$NS" --timeout=600s

# O UE de VR com a sessão do servidor antigo ficaria sem quadros: recria (com o passeio só se o Unity estiver no pod)
"$REPO/venv/bin/python" "$REPO/scripts/pfcp_upf.py" reiniciar-ue ue-vr-07
kubectl get pod -n "$NS" -l app=vr-app-01 -o wide
