#!/usr/bin/env bash
# Étape 0.3 du plan PROXY — test refresh OAuth.
#
# Vérifie que le proxy capture aussi les requêtes APRÈS un délai assez long
# pour déclencher un refresh OAuth côté Claude Code. Si tout passe encore par
# le proxy, on a la confirmation que ANTHROPIC_BASE_URL couvre AUSSI les flux
# d'auth (pas juste /v1/messages).
#
# Usage :
#   bash scripts/dev/phase0_oauth_refresh_test.sh
#
# Le script :
#   1. T0 : lance une requête claude --print, log dans /tmp/proxy.log
#   2. Attend 65 min (en boucle visible)
#   3. T+65min : relance claude --print
#   4. Compare les events proxy : si le 2e appel passe aussi par le proxy → OK

set -u

PROXY_LOG=/tmp/proxy.log
WAIT_MIN=65

if ! ss -ltnp 2>/dev/null | grep -q ":8788"; then
  echo "❌ Le proxy n'écoute pas sur 127.0.0.1:8788. Lance-le d'abord :"
  echo "    nohup mcp_server/venv/bin/python scripts/dev/dummy_proxy.py > /tmp/proxy.log 2>&1 &"
  exit 1
fi

echo "🟢 Proxy actif. Phase 0.3 lancée."

mark_t0=$(grep -c request.in "$PROXY_LOG" 2>/dev/null || echo 0)
echo "  Events proxy avant T0 : $mark_t0"

echo ""
echo "▶ T0 — premier prompt (doit passer par le proxy)"
ANTHROPIC_BASE_URL=http://127.0.0.1:8788 claude --print "T0 — phase 0.3 — donne-moi juste l'heure actuelle" 2>&1 | tail -3
mark_t0_after=$(grep -c request.in "$PROXY_LOG")
diff_t0=$((mark_t0_after - mark_t0))
echo "  → $diff_t0 events proxy supplémentaires"

if [ "$diff_t0" -lt 1 ]; then
  echo "❌ Le premier prompt n'est PAS passé par le proxy. Test annulé."
  exit 1
fi

echo ""
echo "⏳ Attente ${WAIT_MIN} min pour déclencher un refresh OAuth potentiel..."
echo "    (commencé à $(date '+%H:%M:%S'), reprise prévue à $(date -d "+${WAIT_MIN} min" '+%H:%M:%S'))"

# Tick toutes les 5 min pour rassurer
for i in $(seq 1 $((WAIT_MIN / 5))); do
  sleep 300
  echo "  $(date '+%H:%M:%S') — $((i * 5)) / ${WAIT_MIN} min écoulées"
done

# Reste
remaining=$((WAIT_MIN - (WAIT_MIN / 5) * 5))
[ "$remaining" -gt 0 ] && sleep $((remaining * 60))

echo ""
echo "▶ T+${WAIT_MIN}min — deuxième prompt"
mark_t1=$(grep -c request.in "$PROXY_LOG")
ANTHROPIC_BASE_URL=http://127.0.0.1:8788 claude --print "T1 — phase 0.3 — donne-moi juste l'heure actuelle" 2>&1 | tail -3
mark_t1_after=$(grep -c request.in "$PROXY_LOG")
diff_t1=$((mark_t1_after - mark_t1))
echo "  → $diff_t1 events proxy supplémentaires"

echo ""
echo "═══════════════════════════════════════════════════════════"
if [ "$diff_t1" -ge 1 ]; then
  echo "✅ PHASE 0.3 OK — le 2e prompt après ${WAIT_MIN} min passe AUSSI par le proxy."
  echo "   ANTHROPIC_BASE_URL couvre les flux refresh OAuth. Phase 0 close, GO phase 1."
else
  echo "❌ PHASE 0.3 KO — le 2e prompt N'EST PAS passé par le proxy."
  echo "   ANTHROPIC_BASE_URL ne couvre pas le refresh OAuth. Plan à revoir."
fi
echo "═══════════════════════════════════════════════════════════"
echo ""
echo "Détail des requêtes proxy (path/status) :"
grep -E "request.in|response.start" "$PROXY_LOG" | tail -20 | python3 -c "
import json, sys
for l in sys.stdin:
    if not l.strip(): continue
    e = json.loads(l)
    if e['event'] == 'request.in':
        print(f\"  REQ  {e['method']:5s} {e['path']:25s} body={e['body_bytes']}B\")
    else:
        print(f\"  RESP {e['status']} {e['elapsed_ms']}ms\")
"
