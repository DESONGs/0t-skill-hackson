#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATUS=0
ENV_SOURCE=""

if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
  ENV_SOURCE=".env"
elif [[ -f "$ROOT/.env.example" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env.example"
  set +a
  ENV_SOURCE=".env.example"
fi

print_status() {
  local level="$1"
  local message="$2"
  printf '%-8s %s\n' "[$level]" "$message"
}

mark_fail() {
  STATUS=1
  print_status "fail" "$1"
}

mark_ok() {
  print_status "ok" "$1"
}

mark_warn() {
  print_status "warn" "$1"
}

truthy() {
  [[ "${1:-}" =~ ^(1|true|yes|on)$ ]]
}

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
  if command -v python3 >/dev/null 2>&1; then
    printf '%s\n' "python3"
    return
  fi
  printf '%s\n' ""
}

check_python() {
  local python_bin="$1"
  if [[ -z "$python_bin" ]]; then
    mark_fail "Python 3.11+ is required"
    return
  fi
  if "$python_bin" - <<'PY'
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY
  then
    mark_ok "python: $("$python_bin" --version 2>&1)"
  else
    mark_fail "python: $("$python_bin" --version 2>&1) (need 3.11+)"
  fi
}

check_node() {
  if ! command -v node >/dev/null 2>&1; then
    mark_fail "node is required (need 20+)"
    return
  fi
  local major
  major="$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)"
  if [[ "$major" =~ ^[0-9]+$ ]] && (( major >= 20 )); then
    mark_ok "node: $(node --version)"
  else
    mark_fail "node: $(node --version 2>&1) (need 20+)"
  fi
}

check_command() {
  local name="$1"
  local description="$2"
  if command -v "$name" >/dev/null 2>&1; then
    mark_ok "$description: $(command -v "$name")"
  else
    mark_warn "$description: not found"
  fi
}

echo "0T-Skill environment doctor"
echo "root: $ROOT"

PYTHON_BIN="$(resolve_python)"
check_python "$PYTHON_BIN"
check_node
check_command uv "uv"

if command -v npm >/dev/null 2>&1; then
  mark_ok "npm: $(npm --version)"
else
  mark_fail "npm is required"
fi

check_command cargo "cargo (needed only for live execution fallback)"

if command -v docker >/dev/null 2>&1; then
  if docker compose version >/dev/null 2>&1; then
    mark_ok "docker compose: available"
  else
    mark_warn "docker compose: unavailable"
  fi
else
  mark_warn "docker: not found"
fi

if [[ -f "$ROOT/.env" ]]; then
  mark_ok ".env: present"
else
  mark_warn ".env: missing; copy .env.example to .env"
fi

if [[ "$ENV_SOURCE" == ".env.example" ]]; then
  mark_ok "env preview: using .env.example defaults for diagnostics"
fi

if [[ -f "$ROOT/uv.lock" ]]; then
  mark_ok "uv.lock: present"
else
  mark_fail "uv.lock: missing"
fi

if [[ -x "$ROOT/.venv/bin/python" ]]; then
  mark_ok ".venv: present"
else
  mark_warn ".venv: missing; run uv sync --frozen"
fi

if [[ -d "$ROOT/vendor/pi_runtime/node_modules" ]]; then
  mark_ok "pi runtime deps: installed"
else
  mark_warn "pi runtime deps: missing; run uv run 0t runtime prepare --workspace-dir .ot-workspace"
fi

if [[ -f "$ROOT/vendor/pi_runtime/dist/pi-runtime.mjs" ]]; then
  mark_ok "pi runtime bundle: present"
else
  mark_warn "pi runtime bundle: missing; run uv run 0t runtime prepare --workspace-dir .ot-workspace"
fi

PROVIDER_MODE="${AVE_DATA_PROVIDER:-}"
if [[ -z "$PROVIDER_MODE" ]]; then
  if [[ -n "${AVE_API_KEY:-}" ]]; then
    PROVIDER_MODE="auto"
  else
    PROVIDER_MODE="mock"
  fi
fi

REFLECTION_MODE="real"
if truthy "${OT_PI_REFLECTION_MOCK:-0}"; then
  REFLECTION_MODE="mock"
fi

mark_ok "provider mode: $PROVIDER_MODE"
mark_ok "reflection mode: $REFLECTION_MODE"

if [[ "$PROVIDER_MODE" == "ave_rest" || "$PROVIDER_MODE" == "real" ]]; then
  missing_real=()
  [[ -n "${AVE_API_KEY:-}" ]] || missing_real+=("AVE_API_KEY")
  [[ -n "${API_PLAN:-}" ]] || missing_real+=("API_PLAN")
  [[ -n "${KIMI_API_KEY:-}" ]] || missing_real+=("KIMI_API_KEY")
  if (( ${#missing_real[@]} == 0 )); then
    mark_ok "real distillation env: ready"
  else
    mark_warn "real distillation env: missing ${missing_real[*]}"
  fi
fi

if [[ "$PROVIDER_MODE" == "mock" && "$REFLECTION_MODE" == "mock" ]]; then
  mark_warn "startup profile: mock verification mode"
else
  mark_ok "startup profile: real provider path"
fi

echo
echo "uv path readiness:"
if command -v uv >/dev/null 2>&1 && [[ -f "$ROOT/uv.lock" ]]; then
  print_status "ok" "uv startup contract available"
else
  print_status "warn" "uv startup contract blocked"
fi
if truthy "${AVE_USE_DOCKER:-0}"; then
  if command -v docker >/dev/null 2>&1; then
    if docker image inspect ave-cloud >/dev/null 2>&1; then
      print_status "ok" "AVE Docker image: ave-cloud present"
    else
      print_status "warn" "AVE Docker image: missing; runtime prepare will build it"
    fi
  else
    print_status "fail" "AVE Docker image: docker required because AVE_USE_DOCKER=true"
    STATUS=1
  fi
fi

echo
echo "docker path readiness:"
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  print_status "ok" "docker compose app path available"
else
  print_status "warn" "docker compose app path unavailable"
fi
if [[ -f "$ROOT/docker-compose.yml" ]]; then
  print_status "ok" "docker-compose.yml: present"
else
  print_status "warn" "docker-compose.yml: missing"
fi

echo
echo "recommended next steps:"
echo "  uv path:"
echo "    1. cp .env.example .env"
echo "    2. uv sync --frozen"
echo "    3. uv run 0t runtime prepare --workspace-dir .ot-workspace"
echo "  docker path:"
echo "    1. cp .env.example .env"
echo "    2. ./scripts/docker_build.sh"
echo "    3. ./scripts/docker_up.sh"

exit "$STATUS"
