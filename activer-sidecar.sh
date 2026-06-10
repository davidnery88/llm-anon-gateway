#!/usr/bin/env bash
# Lance le sidecar d'anonymisation en local.
# Premier démarrage : GLiNER télécharge ~300MB (une seule fois).
# Usage : ./activer-sidecar.sh [port]

set -e
PORT=${1:-8787}
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# ── Redis ──────────────────────────────────────────────────────────────────────
if ! redis-cli ping &>/dev/null; then
  echo "→ Démarrage Redis..."
  # --bind 127.0.0.1 : les mappings PII ne doivent jamais être accessibles
  # depuis le réseau. --maxmemory : aligné sur sidecar/docker-compose.yml.
  redis-server --daemonize yes --logfile /tmp/redis-sidecar.log \
    --bind 127.0.0.1 --maxmemory 256mb --maxmemory-policy allkeys-lru
  sleep 1
fi
echo "✓ Redis OK"

# ── Sidecar ───────────────────────────────────────────────────────────────────
echo "→ Démarrage sidecar sur 127.0.0.1:$PORT (Ctrl+C pour arrêter)..."
echo "  Premier démarrage : téléchargement GLiNER ~300MB — patiente."
echo ""

REDIS_URL=redis://localhost:6379 \
  exec "$SCRIPT_DIR/sidecar/.venv/bin/uvicorn" sidecar.main:app \
    --host 127.0.0.1 \
    --port "$PORT" \
    --app-dir "$SCRIPT_DIR"
