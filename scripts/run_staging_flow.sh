#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

resolve_python() {
  if [[ -n "${PYTHON_BIN:-}" ]]; then
    printf '%s\n' "$PYTHON_BIN"
    return
  fi
  if command -v python3.11 >/dev/null 2>&1 && python3.11 -V >/dev/null 2>&1; then
    printf '%s\n' "python3.11"
    return
  fi
  if [[ -d "${HOME}/.pyenv/versions" ]]; then
    local candidate
    candidate="$(find "${HOME}/.pyenv/versions" -maxdepth 3 -path '*/bin/python3.11' -print -quit 2>/dev/null || true)"
    if [[ -n "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return
    fi
  fi
  printf '%s\n' "python3"
}

PYTHON_BIN="$(resolve_python)"
PRESET="token_due_diligence"
INPUTS_FILE="$ROOT/examples/staging/token_due_diligence.json"
WORKSPACE_DIR=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --preset)
      PRESET="$2"
      shift 2
      ;;
    --inputs-file)
      INPUTS_FILE="$2"
      shift 2
      ;;
    --workspace-dir)
      WORKSPACE_DIR="$2"
      shift 2
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

cd "$ROOT"

if [[ -f ".env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source ".env"
  set +a
fi

export PYTHONPATH="${PYTHONPATH:-src}"
export AVE_DATA_PROVIDER="${AVE_DATA_PROVIDER:-ave_rest}"
export AVE_DATA_SERVICE_BIND_HOST="${AVE_DATA_SERVICE_BIND_HOST:-127.0.0.1}"
export AVE_DATA_SERVICE_PORT="${AVE_DATA_SERVICE_PORT:-8080}"
export AVE_DATA_SERVICE_URL="${AVE_DATA_SERVICE_URL:-http://${AVE_DATA_SERVICE_BIND_HOST}:${AVE_DATA_SERVICE_PORT}}"
export OT_STAGING_WORKSPACE="${OT_STAGING_WORKSPACE:-.staging-workspace}"

if [[ -z "$WORKSPACE_DIR" ]]; then
  WORKSPACE_DIR="$OT_STAGING_WORKSPACE"
fi

if [[ ! -f "$INPUTS_FILE" ]]; then
  echo "inputs file not found: $INPUTS_FILE" >&2
  exit 1
fi

if [[ "$AVE_DATA_PROVIDER" != "mock" && -z "${AVE_API_KEY:-}" ]]; then
  echo "AVE_API_KEY is required when AVE_DATA_PROVIDER=$AVE_DATA_PROVIDER" >&2
  exit 1
fi

./scripts/start_ave_data_service.sh >"$ROOT/.staging-service.log" 2>&1 &
SERVICE_PID=$!

cleanup() {
  if kill -0 "$SERVICE_PID" >/dev/null 2>&1; then
    kill "$SERVICE_PID" >/dev/null 2>&1 || true
    wait "$SERVICE_PID" >/dev/null 2>&1 || true
  fi
}

trap cleanup EXIT

SERVICE_READY=0
for _ in $(seq 1 30); do
  if ! kill -0 "$SERVICE_PID" >/dev/null 2>&1; then
    echo "ave-data-service exited before becoming healthy" >&2
    sed -n '1,240p' "$ROOT/.staging-service.log" >&2 || true
    exit 1
  fi
  if "$PYTHON_BIN" - <<'PY'
import os
import sys
from urllib.request import urlopen

url = os.environ["AVE_DATA_SERVICE_URL"].rstrip("/") + "/healthz"
try:
    with urlopen(url, timeout=2) as response:
        if response.status == 200:
            sys.exit(0)
except Exception:
    pass
sys.exit(1)
PY
  then
    SERVICE_READY=1
    break
  fi
  sleep 1
done

if [[ "$SERVICE_READY" -ne 1 ]]; then
  echo "service health check timed out" >&2
  sed -n '1,240p' "$ROOT/.staging-service.log" >&2 || true
  exit 1
fi

"$PYTHON_BIN" -m ot_skill_enterprise.root_cli workflow-run \
  --preset "$PRESET" \
  --workspace-dir "$WORKSPACE_DIR" \
  --inputs-file "$INPUTS_FILE"
