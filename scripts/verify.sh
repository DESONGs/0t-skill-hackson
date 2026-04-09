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

cd "$ROOT"

if [[ -f ".env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source ".env"
  set +a
fi

export PYTHONPATH="${PYTHONPATH:-src}"
export AVE_DATA_PROVIDER="${AVE_DATA_PROVIDER:-mock}"

echo "[verify] running tests"
"$PYTHON_BIN" -m pytest tests/unit tests/integration -q

echo "[verify] bridge discover smoke"
"$PYTHON_BIN" -m ot_skill_enterprise.root_cli bridge discover >/tmp/ot_enterprise_bridge_discover.json

echo "[verify] workflow smoke"
"$PYTHON_BIN" -m ot_skill_enterprise.root_cli workflow-run \
  --preset token_due_diligence \
  --workspace-dir .ot-workspace \
  --inputs-file examples/staging/token_due_diligence.json >/tmp/ot_enterprise_workflow_smoke.json

echo "[verify] complete"
