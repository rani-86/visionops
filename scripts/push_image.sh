#!/usr/bin/env bash
# First-time (or manual) image push, before GitHub Actions is set up.
#   ./scripts/push_image.sh <ecr_repository_url> [tag]
# Then: cd infra && terraform apply -var image_tag=<tag>
set -euo pipefail
REPO_URL="$1"
TAG="${2:-$(git rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M%S)}"
REGISTRY="${REPO_URL%%/*}"
REGION="$(echo "$REGISTRY" | cut -d. -f4)"

aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$REGISTRY"
docker build --platform linux/amd64 --build-arg GIT_SHA="$TAG" -t "$REPO_URL:$TAG" .
docker push "$REPO_URL:$TAG"
echo "pushed $REPO_URL:$TAG"
