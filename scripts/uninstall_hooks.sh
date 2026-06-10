#!/usr/bin/env bash
# uninstall_hooks.sh — retire proprement les hooks Claude Code installés par
# install_hooks.sh. Lit le marker ~/.claude/hooks/.anon-gateway-installed
# pour savoir quoi enlever ; refuse de toucher quoi que ce soit s'il n'existe pas.
#
# Idempotent : peut être relancé plusieurs fois sans casser quoi que ce soit.

set -e

HOOKS_DIR="$HOME/.claude/hooks"
MARKER="$HOOKS_DIR/.anon-gateway-installed"
SETTINGS="$HOME/.claude/settings.local.json"

echo ""
echo "  LLM Anonymization — désinstallation des hooks"
echo ""

if [ ! -f "$MARKER" ]; then
  echo "  ⚠ Pas de marker à $MARKER — rien à désinstaller."
  echo "    (Si tu as installé manuellement, retire à la main les sections"
  echo "    'UserPromptSubmit' et 'Stop' de $SETTINGS.)"
  exit 0
fi

# Source le marker pour récupérer FILES + SETTINGS
# shellcheck disable=SC1090
source "$MARKER"

# ── 1. Retirer les sections injectées de settings.local.json ─────────────────
if [ -f "$SETTINGS" ]; then
  python3 - "$SETTINGS" << 'PYEOF'
import json, sys
path = sys.argv[1]
with open(path) as f:
    data = json.load(f) or {}

hooks = data.get("hooks", {})
removed = []
for event in ("UserPromptSubmit", "Stop"):
    entries = hooks.get(event, [])
    # On ne supprime que les hooks que NOUS avons marqués (_anon_gateway = True)
    kept = []
    for entry in entries:
        inner = entry.get("hooks", [])
        ours = [h for h in inner if isinstance(h, dict) and h.get("_anon_gateway")]
        rest = [h for h in inner if not (isinstance(h, dict) and h.get("_anon_gateway"))]
        if rest:
            kept.append({**entry, "hooks": rest})
        if ours:
            removed.append(event)
    if kept:
        hooks[event] = kept
    else:
        hooks.pop(event, None)

if not hooks:
    data.pop("hooks", None)
else:
    data["hooks"] = hooks

with open(path, "w") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
print(f"  ✓ {path} nettoyé ({len(removed)} hook(s) retiré(s) : {', '.join(set(removed)) or 'aucun'})")
PYEOF
else
  echo "  ⚠ $SETTINGS introuvable — skip."
fi

# ── 2. Supprimer les fichiers hooks ──────────────────────────────────────────
for f in "${FILES[@]}"; do
  if [ -f "$f" ]; then
    rm -- "$f"
    echo "  ✓ supprimé $f"
  fi
done

# ── 3. Retirer le marker lui-même ────────────────────────────────────────────
rm -- "$MARKER"
echo "  ✓ marker retiré"

echo ""
echo "  Désinstallation terminée. Relance Claude Code pour appliquer."
echo ""
