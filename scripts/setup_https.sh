#!/usr/bin/env bash
# Génère les certificats TLS pour le gateway avec mkcert.
#
# Prérequis :
#   - mkcert installé (https://github.com/FiloSottile/mkcert)
#   - La CA mkcert installée sur le poste : mkcert -install
#
# Usage :
#   ./scripts/setup_https.sh [IP_DU_SERVEUR_LAN]
#
# Exemple :
#   ./scripts/setup_https.sh 192.168.1.100
#
# Les certs sont générés dans certs/ et montés dans le container nginx.

set -euo pipefail

SERVER_IP="${1:-}"
if [ -z "$SERVER_IP" ]; then
    echo "Usage: $0 <IP_DU_SERVEUR_LAN>"
    echo "Exemple: $0 192.168.1.100"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
CERTS_DIR="$PROJECT_ROOT/certs"

# Vérifier mkcert
if ! command -v mkcert &> /dev/null; then
    echo "Erreur: mkcert n'est pas installé."
    echo "Installer avec: brew install mkcert (macOS) ou voir https://github.com/FiloSottile/mkcert"
    exit 1
fi

# Créer le dossier certs
mkdir -p "$CERTS_DIR"

# Générer les certs
echo "Génération des certificats pour $SERVER_IP..."
cd "$CERTS_DIR"
mkcert -cert-file server.pem -key-file server-key.pem "$SERVER_IP" localhost 127.0.0.1

# Copier la CA pour le sidecar (les sidecars doivent faire confiance à cette CA)
CA_FILE="$(mkcert -CAROOT)/rootCA.pem"
cp "$CA_FILE" "$CERTS_DIR/ca.pem"

echo ""
echo "✓ Certificats générés dans $CERTS_DIR/"
echo "  - server.pem + server-key.pem → montés dans nginx"
echo "  - ca.pem → monté dans le sidecar (SSL_CERT_FILE)"
echo ""
echo "⚠ Installer la CA sur les postes clients :"
echo "  1. Copier $CERTS_DIR/ca.pem sur le poste"
echo "  2. Installer avec mkcert -install (ou manuellement dans le trousseau)"
echo ""
echo "⚠ Redémarrer docker compose pour prendre en compte les certs :"
echo "  docker compose down && docker compose up -d"
