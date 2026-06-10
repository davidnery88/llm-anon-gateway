#!/usr/bin/env bash
# Arrête le sidecar et supprime la config Claude Code du dossier de test.
# Usage : ./desactiver-sidecar.sh [dossier-test]
# Par défaut le dossier de test est ~/proxy-test

TEST_DIR=${1:-"$HOME/proxy-test"}

# ── Arrêter le sidecar ────────────────────────────────────────────────────────
if pkill -f "uvicorn sidecar.main:app" 2>/dev/null; then
  echo "✓ Sidecar arrêté"
else
  echo "  (sidecar n'était pas lancé)"
fi

# ── Supprimer la config Claude Code ──────────────────────────────────────────
if [ -f "$TEST_DIR/.claude/settings.json" ]; then
  rm "$TEST_DIR/.claude/settings.json"
  echo "✓ Config proxy supprimée ($TEST_DIR/.claude/settings.json)"
else
  echo "  (pas de config proxy à supprimer)"
fi

echo ""
echo "Claude Code pointe à nouveau directement sur api.anthropic.com."
