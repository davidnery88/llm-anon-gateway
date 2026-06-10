#!/usr/bin/env bash
# Build Docker images for LLM Anonymization Gateway
#
# Usage:
#   ./scripts/docker_build.sh [VERSION]
#
# Examples:
#   ./scripts/docker_build.sh          # builds with tag 'latest'
#   ./scripts/docker_build.sh v1.0.0   # builds with tag 'v1.0.0' and 'latest'
#
# Images built:
#   - davidneri/anon-gateway:TAG
#   - davidneri/anon-sidecar:TAG

set -euo pipefail

VERSION="${1:-latest}"
REGISTRY="davidneri"

echo "=== Building Docker images ==="
echo "Version: $VERSION"
echo "Registry: $REGISTRY"
echo ""

# Build gateway image
echo "→ Building gateway image..."
docker build -t "${REGISTRY}/anon-gateway:${VERSION}" -f gateway/Dockerfile gateway/

if [ "$VERSION" != "latest" ]; then
    docker tag "${REGISTRY}/anon-gateway:${VERSION}" "${REGISTRY}/anon-gateway:latest"
fi

echo "✓ Gateway image built: ${REGISTRY}/anon-gateway:${VERSION}"
echo ""

# Build sidecar image
echo "→ Building sidecar image (this may take 5-10 minutes)..."
docker build -t "${REGISTRY}/anon-sidecar:${VERSION}" -f sidecar/Dockerfile .

if [ "$VERSION" != "latest" ]; then
    docker tag "${REGISTRY}/anon-sidecar:${VERSION}" "${REGISTRY}/anon-sidecar:latest"
fi

echo "✓ Sidecar image built: ${REGISTRY}/anon-sidecar:${VERSION}"
echo ""

echo "=== Build complete ==="
echo ""
echo "Images created:"
echo "  - ${REGISTRY}/anon-gateway:${VERSION}"
echo "  - ${REGISTRY}/anon-sidecar:${VERSION}"
echo ""
echo "To push to Docker Hub:"
echo "  ./scripts/docker_push.sh ${VERSION}"
