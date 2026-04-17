#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

truthy() {
  [[ "${1:-}" =~ ^(1|true|yes|on)$ ]]
}

resolve_uv() {
  if command -v uv >/dev/null 2>&1; then
    command -v uv
    return
  fi
  printf '%s\n' ""
}

UV_BIN="$(resolve_uv)"

cd "$ROOT"

if [[ -z "$UV_BIN" ]]; then
  echo "[bootstrap] uv is required; install uv and rerun" >&2
  exit 1
fi

if [[ -f ".env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source ".env"
  set +a
fi

SYNC_ARGS=(sync --frozen)
if truthy "${OT_START_LOCAL_STACK:-0}" || [[ -n "${OT_DB_DSN:-}" || -n "${OT_REDIS_URL:-}" ]]; then
  SYNC_ARGS+=(--extra storage)
elif [[ -n "${OT_BLOB_ENDPOINT:-}" || -n "${OT_BLOB_BUCKET:-}" ]]; then
  SYNC_ARGS+=(--extra storage)
fi

echo "[bootstrap] syncing Python environment with uv"
"$UV_BIN" "${SYNC_ARGS[@]}"

echo "[bootstrap] preparing embedded runtime and Docker-backed AVE bridge if configured"
"$UV_BIN" run --no-sync ot-enterprise runtime prepare --workspace-dir "$ROOT/.ot-workspace"

if [[ "${OT_START_LOCAL_STACK:-0}" == "1" ]]; then
  echo "[bootstrap] starting local postgres/redis/blob stack"
  "$ROOT/scripts/start_stack.sh"
else
  echo "[bootstrap] local infra stack is optional and intended for local development only"
  echo "[bootstrap] run ./scripts/start_stack.sh for local postgres + redis + blob"
  echo "[bootstrap] production deployments should provide OT_DB_DSN / OT_REDIS_URL / OT_BLOB_* explicitly"
fi

if [[ ! -f "$ROOT/.env" ]]; then
  echo "[bootstrap] no .env found; copy .env.example to .env and fill the required real-provider values"
fi
echo "[bootstrap] run ./scripts/doctor.sh for an environment summary"
echo "[bootstrap] done"
