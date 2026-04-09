from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, MutableMapping, Optional
from uuid import uuid4

from pydantic import Field

from ot_skill_enterprise.shared.contracts import ArtifactRef, ServiceError
from ot_skill_enterprise.shared.contracts.common import ContractModel, utc_now

from .presets import (
    ANALYSIS_CORE_SKILL_ID,
    AVE_DATA_GATEWAY_SKILL_ID,
    WorkflowPreset,
    WorkflowStep,
    get_workflow_preset,
    validate_workflow_preset,
)


WorkflowStepHandler = Callable[["WorkflowStep", dict[str, Any], "WorkflowRunContext"], Any]
HandlerRegistry = Mapping[tuple[str, str] | str, WorkflowStepHandler]


class WorkflowStepRun(ContractModel):
    step_id: str = Field(min_length=1)
    skill_id: str = Field(min_length=1)
    action_id: str = Field(min_length=1)
    status: str = Field(min_length=1)
    ok: bool = True
    summary: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    error: Optional[ServiceError] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime = Field(default_factory=utc_now)


class WorkflowRunResult(ContractModel):
    run_id: str = Field(min_length=1)
    preset_id: str = Field(min_length=1)
    preset_title: str = Field(min_length=1)
    status: str = Field(min_length=1)
    ok: bool = True
    summary: str = Field(min_length=1)
    inputs: dict[str, Any] = Field(default_factory=dict)
    executed_steps: list[WorkflowStepRun] = Field(default_factory=list)
    artifact_refs: list[ArtifactRef] = Field(default_factory=list)
    failure_step_id: Optional[str] = None
    failure_summary: Optional[str] = None
    failure: Optional[ServiceError] = None
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


@dataclass
class WorkflowRunContext:
    run_id: str
    preset: WorkflowPreset
    inputs: dict[str, Any]
    workspace_dir: Path | None
    step_outputs: MutableMapping[str, dict[str, Any]] = field(default_factory=dict)
    artifact_refs: list[ArtifactRef] = field(default_factory=list)

    def snapshot(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "preset_id": self.preset.preset_id,
            "inputs": dict(self.inputs),
            "step_outputs": {key: value for key, value in self.step_outputs.items()},
            "artifact_refs": [ref.model_dump(mode="json") for ref in self.artifact_refs],
        }


def _dump_model(value: Any) -> Any:
    dumper = getattr(value, "model_dump", None)
    if dumper is not None:
        return dumper(mode="json")
    return value


def _artifact_ref(artifact: Any) -> ArtifactRef:
    return ArtifactRef.model_validate(_dump_model(artifact))


def _unique_artifacts(items: list[ArtifactRef]) -> list[ArtifactRef]:
    seen: set[tuple[str, str | None, str | None]] = set()
    unique: list[ArtifactRef] = []
    for item in items:
        marker = (item.artifact_id, item.kind, item.uri)
        if marker in seen:
            continue
        seen.add(marker)
        unique.append(item)
    return unique


def _normalize_error(code: str, message: str, *, details: Optional[dict[str, Any]] = None) -> ServiceError:
    return ServiceError(code=code, message=message, details=details or {})


def _load_artifact_json(uri: str | None) -> dict[str, Any] | None:
    if not uri:
        return None
    path = Path(uri)
    if not path.exists() or path.suffix.lower() != ".json":
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # pragma: no cover - defensive artifact parsing
        return None


def _extract_artifact_payload(result: Mapping[str, Any]) -> dict[str, Any]:
    payload = result.get("payload")
    if isinstance(payload, dict):
        return payload

    data = result.get("data")
    if isinstance(data, dict):
        return data

    result_data = result.get("result")
    if isinstance(result_data, dict):
        return result_data

    response = result.get("response")
    if isinstance(response, dict):
        if isinstance(response.get("data"), dict):
            return response["data"]
        return response

    return {}


def _collect_artifact_refs(result: Mapping[str, Any]) -> list[ArtifactRef]:
    refs: list[ArtifactRef] = []
    for key in ("artifact_refs", "artifacts"):
        for item in result.get(key, []) or []:
            try:
                refs.append(_artifact_ref(item))
            except Exception:
                continue
    return _unique_artifacts(refs)


def _extract_step_output(result: Mapping[str, Any]) -> dict[str, Any]:
    payload = _extract_artifact_payload(result)
    if payload:
        return payload

    refs = _collect_artifact_refs(result)
    for ref in refs:
        body = _load_artifact_json(ref.uri)
        if not isinstance(body, dict):
            continue
        if isinstance(body.get("response"), dict) and isinstance(body["response"].get("data"), dict):
            return body["response"]["data"]
        if isinstance(body.get("payload"), dict):
            return body["payload"]
        return body

    return {}


def _first_string(*values: Any) -> Optional[str]:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            text = value.strip()
            if text:
                return text
        elif isinstance(value, dict):
            candidate = value.get("identifier") or value.get("token_address") or value.get("wallet_address") or value.get("wallet")
            if isinstance(candidate, str):
                text = candidate.strip()
                if text:
                    return text
    return None


def _token_ref_dict(value: Any) -> Optional[dict[str, Any]]:
    if value is None:
        return None
    if isinstance(value, dict):
        for key in ("identity", "token_ref"):
            nested = _token_ref_dict(value.get(key))
            if nested is not None:
                return nested
        identifier = _first_string(value.get("identifier"), value.get("token_address"), value.get("symbol"), value.get("name"))
        if identifier is None:
            return None
        payload = dict(value)
        payload.setdefault("identifier", identifier)
        return payload
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
        if isinstance(dumped, dict):
            return _token_ref_dict(dumped)
    if isinstance(value, str):
        text = value.strip()
        if text:
            return {"identifier": text}
    return None


def _extract_token_refs(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, list):
        collected: list[dict[str, Any]] = []
        for item in value:
            ref = _token_ref_dict(item)
            if ref is not None:
                collected.append(ref)
        return collected
    if isinstance(value, dict):
        for key in ("token_refs", "linked_token_refs", "holdings", "top_holding_token_refs", "token_candidates"):
            if key not in value:
                continue
            extracted = _extract_token_refs(value.get(key))
            if extracted:
                return extracted
        for key in ("identity", "token_ref"):
            ref = _token_ref_dict(value.get(key))
            if ref is not None:
                return [ref]
    ref = _token_ref_dict(value)
    return [ref] if ref is not None else []


def _wallet_address_from_value(value: Any) -> Optional[str]:
    if isinstance(value, dict):
        for key in ("wallet_address", "wallet", "identifier"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
    return _first_string(value)


def _find_previous_output(context: WorkflowRunContext, keys: tuple[str, ...]) -> dict[str, Any]:
    for key in keys:
        output = context.step_outputs.get(key)
        if isinstance(output, dict) and output:
            return output
    return {}


def _resolve_plan_payload(step: WorkflowStep, context: WorkflowRunContext) -> dict[str, Any]:
    allowed = {
        "topic": context.inputs.get("topic"),
        "objective": context.inputs.get("objective"),
        "scope": context.inputs.get("scope"),
        "questions": context.inputs.get("questions"),
        "focus_domains": context.inputs.get("focus_domains"),
        "request_id": context.inputs.get("request_id") or context.run_id,
        "workspace_hint": context.inputs.get("workspace_hint") or (str(context.workspace_dir) if context.workspace_dir else None),
        "metadata": context.inputs.get("metadata"),
    }
    return {key: value for key, value in allowed.items() if value is not None}


def _resolve_gateway_payload(step: WorkflowStep, context: WorkflowRunContext) -> dict[str, Any]:
    inputs = context.inputs
    previous = context.step_outputs
    payload: dict[str, Any] = {}

    if step.action_id == "discover_tokens":
        payload = {
            "query": _first_string(inputs.get("query"), inputs.get("topic"), inputs.get("objective")),
            "chain": _first_string(inputs.get("chain"), inputs.get("focus_domain")),
            "source": _first_string(inputs.get("source")),
            "limit": inputs.get("limit"),
        }
    elif step.action_id == "inspect_wallet":
        wallet = _wallet_address_from_value(inputs.get("wallet_address") or inputs.get("wallet"))
        payload = {
            "wallet": wallet,
            "wallet_address": wallet,
            "chain": _first_string(inputs.get("chain")),
            "include_holdings": True,
            "include_activity": True,
        }
    elif step.action_id == "inspect_token":
        source = _find_previous_output(context, ("inspect_wallet", "discover_tokens", "synthesize_evidence"))
        token_refs = _extract_token_refs(
            inputs.get("target_token_ref")
            or inputs.get("token_ref")
            or inputs.get("token")
            or inputs.get("address")
            or source
        )
        token = token_refs[0] if token_refs else _token_ref_dict(inputs.get("target_token_ref") or inputs.get("token_ref") or inputs.get("token") or inputs.get("address"))
        payload = {
            "token": token.get("identifier") if token else _first_string(inputs.get("token"), inputs.get("address")),
            "chain": _first_string(inputs.get("chain")),
            "include_holders": True,
            "include_risk": True,
        }
        if token_refs:
            payload["top_holding_token_refs"] = token_refs
    elif step.action_id == "inspect_market":
        source = _find_previous_output(context, ("discover_tokens", "inspect_wallet", "inspect_token"))
        token_refs = _extract_token_refs(
            inputs.get("token_candidates")
            or inputs.get("primary_holding_ref")
            or inputs.get("target_token_ref")
            or inputs.get("token_ref")
            or inputs.get("token")
            or inputs.get("address")
            or source
        )
        token = token_refs[0] if token_refs else _token_ref_dict(inputs.get("primary_holding_ref") or inputs.get("target_token_ref") or inputs.get("token_ref") or inputs.get("token") or inputs.get("address"))
        payload = {
            "token": token.get("identifier") if token else _first_string(inputs.get("token"), inputs.get("address")),
            "pair": _first_string(inputs.get("pair")),
            "chain": _first_string(inputs.get("chain")),
            "interval": _first_string(inputs.get("interval")) or "1h",
            "window": _first_string(inputs.get("analysis_window"), inputs.get("window")) or "24h",
        }
        if token_refs:
            payload["token_candidates"] = token_refs
    elif step.action_id == "review_signals":
        source = _find_previous_output(context, ("discover_tokens", "inspect_token", "inspect_wallet"))
        token_refs = _extract_token_refs(
            inputs.get("token_candidates")
            or inputs.get("target_token_ref")
            or inputs.get("token_ref")
            or inputs.get("token")
            or source
        )
        token = token_refs[0] if token_refs else _token_ref_dict(inputs.get("target_token_ref") or inputs.get("token_ref") or inputs.get("token"))
        payload = {
            "chain": _first_string(inputs.get("chain")),
            "limit": inputs.get("limit") or 20,
        }
        if token is not None:
            payload["token"] = token.get("identifier")
            payload["token_ref"] = token
        if token_refs:
            payload["token_candidates"] = token_refs
    else:
        payload = dict(inputs)

    return {key: value for key, value in payload.items() if value is not None}


def _normalize_step_result(step: WorkflowStep, raw_result: Any, payload: dict[str, Any], started_at: datetime) -> WorkflowStepRun:
    finished_at = utc_now()
    result = _dump_model(raw_result)
    if not isinstance(result, dict):
        result = {"value": result}

    artifacts = _collect_artifact_refs(result)
    ok = bool(result.get("ok", True))
    summary = str(result.get("summary") or step.purpose)
    error: Optional[ServiceError] = None
    if not ok:
        error_value = result.get("error")
        if isinstance(error_value, dict):
            error = ServiceError.model_validate(error_value)
        else:
            error = _normalize_error(
                "STEP_EXECUTION_FAILED",
                summary if summary else f"{step.step_id} failed",
                details={"step_id": step.step_id, "skill_id": step.skill_id, "action_id": step.action_id},
            )
    status = "succeeded" if ok else "failed"
    return WorkflowStepRun(
        step_id=step.step_id,
        skill_id=step.skill_id,
        action_id=step.action_id,
        status=status,
        ok=ok,
        summary=summary,
        payload=payload,
        result=result,
        artifacts=artifacts,
        error=error,
        metadata={"started_at": started_at.isoformat(), "finished_at": finished_at.isoformat()},
        started_at=started_at,
        finished_at=finished_at,
    )


def _failure_step_result(step: WorkflowStep, payload: dict[str, Any], exc: Exception, started_at: datetime) -> WorkflowStepRun:
    finished_at = utc_now()
    error = _normalize_error(
        "STEP_EXECUTION_ERROR",
        str(exc),
        details={"step_id": step.step_id, "skill_id": step.skill_id, "action_id": step.action_id},
    )
    return WorkflowStepRun(
        step_id=step.step_id,
        skill_id=step.skill_id,
        action_id=step.action_id,
        status="failed",
        ok=False,
        summary=f"{step.step_id} failed: {exc}",
        payload=payload,
        result={"exception": exc.__class__.__name__, "message": str(exc)},
        artifacts=[],
        error=error,
        metadata={"started_at": started_at.isoformat(), "finished_at": finished_at.isoformat()},
        started_at=started_at,
        finished_at=finished_at,
    )


def _result_envelope(
    *,
    run_id: str,
    preset: WorkflowPreset,
    inputs: dict[str, Any],
    executed_steps: list[WorkflowStepRun],
    started_at: datetime,
    finished_at: datetime,
    status: str,
    summary: str,
    failure_step_id: str | None = None,
    failure_summary: str | None = None,
    failure: ServiceError | None = None,
    metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    artifact_refs: list[ArtifactRef] = []
    for step in executed_steps:
        artifact_refs.extend(step.artifacts)
    artifact_refs = _unique_artifacts(artifact_refs)

    result = WorkflowRunResult(
        run_id=run_id,
        preset_id=preset.preset_id,
        preset_title=preset.title,
        status=status,
        ok=status == "succeeded",
        summary=summary,
        inputs=inputs,
        executed_steps=executed_steps,
        artifact_refs=artifact_refs,
        failure_step_id=failure_step_id,
        failure_summary=failure_summary,
        failure=failure,
        started_at=started_at,
        finished_at=finished_at,
        metadata=metadata or {},
    )
    return result.model_dump(mode="json")


class WorkflowRuntime:
    def __init__(
        self,
        handlers: Optional[HandlerRegistry] = None,
        *,
        workspace_dir: Path | str | None = None,
    ) -> None:
        self._handlers = dict(handlers or {})
        self._workspace_dir = Path(workspace_dir).expanduser().resolve() if workspace_dir is not None else None

    def _resolve_handler(self, step: WorkflowStep) -> WorkflowStepHandler | None:
        for key in ((step.skill_id, step.action_id), step.action_id):
            handler = self._handlers.get(key)
            if handler is not None:
                return handler
        return None

    def _resolve_step_payload(self, step: WorkflowStep, context: WorkflowRunContext) -> dict[str, Any]:
        if step.skill_id == ANALYSIS_CORE_SKILL_ID and step.action_id == "plan_data_needs":
            return _resolve_plan_payload(step, context)
        if step.skill_id == AVE_DATA_GATEWAY_SKILL_ID:
            return _resolve_gateway_payload(step, context)
        if step.skill_id == ANALYSIS_CORE_SKILL_ID and step.action_id in {"synthesize_evidence", "write_report"}:
            return {"request_id": context.inputs.get("request_id") or context.run_id}
        return dict(context.inputs)

    def run(
        self,
        preset: str | WorkflowPreset,
        inputs: Optional[Mapping[str, Any]] = None,
        *,
        run_id: Optional[str] = None,
        workspace_dir: Path | str | None = None,
    ) -> dict[str, Any]:
        preset_model = preset if isinstance(preset, WorkflowPreset) else get_workflow_preset(preset)
        preset_model = validate_workflow_preset(preset_model)

        started_at = utc_now()
        resolved_run_id: str | None = None
        if isinstance(inputs, Mapping):
            input_run_id = inputs.get("run_id")
            if isinstance(input_run_id, str) and input_run_id.strip():
                resolved_run_id = input_run_id.strip()
        if not resolved_run_id and isinstance(run_id, str) and run_id.strip():
            resolved_run_id = run_id.strip()
        if not resolved_run_id:
            resolved_run_id = uuid4().hex
        resolved_inputs = dict(inputs or {})
        resolved_inputs.setdefault("request_id", resolved_run_id)

        runtime = WorkflowRunContext(
            run_id=resolved_run_id,
            preset=preset_model,
            inputs=resolved_inputs,
            workspace_dir=Path(workspace_dir).expanduser().resolve()
            if workspace_dir is not None
            else self._workspace_dir,
        )

        executed_steps: list[WorkflowStepRun] = []
        for step in preset_model.steps:
            payload = self._resolve_step_payload(step, runtime)
            handler = self._resolve_handler(step)
            step_started_at = utc_now()
            if handler is None:
                step_result = _failure_step_result(
                    step,
                    payload,
                    ValueError(f"no handler registered for {step.skill_id}.{step.action_id}"),
                    step_started_at,
                )
                executed_steps.append(step_result)
                return _result_envelope(
                    run_id=resolved_run_id,
                    preset=preset_model,
                    inputs=resolved_inputs,
                    executed_steps=executed_steps,
                    started_at=started_at,
                    finished_at=utc_now(),
                    status="failed",
                    summary=f"{preset_model.preset_id} failed before completing {step.step_id}",
                    failure_step_id=step.step_id,
                    failure_summary=step_result.summary,
                    failure=step_result.error,
                    metadata=runtime.snapshot(),
                )

            try:
                raw_result = handler(step, payload, runtime)
                step_result = _normalize_step_result(step, raw_result, payload, step_started_at)
            except Exception as exc:  # pragma: no cover - defensive path
                step_result = _failure_step_result(step, payload, exc, step_started_at)

            executed_steps.append(step_result)
            runtime.step_outputs[step.step_id] = _extract_step_output(step_result.result)
            runtime.artifact_refs.extend(step_result.artifacts)

            if not step_result.ok:
                return _result_envelope(
                    run_id=resolved_run_id,
                    preset=preset_model,
                    inputs=resolved_inputs,
                    executed_steps=executed_steps,
                    started_at=started_at,
                    finished_at=utc_now(),
                    status="failed",
                    summary=f"{preset_model.preset_id} failed at {step.step_id}",
                    failure_step_id=step.step_id,
                    failure_summary=step_result.summary,
                    failure=step_result.error,
                    metadata=runtime.snapshot(),
                )

        summary = f"{preset_model.preset_id} completed successfully"
        return _result_envelope(
            run_id=resolved_run_id,
            preset=preset_model,
            inputs=resolved_inputs,
            executed_steps=executed_steps,
            started_at=started_at,
            finished_at=utc_now(),
            status="succeeded",
            summary=summary,
            metadata=runtime.snapshot(),
        )


def run_workflow(
    preset: str | WorkflowPreset,
    inputs: Optional[Mapping[str, Any]] = None,
    *,
    handlers: Optional[HandlerRegistry] = None,
    workspace_dir: Path | str | None = None,
    run_id: Optional[str] = None,
) -> dict[str, Any]:
    runtime = WorkflowRuntime(handlers=handlers, workspace_dir=workspace_dir)
    return runtime.run(preset, inputs, run_id=run_id)
