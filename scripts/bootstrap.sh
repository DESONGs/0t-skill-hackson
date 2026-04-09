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

echo "[bootstrap] installing project dependencies"
"$PYTHON_BIN" -m pip install -e ".[dev]"

if [[ -f "$ROOT/vendor/ave_cloud_skill/scripts/requirements.txt" ]]; then
  echo "[bootstrap] installing vendored AVE CLI dependencies"
  "$PYTHON_BIN" -m pip install -r "$ROOT/vendor/ave_cloud_skill/scripts/requirements.txt"
fi

echo "[bootstrap] done"
