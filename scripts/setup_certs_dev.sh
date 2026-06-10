#!/usr/bin/env bash
# Génère des certificats auto-signés temporaires si certs/ n'existe pas.
# Pour la production, utiliser scripts/setup_https.sh avec mkcert.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
CERTS_DIR="$PROJECT_ROOT/certs"

if [ -f "$CERTS_DIR/server.pem" ] && [ -f "$CERTS_DIR/server-key.pem" ]; then
    echo "✓ Certificats déjà présents dans $CERTS_DIR/"
    exit 0
fi

echo "⚠ Génération de certificats auto-signés temporaires..."
echo "  Pour la production, utiliser: ./scripts/setup_https.sh <IP_SERVEUR>"

mkdir -p "$CERTS_DIR"

# Générer un cert auto-signé valide 1 an
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
    -keyout "$CERTS_DIR/server-key.pem" \
    -out "$CERTS_DIR/server.pem" \
    -subj "/CN=localhost" \
    -addext "subjectAltName=IP:127.0.0.1,IP:192.168.0.0/16,IP:10.0.0.0/8,DNS:localhost" \
    2>/dev/null

# Copier le cert comme CA (auto-signé = son propre CA)
cp "$CERTS_DIR/server.pem" "$CERTS_DIR/ca.pem"

echo "✓ Certificats temporaires générés dans $CERTS_DIR/"
echo "  ⚠ Ces certs ne sont PAS signés par mkcert — les clients devront désactiver la vérification SSL"
echo "  Pour la production: ./scripts/setup_https.sh <IP_SERVEUR>"
