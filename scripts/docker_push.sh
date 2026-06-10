#!/usr/bin/env bash
# Push Docker images to Docker Hub
#
# Usage:
#   ./scripts/docker_push.sh [VERSION]
#
# Examples:
#   ./scripts/docker_push.sh          # pushes 'latest' tag
#   ./scripts/docker_push.sh v1.0.0   # pushes 'v1.0.0' and 'latest' tags
#
# Prerequisites:
#   - Docker Hub login: docker login
#   - Images must be built first: ./scripts/docker_build.sh [VERSION]

set -euo pipefail

VERSION="${1:-latest}"
REGISTRY="davidneri"

echo "=== Pushing Docker images to Docker Hub ==="
echo "Version: $VERSION"
echo "Registry: $REGISTRY"
echo ""

# Check if logged in
if ! docker info 2>/dev/null | grep -q "Username"; then
    echo "⚠ Not logged in to Docker Hub. Please run: docker login"
    exit 1
fi

# Push gateway image
echo "→ Pushing gateway image..."
docker push "${REGISTRY}/anon-gateway:${VERSION}"

if [ "$VERSION" != "latest" ]; then
    docker push "${REGISTRY}/anon-gateway:latest"
fi

echo "✓ Gateway image pushed: ${REGISTRY}/anon-gateway:${VERSION}"
echo ""

# Push sidecar image
echo "→ Pushing sidecar image (this may take a while, image is ~2GB)..."
docker push "${REGISTRY}/anon-sidecar:${VERSION}"

if [ "$VERSION" != "latest" ]; then
    docker push "${REGISTRY}/anon-sidecar:latest"
fi

echo "✓ Sidecar image pushed: ${REGISTRY}/anon-sidecar:${VERSION}"
echo ""

echo "=== Push complete ==="
echo ""
echo "Images pushed to Docker Hub:"
echo "  - https://hub.docker.com/r/${REGISTRY}/anon-gateway"
echo "  - https://hub.docker.com/r/${REGISTRY}/anon-sidecar"
