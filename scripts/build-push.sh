#!/usr/bin/env bash
# ============================================================================
# build-push.sh — Build da imagem Docker e push para registry interno
#
# USO:
#   ./scripts/build-push.sh [TAG]
#
# Exemplo:
#   ./scripts/build-push.sh latest
#   ./scripts/build-push.sh 1.0.0
#
# NOTA: Este script NAO executa `docker stack deploy` (FR-031).
#        O deploy e responsabilidade do operador apos validacao da imagem.
# ============================================================================
set -euo pipefail

REGISTRY="${REGISTRY:-registry.todo-tips.com}"
IMAGE_NAME="sdr-whatsapp"
TAG="${1:-latest}"
FULL_IMAGE="${REGISTRY}/${IMAGE_NAME}:${TAG}"

echo "==> Build: ${FULL_IMAGE}"
docker build \
    --tag "${FULL_IMAGE}" \
    --file "$(dirname "$0")/../Dockerfile" \
    "$(dirname "$0")/.."

echo "==> Push: ${FULL_IMAGE}"
docker push "${FULL_IMAGE}"

echo "==> Concluido: ${FULL_IMAGE}"
echo ""
echo "Para deployar (responsabilidade do operador):"
echo "  1. Confirmar overlay do n8n: docker network ls | grep n8n"
echo "  2. Confirmar overlay do Traefik: docker network ls | grep traefik"
echo "  3. Criar secrets Swarm se ainda nao existirem:"
echo "     echo 'sk-...' | docker secret create openai_api_key -"
echo "     echo 'TOKEN' | docker secret create chatmaster_token -"
echo "     echo 'ADMIN_TOKEN' | docker secret create admin_token -"
echo "     echo '' | docker secret create webhook_token -  # opcional"
echo "  4. docker stack deploy -c stack.yml sdr-whatsapp"
echo ""
echo "AVISO: Este script NAO executa 'docker stack deploy' (FR-031)."
