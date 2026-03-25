#!/usr/bin/env bash
set -euo pipefail

# Linux Docker stack wrapper for A1phquest.
# Intended for VPS deployment where Docker Engine runs directly on Linux.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="deploy/docker-compose.yml"
COMPOSE_ENV_FILE="${COMPOSE_ENV_FILE:-$ROOT_DIR/.env}"
DOCKER_BIN="${DOCKER_BIN:-docker}"
CMD="${1:-ps}"
shift || true

cd "$ROOT_DIR"

case "$CMD" in
  up)
    if [ "${BUILD_IMAGES:-0}" = "1" ]; then
      $DOCKER_BIN compose --env-file "$COMPOSE_ENV_FILE" -f "$COMPOSE_FILE" up --build -d "$@"
    else
      $DOCKER_BIN compose --env-file "$COMPOSE_ENV_FILE" -f "$COMPOSE_FILE" up -d "$@"
    fi
    ;;
  down)
    $DOCKER_BIN compose --env-file "$COMPOSE_ENV_FILE" -f "$COMPOSE_FILE" down "$@"
    ;;
  ps)
    $DOCKER_BIN compose --env-file "$COMPOSE_ENV_FILE" -f "$COMPOSE_FILE" ps "$@"
    ;;
  logs)
    $DOCKER_BIN compose --env-file "$COMPOSE_ENV_FILE" -f "$COMPOSE_FILE" logs -f "$@"
    ;;
  restart)
    $DOCKER_BIN compose --env-file "$COMPOSE_ENV_FILE" -f "$COMPOSE_FILE" restart "$@"
    ;;
  *)
    echo "Usage: $0 {up|down|ps|logs|restart} [compose args...]"
    exit 2
    ;;
esac
