#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WITH_INFRA=0

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

if [[ "${1:-}" == "--with-infra" ]]; then
  WITH_INFRA=1
  shift
fi

COMPOSE_BIN="$(resolve_compose)"

cd "$ROOT"

if [[ -z "$COMPOSE_BIN" ]]; then
  echo "[docker_up] docker compose is required" >&2
  exit 1
fi

if [[ ! -f "$ROOT/.env" ]]; then
  echo "[docker_up] .env is required; copy .env.example to .env first" >&2
  exit 1
fi

mkdir -p "$ROOT/.ot-workspace"

if [[ "$WITH_INFRA" == "1" ]]; then
  exec $COMPOSE_BIN --profile infra --profile app up -d postgres redis blob ave-data-service frontend "$@"
fi

exec $COMPOSE_BIN --profile app up -d ave-data-service frontend "$@"
