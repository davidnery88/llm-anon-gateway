#!/usr/bin/env bash
# Lance le gateway (PostgreSQL + gateway + frontend) sur la machine serveur.
# Usage : ./activer-gateway.sh

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Charge les variables d'env si un .env existe
if [ -f .env ]; then
  export $(grep -v '^#' .env | xargs)
fi

echo "→ Démarrage gateway..."
docker compose up -d --build

echo ""
echo "✓ Gateway disponible sur http://$(hostname -I | awk '{print $1}'):8001"
echo "✓ Frontend disponible sur http://$(hostname -I | awk '{print $1}'):3000"
echo ""
echo "Logs : docker compose logs -f gateway"
