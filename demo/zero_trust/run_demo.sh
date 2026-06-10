#!/usr/bin/env bash
# Démo zero-trust — script séquencé tapant le SIDECAR LOCAL (127.0.0.1:8787)
# au lieu de l'ancien gateway centralisé. La gateway LAN n'est touchée que
# pour les opérations de gouvernance (admin/patterns), pas pour des PII.
#
# Prérequis :
#   1. Sidecar tournant : bash sidecar/install.sh && curl http://127.0.0.1:8787/healthz
#   2. Gateway metadata-only : docker compose up -d (depuis la racine)
#   3. Token sidecar dans ~/.config/anon-sidecar/token (si auth activée)
#
# Usage : bash demo/zero_trust/run_demo.sh

set -uo pipefail

SIDECAR="${SIDECAR_URL:-http://127.0.0.1:8787}"
GATEWAY="${GATEWAY_URL:-http://localhost:8001}"
ADMIN_SECRET="${ADMIN_SECRET:-dneri2.1}"
GATEWAY_API_KEY="${GATEWAY_API_KEY:-anon_d1efd11561695c12bafe87c0f37c5a758cbc64fd6ec084b4d68cd083a1602ab5}"
SQLITE="${SQLITE_DB:-demo/demo.sqlite}"

TOKEN_FILE="$HOME/.config/anon-sidecar/token"
SIDECAR_TOKEN=""
[ -f "$TOKEN_FILE" ] && SIDECAR_TOKEN="$(cat "$TOKEN_FILE")"

B=$'\033[1m'; D=$'\033[2m'; R=$'\033[31m'; G=$'\033[32m'; Y=$'\033[33m'; C=$'\033[36m'; M=$'\033[35m'; N=$'\033[0m'

hr()    { printf "${D}%s${N}\n" "────────────────────────────────────────────────────────────────────"; }
title() { printf "\n${B}${G}▶ %s${N}\n\n" "$1"; }
sub()   { printf "${C}%s${N}\n" "$1"; }
pause() { printf "\n${D}  ── Entrée pour continuer ──${N}"; read -r _; }

# Helper curl-wrappers
sidecar_post() {
  local path="$1"; local body="$2"
  local auth_h=()
  [ -n "$SIDECAR_TOKEN" ] && auth_h=(-H "X-Sidecar-Token: $SIDECAR_TOKEN")
  curl -sS -X POST "${SIDECAR}${path}" \
       "${auth_h[@]}" \
       -H "Content-Type: application/json" \
       -d "$body"
}

sidecar_get() {
  local path="$1"
  local auth_h=()
  [ -n "$SIDECAR_TOKEN" ] && auth_h=(-H "X-Sidecar-Token: $SIDECAR_TOKEN")
  curl -sS "${auth_h[@]}" "${SIDECAR}${path}"
}

gateway_admin() {
  local method="$1"; local path="$2"; local body="${3:-}"
  if [ -n "$body" ]; then
    curl -sS -X "$method" "${GATEWAY}${path}" \
      -H "X-Admin-Secret: $ADMIN_SECRET" \
      -H "Content-Type: application/json" \
      -d "$body"
  else
    curl -sS -X "$method" "${GATEWAY}${path}" \
      -H "X-Admin-Secret: $ADMIN_SECRET"
  fi
}

pretty() {
  python3 -c '
import json,sys,re
d=json.load(sys.stdin)
def color(s):
  s=re.sub(r"(\[[A-Z]+_\d+\])", "\033[33m\\1\033[0m", s)
  return s
text = d.get("anonymized_text") or d.get("result") or ""
print("\033[2mtext:\033[0m")
print("  "+color(text).replace("\n","\n  "))
print()
mapping = d.get("mapping") or {}
if isinstance(mapping, dict) and mapping:
  print(f"\033[2mmapping ({len(mapping)} tokens):\033[0m")
  for k,v in mapping.items():
    print(f"  \033[33m{k:<22}\033[0m → \033[31m{v}\033[0m")
'
}

# ── Pré-vol ───────────────────────────────────────────────────────────────────
clear
printf "${B}${G}"
cat <<'BANNER'
   ╔═══════════════════════════════════════════════════════════════════╗
   ║          LLM ANONYMIZATION — démo ZERO-TRUST                      ║
   ║          NER + mappings sur la machine de l'employé.              ║
   ║          Le serveur ne voit RIEN. Toujours.                       ║
   ╚═══════════════════════════════════════════════════════════════════╝
BANNER
printf "${N}\n"

sub "Vérification du stack..."

if ! curl -sf "$SIDECAR/healthz" > /dev/null 2>&1; then
  printf "${R}✗ Sidecar injoignable à $SIDECAR${N}\n"
  printf "  Lancer : ${B}bash sidecar/install.sh${N}\n"
  exit 1
fi
printf "${G}✓${N} Sidecar  %s\n" "$SIDECAR"
[ -n "$SIDECAR_TOKEN" ] && printf "${G}✓${N} Token    %s… (lu depuis %s)\n" "${SIDECAR_TOKEN:0:12}" "$TOKEN_FILE" \
                       || printf "${D}─${N} Pas de token (sidecar sans auth)\n"

if curl -sf "$GATEWAY/admin/config" -H "X-Admin-Secret: $ADMIN_SECRET" > /dev/null 2>&1; then
  printf "${G}✓${N} Gateway  %s\n" "$GATEWAY"
else
  printf "${Y}⚠${N} Gateway  %s — étapes 6 et 8 nécessitent le gateway\n" "$GATEWAY"
fi

printf "${G}✓${N} DB démo  %s\n" "$SQLITE"

pause

# ── 1. Texte libre · 3 couches ─────────────────────────────────────────────
clear
title "1 · Texte libre — 3 couches NER en local"
TEXT_1='David Neri (AVS 756.1234.5678.97) habite à Lausanne, tél +41 21 555 12 34, email david@example.com. Sinistre n° SIN-2025-00421.'
sub "Entrée :"
printf "  ${R}%s${N}\n\n" "$TEXT_1"

sub "→ POST ${SIDECAR}/anonymize   ${D}(local, jamais le LAN)${N}"
sidecar_post "/anonymize" "$(jq -n --arg t "$TEXT_1" '{text:$t}')" | pretty

hr
sub "Légende :"
printf "  ${Y}[PERSONNE_*]${N}      ← GLiNER (NER neuronal local)\n"
printf "  ${Y}[LOCALISATION_*]${N}  ← GLiNER local\n"
printf "  ${Y}[AVS_*]${N}           ← Presidio (regex 756.xxxx)\n"
printf "  ${Y}[TEL_*]${N}           ← Presidio (regex +41)\n"
printf "  ${Y}[EMAIL_*]${N}         ← Presidio\n"
printf "  ${D}SIN-2025-00421${N}    ← ${Y}toujours en clair${N} (pas de pattern · cf étape 8)\n"

pause

# ── 2. CSV tabulaire ──────────────────────────────────────────────────────
clear
title "2 · CSV tabulaire — KB de colonnes locale"
CSV_2='client_nom,iban,montant,date_sinistre
Marie Dupont,CH9300762011623852957,12500,2025-03-12
Paolo Rossi,CH5604835012345678009,8200,2025-04-02
Hans Müller,CH4408401016549870031,3400,2025-04-19'

sub "Entrée :"
printf "${R}%s${N}\n\n" "$CSV_2"

sub "Le sidecar reconnaît ${C}client_nom${N} via sa KB synchronisée (147 entrées)."
echo ""
sidecar_post "/anonymize" "$(jq -n --arg t "$CSV_2" '{text:$t}')" | pretty

pause

# ── 3. Idempotence locale ─────────────────────────────────────────────────
clear
title "3 · Mappings éphémères et cohérents au sein d'une session"

sub "Premier prompt :"
printf "  ${R}David Neri a un sinistre.${N}\n\n"
sidecar_post "/anonymize" '{"text":"David Neri a un sinistre."}' | pretty
echo ""
hr
sub "Deuxième prompt, plus tard dans la même session :"
printf "  ${R}David Neri habite à Genève.${N}\n\n"
sidecar_post "/anonymize" '{"text":"David Neri habite à Genève."}' | pretty
echo ""
hr
printf "${G}→ Même token pour David Neri dans les deux prompts.${N}\n"
printf "  ${D}Mais au reboot du poste, Redis local est vidé. Le mardi, [PERSONNE_1]${N}\n"
printf "  ${D}désignera potentiellement une autre personne. C'est volontaire.${N}\n"

pause

# ── 4. SQL démo — texte libre dans commentaire ─────────────────────────────
clear
title "4 · Query SQL démo — defense in depth"

sub "Query : SELECT prenom, nom, no_avs, commentaire FROM clients WHERE client_id=1;"
ROW=$(python3 -c "
import sqlite3,csv,sys,io
c=sqlite3.connect('$SQLITE')
r=c.execute('SELECT prenom,nom,no_avs,commentaire FROM clients WHERE client_id=1').fetchone()
buf=io.StringIO()
w=csv.writer(buf)
w.writerow(['prenom','nom','no_avs','commentaire'])
w.writerow(r)
print(buf.getvalue().strip())
")
echo ""
sub "Brut (PII partout, y compris dans la colonne commentaire libre) :"
printf "${R}%s${N}\n\n" "$ROW"

sub "→ Sidecar anonymise localement :"
sidecar_post "/anonymize" "$(jq -n --arg t "$ROW" '{text:$t}')" | pretty

hr
sub "La colonne ${C}commentaire${N} n'a pas de label PII strict — GLiNER + Presidio"
sub "${G}attrapent quand même le conjoint, le médecin, le tel, l'IBAN inline${N}."

pause

# ── 5. Multilingue DE ─────────────────────────────────────────────────────
clear
title "5 · Multilingue — client DE"
ROW_DE=$(python3 -c "
import sqlite3,csv,io
c=sqlite3.connect('$SQLITE')
r=c.execute('SELECT prenom,nom,no_avs,commentaire FROM clients WHERE client_id=2').fetchone()
buf=io.StringIO();w=csv.writer(buf)
w.writerow(['vorname','nachname','ahv','bemerkung'])
w.writerow(r)
print(buf.getvalue().strip())
")
sub "Entrée (headers DE, contenu DE) :"
printf "${R}%s${N}\n\n" "$ROW_DE"
sub "→ La KB locale connaît ${C}vorname${N} / ${C}nachname${N} / ${C}ahv${N}"
echo ""
sidecar_post "/anonymize" "$(jq -n --arg t "$ROW_DE" '{text:$t}')" | pretty

pause

# ── 6. KB sync depuis le gateway (admin) ─────────────────────────────────
clear
title "6 · KB synchronisée — gateway distribue les métadonnées"

sub "Vue admin (gateway LAN) : GET /admin/column_labels?status=active"
gateway_admin GET "/admin/column_labels?status=active" \
  | python3 -c "
import json,sys
data=json.load(sys.stdin)
print(f'\033[32m✓ {len(data)} entrées actives sur le gateway\033[0m\n')
from collections import defaultdict
by=defaultdict(list)
for r in data:
  by[r['label']].append(r['header_norm'])
for label,headers in sorted(by.items()):
  sample=', '.join(headers[:4])
  more=f' (+{len(headers)-4})' if len(headers)>4 else ''
  print(f'  \033[33m{label:<14}\033[0m {sample}{more}')
"
echo ""
hr
sub "Vue sidecar (machine user) : GET ${SIDECAR}/mapping ${D}# rien · pas de KB exposée${N}"
sub "  Sa KB est en mémoire — synchronisée toutes les 600s ou via POST /refresh."

pause

# ── 7. Mapping local (côté user) ─────────────────────────────────────────
clear
title "7 · Mapping accumulé · uniquement sur la machine user"

sub "GET ${SIDECAR}/mapping"
sidecar_get "/mapping" | python3 -c "
import json,sys
m=json.load(sys.stdin)
if not isinstance(m, dict):
  print('   (pas un mapping — auth manquante ?)')
else:
  print(f'\033[32m✓ {len(m)} tokens dans Redis local\033[0m\n')
  for k,v in list(m.items())[:25]:
    print(f'  \033[33m{k:<24}\033[0m → \033[31m{v}\033[0m')
  if len(m)>25: print(f'  \033[2m… +{len(m)-25} autres\033[0m')
"
echo ""
hr
sub "Ce mapping est en mémoire de Redis local."
sub "Reboot du poste → ${B}tout effacé${N}. Vol du laptop chiffré → ${B}rien à exfiltrer${N}."

pause

# ── 8. Pattern custom à chaud ─────────────────────────────────────────────
clear
title "8 · Pattern admin → KB sync → sidecar"

EXISTING=$(gateway_admin GET "/admin/patterns" \
  | python3 -c "import json,sys;[print(p['id']) for p in json.load(sys.stdin) if p['name']=='sinistre_no']" 2>/dev/null)
for id in $EXISTING; do
  gateway_admin DELETE "/admin/patterns/$id" > /dev/null
done

LEAK='Sinistre n° SIN-2025-00422 ouvert le 2025-04-08.'

sub "${B}AVANT${N} — POST ${SIDECAR}/anonymize"
printf "  Entrée : ${R}%s${N}\n\n" "$LEAK"
sidecar_post "/anonymize" "$(jq -n --arg t "$LEAK" '{text:$t}')" | pretty

echo ""
hr
printf "${R}⚠  SIN-2025-00422 passe entre les mailles${N}\n\n"

sub "${B}Correction live${N} sur le gateway LAN — POST /admin/patterns"
printf "  ${C}name${N}         sinistre_no\n"
printf "  ${C}regex${N}        ${Y}SIN-\\d{4}-\\d{5}${N}\n"
printf "  ${C}entity_label${N} ${G}SINISTRE${N}\n\n"

gateway_admin POST "/admin/patterns" \
  '{"name":"sinistre_no","regex":"SIN-\\d{4}-\\d{5}","entity_label":"SINISTRE","score":0.95}' > /dev/null

printf "${G}✓${N} Pattern créé · le sidecar va pull la KB...\n"

sub "POST ${SIDECAR}/refresh  ${D}(force un pull immédiat du gateway)${N}"
sidecar_post "/refresh" '{}' > /dev/null
printf "${G}✓${N} Sidecar resynchronisé\n"

sleep 0.3
echo ""
hr
LEAK2='Sinistre n° SIN-2025-00423 ouvert le 2025-04-12.'
sub "${B}APRÈS${N} — même structure, autre numéro pour éviter le cache local"
printf "  ${R}%s${N}\n\n" "$LEAK2"
sidecar_post "/anonymize" "$(jq -n --arg t "$LEAK2" '{text:$t}')" | pretty

echo ""
hr
printf "${G}→ Le sidecar a importé le pattern et tokenise désormais SIN-2025-*.${N}\n"
printf "  ${D}Toute la fleet apprend en une opération admin. Le contenu user n'a${N}\n"
printf "  ${D}jamais quitté son poste pendant tout ce trajet.${N}\n"

pause

# ── FIN ───────────────────────────────────────────────────────────────────
clear
printf "\n${B}${G}"
cat <<'END'
   ╔═══════════════════════════════════════════════════════════════════╗
   ║                  Démo zero-trust terminée  ✓                      ║
   ║                                                                   ║
   ║   • NER + mappings sur le poste · jamais sur le serveur           ║
   ║   • KB partagée via gateway · métadonnées uniquement              ║
   ║   • Side-channel qwen3-pii rate-limited, no body logging          ║
   ║   • Install/uninstall propre par sidecar.install/uninstall.sh     ║
   ║                                                                   ║
   ║   « Le LLM voit des tokens.                                       ║
   ║     Les PII restent sur le poste.                                 ║
   ║     Toujours. »                                                   ║
   ╚═══════════════════════════════════════════════════════════════════╝
END
printf "${N}\n"
