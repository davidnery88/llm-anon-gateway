#!/usr/bin/env bash
# Arrête le gateway. Les données PostgreSQL sont conservées (volume pg_data).
# Usage :
#   ./desactiver-gateway.sh          — arrête sans supprimer les données
#   ./desactiver-gateway.sh --reset  — arrête ET supprime les données (pg_data)

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if [ "${1}" = "--reset" ]; then
  echo "→ Arrêt + suppression des données..."
  docker compose down -v
  echo "✓ Gateway arrêté, données supprimées."
else
  echo "→ Arrêt gateway (données conservées)..."
  docker compose down
  echo "✓ Gateway arrêté."
fi
