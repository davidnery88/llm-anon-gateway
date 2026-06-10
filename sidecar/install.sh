#!/usr/bin/env bash
# install.sh — installe le sidecar zero-trust sur la machine utilisateur.
#
# Ce que fait ce script :
#   1. Vérifie Docker + Docker Compose
#   2. Génère un secret partagé X-Sidecar-Token (mode 0600) si absent
#   3. Build l'image (~2 GB, premier run uniquement)
#   4. Lance la stack avec docker compose
#   5. Optionnellement écrit une unit systemd-user pour démarrage au boot
#
# Désinstallation : `bash sidecar/uninstall.sh`. Idempotent.
#
# Usage :
#   bash sidecar/install.sh                              # défaut : token + auto-start
#   bash sidecar/install.sh --gateway http://192.168.1.13:8001
#   bash sidecar/install.sh --no-systemd                 # skip systemd unit

set -e

GATEWAY_URL="${GATEWAY_URL:-http://host.docker.internal:8001}"
GATEWAY_API_KEY="${GATEWAY_API_KEY:-}"
WITH_SYSTEMD=1

while [[ $# -gt 0 ]]; do
  case $1 in
    --gateway)    GATEWAY_URL="$2"; shift 2 ;;
    --key)        GATEWAY_API_KEY="$2"; shift 2 ;;
    --no-systemd) WITH_SYSTEMD=0; shift ;;
    *) echo "Usage: $0 [--gateway URL] [--key API_KEY] [--no-systemd]"; exit 1 ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CONFIG_DIR="$HOME/.config/anon-sidecar"
TOKEN_FILE="$CONFIG_DIR/token"
ENV_FILE="$CONFIG_DIR/env"
MARKER="$CONFIG_DIR/.installed"
SYSTEMD_UNIT="$HOME/.config/systemd/user/anon-sidecar.service"

echo ""
echo "  LLM Anonymization — sidecar zero-trust install"
echo "  Gateway (pour KB + classify_column) : $GATEWAY_URL"
echo ""

# ── 1. Dépendances ────────────────────────────────────────────────────────────
command -v docker >/dev/null || { echo "  ✗ docker requis."; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "  ✗ docker compose (plugin) requis."; exit 1; }
echo "  ✓ docker $(docker --version | cut -d' ' -f3 | tr -d ,)"
echo "  ✓ docker compose $(docker compose version --short)"

# ── 2. Token X-Sidecar-Token ─────────────────────────────────────────────────
mkdir -p "$CONFIG_DIR"
chmod 700 "$CONFIG_DIR"
if [ ! -s "$TOKEN_FILE" ]; then
  if command -v openssl >/dev/null; then
    openssl rand -hex 32 > "$TOKEN_FILE"
  else
    head -c 32 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 64 > "$TOKEN_FILE"
  fi
  chmod 600 "$TOKEN_FILE"
  echo "  ✓ Token X-Sidecar-Token généré : $TOKEN_FILE (mode 0600)"
else
  echo "  ✓ Token existant : $TOKEN_FILE"
fi

# ── 3. .env pour docker compose ──────────────────────────────────────────────
cat > "$ENV_FILE" <<EOF
GATEWAY_URL=$GATEWAY_URL
GATEWAY_API_KEY=$GATEWAY_API_KEY
ANON_SIDECAR_TOKEN=$(cat "$TOKEN_FILE")
ANON_SIDECAR_ALLOWED_ORIGIN=${ANON_SIDECAR_ALLOWED_ORIGIN:-http://localhost:3000}
EOF
chmod 600 "$ENV_FILE"
echo "  ✓ Variables d'env écrites : $ENV_FILE"

# ── 4. Build de l'image ──────────────────────────────────────────────────────
echo "  Build de l'image sidecar (peut prendre 5-10 min la première fois)..."
docker compose --env-file "$ENV_FILE" -f "$SCRIPT_DIR/docker-compose.yml" build sidecar

# ── 5. Démarrage de la stack ─────────────────────────────────────────────────
docker compose --env-file "$ENV_FILE" -f "$SCRIPT_DIR/docker-compose.yml" up -d
echo "  ✓ Stack démarrée"

# ── 6. Healthcheck ───────────────────────────────────────────────────────────
echo -n "  Attente du healthcheck... "
for i in {1..30}; do
  if curl -sf http://127.0.0.1:8787/healthz >/dev/null 2>&1; then
    echo "ok"
    break
  fi
  sleep 1
  [ $i -eq 30 ] && { echo "TIMEOUT"; exit 1; }
done

# ── 7. systemd-user unit (Linux uniquement, optionnel) ───────────────────────
if [ "$WITH_SYSTEMD" -eq 1 ] && command -v systemctl >/dev/null && [ -d "/run/systemd/system" ]; then
  mkdir -p "$(dirname "$SYSTEMD_UNIT")"
  cat > "$SYSTEMD_UNIT" <<EOF
[Unit]
Description=LLM Anonymization sidecar
After=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=$SCRIPT_DIR
ExecStart=/usr/bin/docker compose --env-file $ENV_FILE -f $SCRIPT_DIR/docker-compose.yml up -d
ExecStop=/usr/bin/docker compose --env-file $ENV_FILE -f $SCRIPT_DIR/docker-compose.yml down

[Install]
WantedBy=default.target
EOF
  systemctl --user daemon-reload
  systemctl --user enable --now anon-sidecar.service >/dev/null 2>&1 || true
  echo "  ✓ systemd-user unit installée : $SYSTEMD_UNIT"
  echo "    (run \`loginctl enable-linger $USER\` pour démarrage au boot sans session)"
fi

# ── 8. Marker pour uninstall ─────────────────────────────────────────────────
cat > "$MARKER" <<EOF
INSTALLED_AT=$(date -Iseconds)
REPO_DIR=$REPO_DIR
SCRIPT_DIR=$SCRIPT_DIR
ENV_FILE=$ENV_FILE
TOKEN_FILE=$TOKEN_FILE
SYSTEMD_UNIT=$SYSTEMD_UNIT
WITH_SYSTEMD=$WITH_SYSTEMD
EOF

echo ""
echo "  ✓ Installation terminée."
echo "    Sidecar : http://127.0.0.1:8787"
echo "    Token   : $TOKEN_FILE  (lu automatiquement par MCP/hooks)"
echo "    Logs    : docker logs -f anon-sidecar"
echo "    Stop    : bash sidecar/uninstall.sh"
echo ""
