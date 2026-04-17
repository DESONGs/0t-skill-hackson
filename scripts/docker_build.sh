#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

resolve_compose() {
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    printf '%s\n' "docker compose"
    return
  fi
  if command -v docker-compose >/dev/null 2>&1; then
    printf '%s\n' "docker-compose"
    return
  fi
  printf '%s\n' ""
}

COMPOSE_BIN="$(resolve_compose)"

cd "$ROOT"

if [[ -z "$COMPOSE_BIN" ]]; then
  echo "[docker_build] docker compose is required" >&2
  exit 1
fi

if [[ ! -f "$ROOT/.env" ]]; then
  echo "[docker_build] .env is required; copy .env.example to .env first" >&2
  exit 1
fi

if [[ $# -gt 0 ]]; then
  exec $COMPOSE_BIN --profile app build "$@"
fi

exec $COMPOSE_BIN --profile app build ave-data-service qa
