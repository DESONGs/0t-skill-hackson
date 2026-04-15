#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$ROOT/docker-compose.yml"

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

if [[ ! -f "$COMPOSE_FILE" ]]; then
  echo "[stack] docker-compose.yml not found; local dev stack is unavailable"
  echo "[stack] production deployments must provide OT_DB_DSN / OT_REDIS_URL / OT_BLOB_* explicitly"
  exit 0
fi

if [[ -z "$COMPOSE_BIN" ]]; then
  echo "[stack] docker compose is unavailable; local dev stack was not started"
  echo "[stack] production deployments must provide OT_DB_DSN / OT_REDIS_URL / OT_BLOB_* explicitly"
  echo "[stack] expected local services: postgres:5432 redis:6379 blob:minio 9000/9001"
  exit 0
fi

echo "[stack] starting local development postgres, redis, and blob services"
$COMPOSE_BIN -f "$COMPOSE_FILE" up -d postgres redis blob

echo "[stack] current service status"
$COMPOSE_BIN -f "$COMPOSE_FILE" ps

cat <<'EOF'
[stack] local development stack is ready
[stack] postgres: postgres://ot:ot_dev_password@127.0.0.1:5432/ot_skill_enterprise
[stack] redis: redis://127.0.0.1:6379/0
[stack] blob: http://127.0.0.1:9000
[stack] console: http://127.0.0.1:9001
[stack] this stack is for local development only
EOF
