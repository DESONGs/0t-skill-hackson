#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

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
  echo "[start_ave_data_service] uv is required; run uv sync --frozen first" >&2
  exit 1
fi

if [[ -f ".env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source ".env"
  set +a
fi

export AVE_DATA_SERVICE_BIND_HOST="${AVE_DATA_SERVICE_BIND_HOST:-127.0.0.1}"
export AVE_DATA_SERVICE_PORT="${AVE_DATA_SERVICE_PORT:-8080}"

exec "$UV_BIN" run --no-sync ot-serve-ave-data
