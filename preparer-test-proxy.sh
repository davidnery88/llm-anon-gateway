#!/usr/bin/env bash
# Crée le dossier de test avec la config Claude Code scoped.
# Usage : ./preparer-test-proxy.sh [dossier] [port]
# Par défaut : ~/proxy-test, port 8787

TEST_DIR=${1:-"$HOME/proxy-test"}
PORT=${2:-8787}

mkdir -p "$TEST_DIR/.claude"
cat > "$TEST_DIR/.claude/settings.json" << EOF
{
  "env": {
    "ANTHROPIC_BASE_URL": "http://127.0.0.1:$PORT"
  }
}
EOF

echo "✓ Dossier de test prêt : $TEST_DIR"
echo ""
echo "Pour tester :"
echo "  cd $TEST_DIR && claude"
echo ""
echo "Pour rollback :"
echo "  ./desactiver-sidecar.sh"
