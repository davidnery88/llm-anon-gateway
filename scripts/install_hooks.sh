#!/usr/bin/env bash
# install_hooks.sh — installe les hooks Claude Code qui tapent le sidecar local.
#
# Le sidecar tourne sur 127.0.0.1 sur cette machine (cf sidecar/install.sh).
# Les hooks ne quittent pas le poste — pas de clé API, pas de gateway distant.
#
# Désinstallation propre : `bash scripts/uninstall_hooks.sh`. Tous les
# fichiers déposés et toutes les sections injectées dans settings.local.json
# sont tracés dans ~/.claude/hooks/.anon-gateway-installed et retirés au
# clic.
#
# Usage :
#   bash install_hooks.sh                          # défaut http://127.0.0.1:8787
#   bash install_hooks.sh --sidecar http://127.0.0.1:9000

set -e

SIDECAR_URL="${SIDECAR_URL:-http://127.0.0.1:8787}"

while [[ $# -gt 0 ]]; do
  case $1 in
    --sidecar) SIDECAR_URL="$2"; shift 2 ;;
    *) echo "Usage: $0 [--sidecar URL]"; exit 1 ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOKS_DIR="$HOME/.claude/hooks"
SETTINGS="$HOME/.claude/settings.local.json"
MARKER="$HOOKS_DIR/.anon-gateway-installed"

echo ""
echo "  LLM Anonymization — installation des hooks"
echo "  Sidecar : $SIDECAR_URL"
echo ""

# ── 1. Vérifier Python3 ───────────────────────────────────────────────────────
command -v python3 >/dev/null || { echo "  ✗ python3 requis."; exit 1; }
echo "  ✓ python3 $(python3 --version 2>&1 | cut -d' ' -f2)"

# ── 2. Tester la joignabilité du sidecar (warning seulement) ─────────────────
if curl -sf "$SIDECAR_URL/healthz" >/dev/null 2>&1; then
  echo "  ✓ Sidecar joignable"
else
  echo "  ⚠ Sidecar injoignable à $SIDECAR_URL — installer le sidecar (bash sidecar/install.sh) avant d'utiliser Claude Code."
fi

# ── 3. Copier les hooks depuis le repo ───────────────────────────────────────
mkdir -p "$HOOKS_DIR"
cp "$SCRIPT_DIR/hooks/hook_anonymize_prompt.py"    "$HOOKS_DIR/hook_anonymize_prompt.py"
cp "$SCRIPT_DIR/hooks/hook_deanonymize_response.py" "$HOOKS_DIR/hook_deanonymize_response.py"
chmod +x "$HOOKS_DIR/hook_anonymize_prompt.py" "$HOOKS_DIR/hook_deanonymize_response.py"
echo "  ✓ Hooks copiés dans $HOOKS_DIR"

# ── 4. Injecter / fusionner dans settings.local.json ─────────────────────────
HOOK_CMD_ANON="SIDECAR_URL=$SIDECAR_URL python3 $HOOKS_DIR/hook_anonymize_prompt.py"
HOOK_CMD_DEANON="SIDECAR_URL=$SIDECAR_URL python3 $HOOKS_DIR/hook_deanonymize_response.py"

python3 - "$SETTINGS" "$HOOK_CMD_ANON" "$HOOK_CMD_DEANON" << 'PYEOF'
import json, os, sys
settings_path, cmd_anon, cmd_deanon = sys.argv[1:4]
data = {}
if os.path.exists(settings_path):
    with open(settings_path) as f:
        data = json.load(f) or {}
data.setdefault("hooks", {})
data["hooks"]["UserPromptSubmit"] = [{
    "matcher": "",
    "hooks": [{"type": "command", "command": cmd_anon, "_anon_gateway": True}],
}]
data["hooks"]["Stop"] = [{
    "matcher": "",
    "hooks": [{"type": "command", "command": cmd_deanon, "_anon_gateway": True}],
}]
with open(settings_path, "w") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
PYEOF
echo "  ✓ $SETTINGS mis à jour (hooks UserPromptSubmit + Stop)"

# ── 5. Écrire le marker pour la désinstallation ──────────────────────────────
cat > "$MARKER" <<MARKEREOF
# Fichier généré par install_hooks.sh — ne pas éditer à la main.
# Pour désinstaller proprement : bash scripts/uninstall_hooks.sh
FILES=(
  "$HOOKS_DIR/hook_anonymize_prompt.py"
  "$HOOKS_DIR/hook_deanonymize_response.py"
)
SETTINGS="$SETTINGS"
SIDECAR_URL="$SIDECAR_URL"
INSTALLED_AT="$(date -Iseconds)"
MARKEREOF
echo "  ✓ Marker écrit dans $MARKER (pour uninstall propre)"

# ── 6. Test ──────────────────────────────────────────────────────────────────
if curl -sf "$SIDECAR_URL/healthz" >/dev/null 2>&1; then
  echo -n "  Test : "
  RESULT=$(echo '{"prompt": "Test avec M. Jean Dupont à Genève."}' | \
    SIDECAR_URL="$SIDECAR_URL" python3 "$HOOKS_DIR/hook_anonymize_prompt.py" 2>/dev/null)
  if echo "$RESULT" | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); print('ok →', d['prompt'])" 2>/dev/null; then
    echo "  ✓ Installation terminée. Relance Claude Code pour activer les hooks."
  else
    echo "  ⚠ Hook installé mais aucun PII détecté sur le texte de test (vérifie les modèles du sidecar)."
  fi
else
  echo "  ⚠ Hooks installés mais le sidecar n'est pas en route. Démarre-le pour les utiliser."
fi
echo ""
echo "  Pour désinstaller : bash scripts/uninstall_hooks.sh"
echo ""
