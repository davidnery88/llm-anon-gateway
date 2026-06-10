#!/usr/bin/env bash
# uninstall.sh — retire proprement le sidecar.
#
# Lit ~/.config/anon-sidecar/.installed pour savoir quoi enlever ; refuse de
# toucher quoi que ce soit s'il n'existe pas (autre installation manuelle).
#
# Idempotent : peut être relancé plusieurs fois sans casser quoi que ce soit.
#
# Par défaut, conserve le token (utile si tu réinstalles). Ajoute --purge pour
# tout effacer y compris le token et le marker.

set -e

CONFIG_DIR="$HOME/.config/anon-sidecar"
MARKER="$CONFIG_DIR/.installed"
PURGE=0

while [[ $# -gt 0 ]]; do
  case $1 in
    --purge) PURGE=1; shift ;;
    *) echo "Usage: $0 [--purge]"; exit 1 ;;
  esac
done

echo ""
echo "  LLM Anonymization — désinstallation du sidecar"
echo ""

if [ ! -f "$MARKER" ]; then
  echo "  ⚠ Pas de marker à $MARKER — rien à désinstaller."
  echo "    (Si tu as installé manuellement, fais \`docker compose down\` à la main.)"
  exit 0
fi

# shellcheck disable=SC1090
source "$MARKER"

# ── 1. Stopper les containers ────────────────────────────────────────────────
if [ -n "${SCRIPT_DIR:-}" ] && [ -f "$SCRIPT_DIR/docker-compose.yml" ]; then
  docker compose --env-file "$ENV_FILE" -f "$SCRIPT_DIR/docker-compose.yml" down 2>&1 | sed 's/^/  /'
  echo "  ✓ Stack arrêtée"
else
  echo "  ⚠ Script dir introuvable, fallback rm container par nom"
  docker rm -f anon-sidecar anon-sidecar-redis 2>/dev/null || true
fi

# ── 2. Retirer la unit systemd ──────────────────────────────────────────────
if [ -n "${SYSTEMD_UNIT:-}" ] && [ -f "$SYSTEMD_UNIT" ]; then
  systemctl --user stop anon-sidecar.service 2>/dev/null || true
  systemctl --user disable anon-sidecar.service 2>/dev/null || true
  rm -f "$SYSTEMD_UNIT"
  systemctl --user daemon-reload 2>/dev/null || true
  echo "  ✓ systemd unit retirée"
fi

# ── 3. Purge optionnelle ─────────────────────────────────────────────────────
if [ "$PURGE" -eq 1 ]; then
  rm -f "${ENV_FILE:-/dev/null}" "${TOKEN_FILE:-/dev/null}" "$MARKER"
  rmdir "$CONFIG_DIR" 2>/dev/null || true
  echo "  ✓ Token + env + marker purgés"
else
  # Garde le token et le .env, retire juste le marker pour pouvoir réinstaller
  rm -f "$MARKER"
  echo "  ✓ Marker retiré (token et env conservés — relance install.sh pour réutiliser)"
  echo "    (utilise --purge pour tout effacer)"
fi

echo ""
echo "  Désinstallation terminée."
echo ""
