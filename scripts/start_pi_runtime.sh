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
  echo "[start_pi_runtime] uv is required; run uv sync --frozen first" >&2
  exit 1
fi

if [[ -f ".env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source ".env"
  set +a
fi

WORKSPACE="${OT_DEFAULT_WORKSPACE:-$ROOT/.ot-workspace}"
"$UV_BIN" run --no-sync ot-enterprise runtime prepare --workspace-dir "$WORKSPACE" >/dev/null

exec "$UV_BIN" run --no-sync ot-enterprise runtime start --runtime "${OT_RUNTIME_DEFAULT:-pi}" "$@"
