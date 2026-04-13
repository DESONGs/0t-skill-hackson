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
  printf '%s\n' "python3"
}

PYTHON_BIN="$(resolve_python)"

cd "$ROOT"

if [[ -f ".env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source ".env"
  set +a
fi

export PYTHONPATH="${PYTHONPATH:-src}"

"$PYTHON_BIN" -m ot_skill_enterprise.runtime.pi.bootstrap --workspace-dir "${OT_DEFAULT_WORKSPACE:-$ROOT/.ot-workspace}" verify >/dev/null

exec "$PYTHON_BIN" -m ot_skill_enterprise.control_plane.cli runtime start --runtime "${OT_RUNTIME_DEFAULT:-pi}" "$@"
