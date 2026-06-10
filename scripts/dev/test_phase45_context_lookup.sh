#!/usr/bin/env bash
# Phase 4.5 — test du lookup hiérarchique avec contexte (db, table).
#
# Setup : insère 3 entrées qui démontrent les 4 niveaux du lookup :
#   - 'numero' générique → CONTRAT (par prudence)
#   - 'numero' dans table 'marches' → NONE (référence publique, pas PII)
#   - 'description' dans table 'sinistres' → SINISTRE (texte libre avec PII)
#
# Puis appelle /anonymize avec différents contextes pour vérifier que la bonne
# règle s'applique.

set -uo pipefail

TOKEN=$(cat ~/.config/anon-sidecar/token)
SIDECAR=http://127.0.0.1:8787
PG_EXEC="docker exec -i llm-anon-gateway-postgres-1 psql -U postgres -d anondb"

YELLOW=$'\033[33m'; GREEN=$'\033[32m'; RED=$'\033[31m'; RESET=$'\033[0m'
title() { echo ""; echo "${YELLOW}▶ $1${RESET}"; }

title "1. Seed 3 entrées contextualisées en KB"
$PG_EXEC <<'SQL' 2>&1 | tail -3
INSERT INTO column_labels (header_norm, header_raw, label, source, status, db_name, table_name)
VALUES
  ('numero',      'numero',      'CONTRAT',  'admin', 'active', NULL,  NULL),
  ('numero',      'numero',      'NONE',     'admin', 'active', NULL,  'marches'),
  ('description', 'description', 'SINISTRE', 'admin', 'active', NULL,  'sinistres')
ON CONFLICT (header_norm, db_name, table_name) DO UPDATE
  SET label = EXCLUDED.label,
      source = EXCLUDED.source,
      status = EXCLUDED.status,
      updated_at = NOW();
SQL

title "2. Forcer le sidecar à re-pull la KB"
curl -sS -X POST "$SIDECAR/refresh" -H "X-Sidecar-Token: $TOKEN" | head -c 200
echo ""

call_anon() {
  local desc="$1"; local body="$2"
  echo ""
  echo "  ── $desc"
  resp=$(curl -sS -X POST "$SIDECAR/anonymize" -H "X-Sidecar-Token: $TOKEN" -H "Content-Type: application/json" -d "$body")
  echo "$resp" | python3 -c "
import json, sys
d = json.load(sys.stdin)
print('    anonymized:', d.get('anonymized_text','')[:120])
print('    mapping   :', list(d.get('mapping',{}).items())[:5])
"
}

title "3. Reset mapping pour un test propre"
curl -sS -X DELETE -H "X-Sidecar-Token: $TOKEN" "$SIDECAR/mapping" > /dev/null

title "4. Tests (valeurs différentes par cas pour ne pas hit le cache token par valeur)"
call_anon "Sans contexte (fallback générique) — 'numero' devrait être tagué CONTRAT" \
  '{"text":"[{\"numero\":\"AAA-11111\",\"montant\":\"42\"}]"}'

call_anon "Contexte table=marches — 'numero' devrait être tagué NONE" \
  '{"text":"[{\"numero\":\"BBB-22222\",\"montant\":\"42\"}]", "context":{"table":"marches"}}'

call_anon "Contexte table=contrats (pas de règle spécifique) — fallback générique → CONTRAT" \
  '{"text":"[{\"numero\":\"CCC-33333\",\"montant\":\"42\"}]", "context":{"table":"contrats"}}'

call_anon "Contexte table=sinistres — 'description' tagué SINISTRE" \
  '{"text":"[{\"description\":\"Choc voiture rouge avec piéton\",\"montant\":\"42\"}]", "context":{"table":"sinistres"}}'
