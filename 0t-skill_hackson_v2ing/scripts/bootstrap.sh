#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

resolve_python() {
  if [[ -x "$ROOT/.venv/bin/python" ]]; then
    printf '%s\n' "$ROOT/.venv/bin/python"
    return
  fi
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

cd "$ROOT"

echo "[bootstrap] installing project dependencies"
"$PYTHON_BIN" -m pip install -e "."

if [[ -f "$ROOT/vendor/ave_cloud_skill/scripts/requirements.txt" ]]; then
  echo "[bootstrap] installing vendored AVE CLI dependencies"
  "$PYTHON_BIN" -m pip install -r "$ROOT/vendor/ave_cloud_skill/scripts/requirements.txt"
fi

if [[ -f "$ROOT/vendor/pi_runtime/package.json" ]]; then
  echo "[bootstrap] installing vendored Pi runtime dependencies"
  (cd "$ROOT/vendor/pi_runtime" && npm install)
  echo "[bootstrap] building embedded Pi runtime artifact"
  "$PYTHON_BIN" -m ot_skill_enterprise.runtime.pi.bootstrap --workspace-dir "$ROOT/.ot-workspace" build
  echo "[bootstrap] verifying embedded Pi runtime artifact"
  "$PYTHON_BIN" -m ot_skill_enterprise.runtime.pi.bootstrap --workspace-dir "$ROOT/.ot-workspace" verify
fi

if [[ "${OT_START_LOCAL_STACK:-0}" == "1" ]]; then
  echo "[bootstrap] starting local postgres/redis/blob stack"
  "$ROOT/scripts/start_stack.sh"
else
  echo "[bootstrap] local infra stack is optional and intended for local development only"
  echo "[bootstrap] run ./scripts/start_stack.sh for local postgres + redis + blob"
  echo "[bootstrap] production deployments should provide OT_DB_DSN / OT_REDIS_URL / OT_BLOB_* explicitly"
fi

echo "[bootstrap] done"
