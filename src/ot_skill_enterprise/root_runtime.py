from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Optional
from uuid import uuid4

from ot_skill_enterprise.analysis import plan_data_needs, synthesize_evidence, write_report
from ot_skill_enterprise.gateway import run_action
from ot_skill_enterprise.service_entrypoints import build_ave_provider
from ot_skill_enterprise.workflows import WorkflowRuntime


def _dump_model(value: Any) -> Any:
    dumper = getattr(value, "model_dump", None)
    if dumper is not None:
        return dumper(mode="json")
    return value


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(_dump_model(value), ensure_ascii=False, default=str))


class LocalAveDataClient:
    def __init__(self, provider: Any | None = None) -> None:
        self.provider = provider or build_ave_provider()

    def _call(self, operation: str, request: Any) -> dict[str, Any]:
        handler = getattr(self.provider, operation)
        request_id = f"{operation}-{uuid4().hex[:8]}"
        data = handler(request)
        return {
            "ok": True,
            "operation": operation,
            "request_id": request_id,
            "data": _json_safe(data),
            "meta": {
                "provider": str(getattr(self.provider, "name", "local")),
                "request_id": request_id,
            },
            "error": None,
        }

    def discover_tokens(self, request: Any) -> dict[str, Any]:
        return self._call("discover_tokens", request)

    def inspect_token(self, request: Any) -> dict[str, Any]:
        return self._call("inspect_token", request)

    def inspect_market(self, request: Any) -> dict[str, Any]:
        return self._call("inspect_market", request)

    def inspect_wallet(self, request: Any) -> dict[str, Any]:
        return self._call("inspect_wallet", request)

    def review_signals(self, request: Any) -> dict[str, Any]:
        return self._call("review_signals", request)


def build_workflow_handlers(
    *,
    workspace_dir: Path | str,
    client: Any | None = None,
) -> dict[tuple[str, str], Any]:
    workspace = Path(workspace_dir).expanduser().resolve()
    runtime_client = client or LocalAveDataClient()
    return {
        ("analysis-core", "plan_data_needs"): lambda step, payload, context: plan_data_needs(payload, workspace_dir=workspace),
        ("analysis-core", "synthesize_evidence"): lambda step, payload, context: synthesize_evidence(payload, workspace_dir=workspace),
        ("analysis-core", "write_report"): lambda step, payload, context: write_report(payload, workspace_dir=workspace),
        ("ave-data-gateway", "discover_tokens"): lambda step, payload, context: run_action("discover_tokens", payload, client=runtime_client, workspace_dir=workspace),
        ("ave-data-gateway", "inspect_token"): lambda step, payload, context: run_action("inspect_token", payload, client=runtime_client, workspace_dir=workspace),
        ("ave-data-gateway", "inspect_market"): lambda step, payload, context: run_action("inspect_market", payload, client=runtime_client, workspace_dir=workspace),
        ("ave-data-gateway", "inspect_wallet"): lambda step, payload, context: run_action("inspect_wallet", payload, client=runtime_client, workspace_dir=workspace),
        ("ave-data-gateway", "review_signals"): lambda step, payload, context: run_action("review_signals", payload, client=runtime_client, workspace_dir=workspace),
    }


def run_preset_workflow(
    preset: str,
    inputs: Mapping[str, Any],
    *,
    workspace_dir: Path | str,
    client: Any | None = None,
) -> dict[str, Any]:
    workspace = Path(workspace_dir).expanduser().resolve()
    runtime = WorkflowRuntime(
        handlers=build_workflow_handlers(workspace_dir=workspace, client=client),
        workspace_dir=workspace,
    )
    run_id = str(inputs.get("run_id") or uuid4().hex)
    return runtime.run(preset, dict(inputs), run_id=run_id, workspace_dir=workspace)


def _load_inputs(value: str | None, value_file: str | None) -> dict[str, Any]:
    if value_file:
        return json.loads(Path(value_file).read_text(encoding="utf-8"))
    if value:
        return json.loads(value)
    return {}
