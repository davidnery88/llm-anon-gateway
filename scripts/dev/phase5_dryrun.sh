#!/usr/bin/env bash
# Phase 5 — dry-run reproductible de la démo zero-trust.
#
# Joue les 3 scènes du walkthrough non-interactivement via `claude --print`,
# valide le fail-safe, et audit les logs sidecar pour fuite de PII.
#
# Usage : bash scripts/dev/phase5_dryrun.sh
# Pré-requis : sidecar + gateway up, ANTHROPIC_BASE_URL=http://127.0.0.1:8787

set -uo pipefail

TOKEN=$(cat ~/.config/anon-sidecar/token)
PROXY="http://127.0.0.1:8787"
export ANTHROPIC_BASE_URL=$PROXY

YELLOW=$'\033[33m'; GREEN=$'\033[32m'; RED=$'\033[31m'; RESET=$'\033[0m'
title() { echo ""; echo "${YELLOW}═══ $1 ═══${RESET}"; }
expect() { echo "  ${GREEN}attendu:${RESET} $1"; }

title "0. Reset mapping pour démarrer clean"
curl -sS -X DELETE -H "X-Sidecar-Token: $TOKEN" "$PROXY/mapping" > /dev/null
echo "  mapping cleared"

title "1. Scène phrase libre"
expect "réponse contient 'Julien Maillard' et 'CH56' en clair"
claude --print "En une phrase, confirme à Julien Maillard (julien.maillard@bluewin.ch) que son IBAN CH56 0023 0023 1234 5678 9 a bien été enregistré." 2>&1 | head -3

title "2. Scène tableau CSV"
expect "réponse cite un client par son nom (Lehmann, Müller, Dubois...)"
claude --print "$(cat <<'EOF'
Analyse en 2 lignes max ce CSV, qui a le risque résiliation le plus élevé :

client_id,nom,prenom,email,localite,commentaire
1,Dubois,Marie,marie.dubois@bluewin.ch,Lausanne,Cliente VIP médecin Dr. Anne Leclerc
2,Müller,Hans,hans.mueller@gmx.ch,Zürich,Pensionné IBAN CH56
9,Lehmann,Petra,petra.lehmann@gmail.com,Thalwil,Divorcée 2021 ex Robert Lehmann PLUS bénéficiaire
EOF
)" 2>&1 | head -3

title "3. Scène MCP query_db"
expect "Claude liste 3 noms via query_db (race possible sur 1 placeholder restant)"
echo "Utilise mcp__anon-gateway__query_db pour me lister en 2 lignes les 3 clients avec le plus de sinistres. SQL: SELECT c.prenom, c.nom, COUNT(s.sinistre_id) FROM clients c JOIN contrats_assurance ct ON ct.client_id=c.client_id JOIN sinistres s ON s.contrat_id=ct.contrat_id GROUP BY c.client_id ORDER BY COUNT DESC LIMIT 3" | \
  claude --print --permission-mode acceptEdits --allowedTools "mcp__anon-gateway__query_db" 2>&1 | tail -3

title "4. Fail-safe : stop sidecar"
docker stop anon-sidecar > /dev/null
expect "ConnectionRefused ou 503"
claude --print "test" 2>&1 | head -2
echo "  restart sidecar..."
docker start anon-sidecar > /dev/null
until curl -fsS -m 2 "$PROXY/healthz" >/dev/null 2>&1; do sleep 3; done
echo "  sidecar ready"

title "5. Audit logs sidecar (chercher PII en clair)"
LEAKS=$(docker logs --since 5m anon-sidecar 2>&1 | grep -ciE "julien maillard|marie dubois|olivier|pieter|henry|CH56|gmail|@bluewin" || true)
if [ "$LEAKS" = "0" ]; then
  echo "  ${GREEN}✓${RESET} aucune PII en clair dans les logs sidecar"
else
  echo "  ${RED}✗${RESET} $LEAKS mentions PII trouvées — investiguer !"
fi

title "DRY-RUN TERMINÉ"
echo "Si tout est cohérent ci-dessus, le proxy est prêt pour la démo."
echo "Le split SSE inter-deltas est désormais géré par le parser structuré"
echo "  (StreamDeanonymizer.feed_sse) — voir tests test_proxy_deanonymizer.py."
