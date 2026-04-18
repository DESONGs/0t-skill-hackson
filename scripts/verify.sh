#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACE="${OT_DEFAULT_WORKSPACE:-$ROOT/.ot-workspace}"

resolve_python() {
  if [[ -x "$ROOT/.venv/bin/python" ]]; then
    printf '%s\n' "$ROOT/.venv/bin/python"
    return
  fi
  if [[ -n "${PYTHON_BIN:-}" ]]; then
    printf '%s\n' "$PYTHON_BIN"
    return
  fi
  if command -v python3 >/dev/null 2>&1; then
    printf '%s\n' "python3"
    return
  fi
  printf '%s\n' "python"
}

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

PYTHON_BIN="$(resolve_python)"
COMPOSE_BIN="$(resolve_compose)"

cd "$ROOT"

echo "[verify] py_compile"
find src/ot_skill_enterprise -type f -name '*.py' -print0 | xargs -0 "$PYTHON_BIN" -m py_compile

if command -v "$PYTHON_BIN" >/dev/null 2>&1 && "$PYTHON_BIN" -m pytest --version >/dev/null 2>&1; then
  echo "[verify] qa regression tests"
  PYTHONPATH="${PYTHONPATH:-$ROOT/src}" "$PYTHON_BIN" -m pytest -q \
    "$ROOT/tests/test_agent_team_service.py" \
    "$ROOT/tests/test_style_distillation_archetype.py" \
    "$ROOT/tests/test_style_distillation_archetype_integration.py" \
    "$ROOT/tests/test_wallet_style_reflection.py::WalletStyleReflectionTests::test_parse_wallet_style_review_report_accepts_minimal_distill_output" \
    "$ROOT/tests/test_wallet_style_reflection.py::WalletStyleReflectionTests::test_parse_wallet_style_review_report_auto_fixes_wallet_chain" \
    "$ROOT/tests/test_wallet_style_reflection.py::WalletStyleReflectionTests::test_reflection_job_embeds_ephemeral_context_outside_system_prompt" \
    "$ROOT/tests/test_wallet_style_reflection.py::WalletStyleReflectionTests::test_reflection_job_sets_request_timeout_and_token_budget_metadata" \
    "$ROOT/tests/test_wallet_style_reflection.py::WalletStyleReflectionTests::test_reflection_job_uses_higher_default_token_budget" \
    "$ROOT/tests/test_wallet_style_reflection.py::WalletStyleReflectionTests::test_chain_benchmark_source_defaults_are_chain_specific" \
    "$ROOT/tests/test_wallet_style_reflection.py::WalletStyleReflectionTests::test_fallback_execution_intent_uses_chain_benchmark_source_defaults" \
    "$ROOT/tests/test_wallet_style_reflection.py::WalletStyleReflectionTests::test_reflection_run_does_not_generate_candidate" \
    "$ROOT/tests/test_qa_evaluator_status_semantics.py" \
    "$ROOT/tests/test_verify_script.py" >/dev/null
else
  echo "[verify] qa regression tests skipped (pytest unavailable)"
fi

if [[ -d "$ROOT/tests" ]]; then
  echo "[verify] unit tests"
  PYTHONPATH="${PYTHONPATH:-$ROOT/src}" "$PYTHON_BIN" -m unittest discover -s "$ROOT/tests" -p 'test_*.py' >/dev/null
fi

if [[ -d "$ROOT/vendor/pi_runtime/node_modules" ]]; then
  echo "[verify] pi runtime build"
  (
    cd "$ROOT/vendor/pi_runtime"
    "${OT_PI_NPM:-npm}" run build:ot-runtime >/dev/null
    node --check dist/pi-runtime.mjs >/dev/null
  )
else
  echo "[verify] pi runtime build skipped (vendor/pi_runtime/node_modules missing)"
fi

if [[ -f "$ROOT/docker-compose.yml" && -n "$COMPOSE_BIN" ]]; then
  echo "[verify] docker compose config"
  $COMPOSE_BIN -f "$ROOT/docker-compose.yml" config >/dev/null
else
  echo "[verify] docker compose config skipped (fallback mode)"
fi

if [[ -n "${OT_DB_DSN:-}" ]]; then
  echo "[verify] postgres schema"
  PYTHONPATH="${PYTHONPATH:-$ROOT/src}" "$PYTHON_BIN" - <<'PY'
import os
from pathlib import Path
from ot_skill_enterprise.storage import build_postgres_support, build_storage_settings

project_root = Path.cwd()
workspace_root = Path(os.getenv("OT_DEFAULT_WORKSPACE", ".ot-workspace")).resolve()
settings = build_storage_settings(project_root=project_root, workspace_root=workspace_root)
support = build_postgres_support(settings=settings)
support.ensure_schema()
print("postgres schema ok")
PY
else
  echo "[verify] postgres schema skipped (OT_DB_DSN not set)"
fi

if [[ -n "${OT_REDIS_URL:-}" ]]; then
  echo "[verify] redis projection cache"
  PYTHONPATH="${PYTHONPATH:-$ROOT/src}" "$PYTHON_BIN" - <<'PY'
import os
from pathlib import Path
from ot_skill_enterprise.storage import build_projection_cache, build_storage_settings

project_root = Path.cwd()
workspace_root = Path(os.getenv("OT_DEFAULT_WORKSPACE", ".ot-workspace")).resolve()
settings = build_storage_settings(project_root=project_root, workspace_root=workspace_root)
cache = build_projection_cache(settings=settings)
cache.set_json("ot:verify", {"status": "ok"}, ttl_seconds=5)
assert cache.get_json("ot:verify") == {"status": "ok"}
cache.delete_keys("ot:verify")
print("redis cache ok")
PY
else
  echo "[verify] redis projection cache skipped (OT_REDIS_URL not set)"
fi

if PYTHONPATH="${PYTHONPATH:-$ROOT/src}" "$PYTHON_BIN" - <<'PY' >/dev/null 2>&1
from ot_skill_enterprise.control_plane.api import build_control_plane_api
from ot_skill_enterprise.frontend_server import build_overview_payload

_ = build_control_plane_api
_ = build_overview_payload
PY
then
  echo "[verify] runtime overview"
  PYTHONPATH="${PYTHONPATH:-$ROOT/src}" "$PYTHON_BIN" -m ot_skill_enterprise.control_plane.cli runtime overview --workspace-dir "$WORKSPACE" >/dev/null

  echo "[verify] runtime sessions"
  PYTHONPATH="${PYTHONPATH:-$ROOT/src}" "$PYTHON_BIN" -m ot_skill_enterprise.control_plane.cli runtime sessions --workspace-dir "$WORKSPACE" >/dev/null

  echo "[verify] runtime active-runs"
  PYTHONPATH="${PYTHONPATH:-$ROOT/src}" "$PYTHON_BIN" -m ot_skill_enterprise.control_plane.cli runtime active-runs --workspace-dir "$WORKSPACE" >/dev/null

  PAYLOAD_FILE="$(mktemp)"
  cat > "$PAYLOAD_FILE" <<'JSON'
{
  "run_id": "external-smoke-run",
  "runtime_id": "external-runtime",
  "runtime_session_id": "external-session",
  "agent_id": "external-agent",
  "flow_id": "external-flow",
  "status": "succeeded",
  "ok": true,
  "summary": "external record-run smoke",
  "events": [
    {
      "event_id": "evt-1",
      "event_type": "runtime.completed",
      "status": "succeeded",
      "summary": "completed"
    }
  ]
}
JSON

  echo "[verify] external record-run"
  PYTHONPATH="${PYTHONPATH:-$ROOT/src}" "$PYTHON_BIN" -m ot_skill_enterprise.control_plane.cli runtime record-run --workspace-dir "$WORKSPACE" --payload-file "$PAYLOAD_FILE" >/dev/null
  rm -f "$PAYLOAD_FILE"

  FAIL_PAYLOAD_FILE="$(mktemp)"
  cat > "$FAIL_PAYLOAD_FILE" <<'JSON'
{
  "run_id": "external-failed-run",
  "runtime_id": "external-runtime",
  "runtime_session_id": "external-session-failed",
  "agent_id": "external-agent",
  "flow_id": "external-flow",
  "status": "failed",
  "ok": false,
  "summary": "failed smoke run for candidate generation",
  "events": [
    {
      "event_id": "evt-failed-1",
      "event_type": "run_failed",
      "status": "failed",
      "summary": "runtime failed"
    }
  ]
}
JSON

  echo "[verify] candidate generation source run"
  PYTHONPATH="${PYTHONPATH:-$ROOT/src}" "$PYTHON_BIN" -m ot_skill_enterprise.control_plane.cli runtime record-run --workspace-dir "$WORKSPACE" --payload-file "$FAIL_PAYLOAD_FILE" >/dev/null
  rm -f "$FAIL_PAYLOAD_FILE"

  BAD_PAYLOAD_FILE="$(mktemp)"
  cat > "$BAD_PAYLOAD_FILE" <<'JSON'
{
  "run_id": "external-smoke-run-missing-session",
  "runtime_id": "external-runtime",
  "agent_id": "external-agent",
  "flow_id": "external-flow",
  "status": "succeeded",
  "ok": true,
  "summary": "missing runtime_session_id should fail"
}
JSON

  echo "[verify] external record-run validation"
  if PYTHONPATH="${PYTHONPATH:-$ROOT/src}" "$PYTHON_BIN" -m ot_skill_enterprise.control_plane.cli runtime record-run --workspace-dir "$WORKSPACE" --payload-file "$BAD_PAYLOAD_FILE" >/dev/null 2>&1; then
    echo "[verify] expected missing runtime_session_id validation failure" >&2
    rm -f "$BAD_PAYLOAD_FILE"
    exit 1
  fi
  rm -f "$BAD_PAYLOAD_FILE"

  echo "[verify] frontend payload"
  PYTHONPATH="${PYTHONPATH:-$ROOT/src}" "$PYTHON_BIN" - <<'PY'
from pathlib import Path
from ot_skill_enterprise.frontend_server import build_overview_payload

payload = build_overview_payload(Path(".ot-workspace").resolve())
assert "runtime" in payload
assert "sessions" in payload
assert "active_runs" in payload
assert "evaluations" in payload
assert "candidates" in payload
assert "promotions" in payload
assert "runtime_context" in payload
assert "style_distillations" in payload
runtime = payload["runtime"]
assert "run_count" in runtime
assert "active_run_count" in runtime
assert "session_count" in runtime
print("frontend payload ok")
PY

  CANDIDATE_PAYLOAD_FILE="$(mktemp)"
  cat > "$CANDIDATE_PAYLOAD_FILE" <<'JSON'
{
  "candidate_id": "smoke-candidate",
  "candidate_slug": "smoke-candidate",
  "runtime_session_id": "smoke-session",
  "source_run_id": "smoke-run",
  "source_evaluation_id": "smoke-eval",
  "candidate_type": "prompt",
  "target_skill_name": "smoke generated skill",
  "target_skill_kind": "skill",
  "change_summary": "smoke candidate package",
  "generation_spec": {
    "source": "verify-smoke"
  },
  "manifest_preview": {},
  "status": "draft",
  "validation_status": "pending",
  "metadata": {}
}
JSON

  echo "[verify] candidate compile"
  PYTHONPATH="${PYTHONPATH:-$ROOT/src}" "$PYTHON_BIN" -m ot_skill_enterprise.control_plane.cli candidate compile --workspace-dir "$WORKSPACE" --payload-file "$CANDIDATE_PAYLOAD_FILE" >/dev/null

  echo "[verify] candidate list"
  PYTHONPATH="${PYTHONPATH:-$ROOT/src}" "$PYTHON_BIN" -m ot_skill_enterprise.control_plane.cli candidate list --workspace-dir "$WORKSPACE" >/dev/null

  echo "[verify] candidate validate"
  PYTHONPATH="${PYTHONPATH:-$ROOT/src}" "$PYTHON_BIN" -m ot_skill_enterprise.control_plane.cli candidate validate --workspace-dir "$WORKSPACE" --candidate-id smoke-candidate >/dev/null

  echo "[verify] candidate promote"
  PYTHONPATH="${PYTHONPATH:-$ROOT/src}" "$PYTHON_BIN" -m ot_skill_enterprise.control_plane.cli candidate promote --workspace-dir "$WORKSPACE" --candidate-id smoke-candidate >/dev/null

  echo "[verify] wallet style distill"
  AVE_DATA_PROVIDER=mock OT_PI_REFLECTION_MOCK=1 PYTHONPATH="${PYTHONPATH:-$ROOT/src}" "$PYTHON_BIN" -m ot_skill_enterprise.control_plane.cli style distill --workspace-dir "$WORKSPACE" --wallet 0xverifywallet0001 --chain solana >/dev/null

  echo "[verify] wallet style list"
  AVE_DATA_PROVIDER=mock OT_PI_REFLECTION_MOCK=1 PYTHONPATH="${PYTHONPATH:-$ROOT/src}" "$PYTHON_BIN" -m ot_skill_enterprise.control_plane.cli style list --workspace-dir "$WORKSPACE" >/dev/null

  echo "[verify] wallet style reflection lineage"
  PYTHONPATH="${PYTHONPATH:-$ROOT/src}" "$PYTHON_BIN" - <<'PY'
import json
from pathlib import Path

workspace = Path(".ot-workspace").resolve()
items = list((workspace / "style-distillations").glob("*/summary.json"))
assert items, "expected at least one style distillation summary"
payload = json.loads(max(items, key=lambda p: p.stat().st_mtime).read_text(encoding="utf-8"))
reflection = payload.get("summary") or payload.get("reflection") or payload
assert reflection["review_backend"]
assert reflection["reflection_flow_id"] == "wallet_style_reflection_review"
assert reflection["reflection_run_id"]
assert reflection["reflection_session_id"]
assert reflection["reflection_status"] in {"succeeded", "failed", "fallback"}
assert "fallback_used" in reflection
print("wallet style reflection lineage ok")
PY

  rm -f "$CANDIDATE_PAYLOAD_FILE"
else
  echo "[verify] python runtime checks skipped because control plane imports are unavailable"
fi

echo "[verify] success"
