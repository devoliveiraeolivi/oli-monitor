#!/usr/bin/env bash
# deploy.sh — Pull das imagens GHCR e redeploy da stack oli-monitor
#
# Uso:
#   bash scripts/deploy.sh
#
# Pré-requisitos:
#   - Rodar no node Swarm manager
#   - python scripts/render_configs.py já executado (prometheus.yml renderizado)

set -euo pipefail

STACK_NAME="oli-monitor"
COMPOSE_FILE="docker-compose.yml"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$ROOT_DIR"

# ── Pull imagens custom (GHCR) ─────────────────────────────
echo "Pulling oli-alerts:latest ..."
docker pull ghcr.io/devoliveiraeolivi/oli-alerts:latest

# ── Deploy stack ────────────────────────────────────────────
echo "Deploying stack $STACK_NAME ..."
docker stack deploy -c "$COMPOSE_FILE" "$STACK_NAME"
echo "Deploy OK"

echo ""
echo "Verificar status:"
echo "  docker service ls --filter name=$STACK_NAME"
echo "  docker service logs ${STACK_NAME}_alerts --tail 50"
