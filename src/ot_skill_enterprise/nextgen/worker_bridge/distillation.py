from __future__ import annotations

import json
from itertools import count
from typing import Any

from ot_skill_enterprise.nextgen.plugins import WorkflowPluginRegistry
from ot_skill_enterprise.nextgen.workflows.models import WorkflowArtifact, WorkflowRunRequest, WorkflowVariant
from ot_skill_enterprise.shared.contracts.common import ArtifactRef

from .models import (
    DistillationWorkerBridgeEvent,
    DistillationWorkerBridgeRequest,
    DistillationWorkerBridgeResponse,
    DistillationWorkerOperation,
    DistillationWorkerProtocol,
)

_DEFAULT_OPERATION_ORDER: tuple[DistillationWorkerOperation, ...] = ("plan", "execute", "validate", "summarize")
_DEFAULT_CAPABILITY_BINDINGS: dict[DistillationWorkerOperation, str] = {
    "plan": "distill_wallet_style",
    "execute": "emit_seed_skill",
    "validate": "emit_seed_skill",
    "summarize": "emit_seed_skill",
}
_DEFAULT_BASELINE_ARTIFACT_KINDS = (
    "style_profile",
    "strategy_spec",
    "execution_intent",
    "distillation_report",
    "seed_skill_package",
)


def _workflow_artifact(kind: str, label: str, payload: dict[str, Any]) -> WorkflowArtifact:
    return WorkflowArtifact(
        ref=ArtifactRef(
            artifact_id=f"{kind}:{label}",
            kind=kind,
            label=label,
            metadata={"source": "nextgen-distillation-worker-bridge"},
        ),
        payload=dict(payload),
    )


def _string(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _normalize_result(result: dict[str, Any], request: DistillationWorkerBridgeRequest) -> tuple[WorkflowVariant, list[WorkflowArtifact], dict[str, Any]]:
    raw_result = _json_safe(dict(result))
    title = (
        _string(request.skill_name)
        or _string(raw_result.get("promotion", {}).get("skill_slug"))
        or _string(raw_result.get("candidate", {}).get("candidate_slug"))
        or "baseline-skill"
    )
    baseline_variant = WorkflowVariant(
        variant_id="baseline",
        title=title,
        source="distillation",
        status="seeded",
        style_profile=dict(raw_result.get("profile") or {}),
        strategy_spec=dict(raw_result.get("strategy") or {}),
        execution_intent=dict(raw_result.get("execution_intent") or {}),
        artifacts=[
            _workflow_artifact("distillation_report", "distillation-report", dict(raw_result.get("summary") or {})),
            _workflow_artifact("seed_skill_package", "seed-skill-package", dict(raw_result.get("package") or {})),
        ],
        metadata={
            "wallet": request.wallet,
            "chain": request.chain,
            "job_id": raw_result.get("job_id"),
            "candidate": dict(raw_result.get("candidate") or {}),
            "promotion": dict(raw_result.get("promotion") or {}),
            "qa": dict(raw_result.get("qa") or {}),
            "backtest": dict(raw_result.get("backtest") or {}),
            "review": dict(raw_result.get("review") or {}),
            "artifacts": dict(raw_result.get("artifacts") or {}),
        },
    )
    artifacts = [
        _workflow_artifact("style_profile", "baseline-style-profile", baseline_variant.style_profile),
        _workflow_artifact("strategy_spec", "baseline-strategy", baseline_variant.strategy_spec),
        _workflow_artifact("execution_intent", "baseline-execution-intent", baseline_variant.execution_intent),
        _workflow_artifact("distillation_report", "distillation-report", dict(raw_result.get("summary") or {})),
        _workflow_artifact("seed_skill_package", "seed-skill-package", dict(raw_result.get("package") or {})),
    ]
    compat_payload = {request.protocol.compat_result_key: raw_result}
    return baseline_variant, artifacts, compat_payload


def _variant_from_state(state: dict[str, Any]) -> WorkflowVariant | None:
    payload = state.get("baseline_variant")
    if not isinstance(payload, dict):
        return None
    return WorkflowVariant.model_validate(payload)


def _artifacts_from_state(state: dict[str, Any]) -> list[WorkflowArtifact]:
    items = state.get("artifacts")
    if not isinstance(items, list):
        return []
    return [WorkflowArtifact.model_validate(item) for item in items if isinstance(item, dict)]


def _raw_result_from_state(state: dict[str, Any]) -> dict[str, Any]:
    payload = state.get("raw_result")
    return dict(payload) if isinstance(payload, dict) else {}


def load_distillation_worker_protocol(
    plugin_registry: WorkflowPluginRegistry,
    *,
    workflow_id: str = "distillation_seed",
    step_id: str | None = None,
) -> DistillationWorkerProtocol:
    workflow = plugin_registry.resolve_workflow(workflow_id)
    distillation_step = None
    for step in workflow.steps:
        if step.plugin_id != "distillation":
            continue
        if step_id is not None and step.step_id != step_id:
            continue
        distillation_step = step
        break
    if distillation_step is None:
        raise KeyError(f"workflow {workflow_id!r} does not define a distillation step")
    plugin = plugin_registry.resolve_plugin("distillation")
    plugin_bridge = dict(plugin.metadata.get("worker_bridge") or {})
    step_bridge = dict(distillation_step.metadata.get("worker_bridge") or {})
    capability_bindings = {
        **_DEFAULT_CAPABILITY_BINDINGS,
        **dict(plugin_bridge.get("capability_bindings") or {}),
        **dict(step_bridge.get("capability_bindings") or {}),
    }
    operation_order = list(step_bridge.get("operation_order") or plugin_bridge.get("operation_order") or _DEFAULT_OPERATION_ORDER)
    baseline_artifact_kinds = list(
        step_bridge.get("baseline_artifact_kinds")
        or plugin_bridge.get("baseline_artifact_kinds")
        or _DEFAULT_BASELINE_ARTIFACT_KINDS
    )
    return DistillationWorkerProtocol(
        protocol_id=_string(step_bridge.get("protocol_id") or plugin_bridge.get("protocol_id"), "distillation.wallet_style"),
        plugin_version=plugin.plugin_version,
        workflow_id=workflow.workflow_id,
        workflow_step_id=distillation_step.step_id,
        operation_order=operation_order,
        capability_bindings=capability_bindings,
        baseline_artifact_kinds=baseline_artifact_kinds,
        compat_result_key=_string(step_bridge.get("compat_result_key") or plugin_bridge.get("compat_result_key"), "raw_distillation_result"),
        metadata={
            "plugin_display_name": plugin.display_name,
            "plugin_summary": plugin.summary,
            "step_title": distillation_step.title,
            "step_description": distillation_step.description,
            "stage": distillation_step.stage,
        },
    )


class DistillationWorkerHandler:
    def __init__(self, distillation_service: Any) -> None:
        self._distillation_service = distillation_service
        self._event_counter = count(1)

    def _event(
        self,
        *,
        request: DistillationWorkerBridgeRequest,
        summary: str,
        status: str,
        artifact_ids: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> DistillationWorkerBridgeEvent:
        return DistillationWorkerBridgeEvent(
            event_id=f"{request.workflow_step_id}:{request.operation}:{next(self._event_counter)}",
            event_type=f"worker_bridge.distillation.{request.operation}",
            operation=request.operation,
            status=status,
            summary=summary,
            artifact_ids=list(artifact_ids or []),
            metadata=dict(metadata or {}),
        )

    def invoke(self, request: DistillationWorkerBridgeRequest) -> DistillationWorkerBridgeResponse:
        method = getattr(self, f"_handle_{request.operation}")
        return method(request)

    def run_protocol(
        self,
        *,
        workflow_request: WorkflowRunRequest,
        protocol: DistillationWorkerProtocol,
    ) -> DistillationWorkerBridgeResponse:
        state: dict[str, Any] = {}
        responses: list[DistillationWorkerBridgeResponse] = []
        for operation in protocol.operation_order:
            response = self.invoke(
                DistillationWorkerBridgeRequest(
                    plugin_version=protocol.plugin_version,
                    workflow_id=workflow_request.workflow_id,
                    workflow_step_id=protocol.workflow_step_id,
                    operation=operation,
                    capability_id=protocol.capability_bindings[operation],
                    wallet=_string(workflow_request.wallet),
                    chain=workflow_request.chain,
                    skill_name=workflow_request.skill_name,
                    workspace_dir=workflow_request.workspace_dir,
                    operator_hints=dict(workflow_request.operator_hints),
                    protocol=protocol,
                    state=state,
                    metadata={
                        **dict(workflow_request.metadata),
                        "objective": workflow_request.objective,
                        "iteration_budget": workflow_request.iteration_budget,
                    },
                )
            )
            responses.append(response)
            state = dict(response.state)
        final_response = responses[-1]
        final_response.events = [event for item in responses for event in item.events]
        final_response.metadata = {
            **dict(final_response.metadata),
            "operation_order": list(protocol.operation_order),
            "operation_count": len(responses),
        }
        return final_response

    def _handle_plan(self, request: DistillationWorkerBridgeRequest) -> DistillationWorkerBridgeResponse:
        summary = f"Planned distillation worker execution for {request.wallet} on {request.chain}."
        plan_payload = {
            "wallet": request.wallet,
            "chain": request.chain,
            "skill_name": request.skill_name,
            "capability_id": request.capability_id,
            "operation_order": list(request.protocol.operation_order),
            "baseline_artifact_kinds": list(request.protocol.baseline_artifact_kinds),
        }
        state = {
            **dict(request.state),
            "plan": plan_payload,
        }
        return DistillationWorkerBridgeResponse(
            plugin_version=request.plugin_version,
            workflow_id=request.workflow_id,
            workflow_step_id=request.workflow_step_id,
            operation=request.operation,
            status="planned",
            summary=summary,
            events=[self._event(request=request, summary=summary, status="planned", metadata=plan_payload)],
            state=state,
            metadata={"plan": plan_payload},
        )

    def _handle_execute(self, request: DistillationWorkerBridgeRequest) -> DistillationWorkerBridgeResponse:
        raw_result = self._distillation_service.distill_wallet_style(
            wallet=request.wallet,
            chain=request.chain,
            skill_name=request.skill_name,
            max_attempts=1,
            live_execute=False,
            approval_granted=False,
        )
        baseline_variant, artifacts, compat_payload = _normalize_result(dict(raw_result), request)
        summary = _string(
            dict(raw_result.get("summary") or {}).get("summary")
            or raw_result.get("summary")
            or baseline_variant.title,
            "Distillation execution completed.",
        )
        state = {
            **dict(request.state),
            "baseline_variant": baseline_variant.model_dump(mode="json"),
            "artifacts": [item.model_dump(mode="json") for item in artifacts],
            "raw_result": dict(raw_result),
            "compat_payload": compat_payload,
        }
        artifact_ids = [item.ref.artifact_id for item in artifacts]
        return DistillationWorkerBridgeResponse(
            plugin_version=request.plugin_version,
            workflow_id=request.workflow_id,
            workflow_step_id=request.workflow_step_id,
            operation=request.operation,
            status="succeeded",
            summary=summary,
            baseline_variant=baseline_variant,
            artifacts=artifacts,
            raw_result=dict(raw_result),
            compat_payload=compat_payload,
            events=[self._event(request=request, summary=summary, status="succeeded", artifact_ids=artifact_ids)],
            state=state,
            metadata={
                "job_id": raw_result.get("job_id"),
                "compat_result_key": request.protocol.compat_result_key,
            },
        )

    def _handle_validate(self, request: DistillationWorkerBridgeRequest) -> DistillationWorkerBridgeResponse:
        baseline_variant = _variant_from_state(request.state)
        artifacts = _artifacts_from_state(request.state)
        raw_result = _raw_result_from_state(request.state)
        artifact_kinds = {item.ref.kind for item in artifacts}
        checks = [
            {"check": "raw_result_present", "passed": bool(raw_result)},
            {"check": "baseline_variant_present", "passed": baseline_variant is not None},
            {
                "check": "baseline_artifacts_present",
                "passed": set(request.protocol.baseline_artifact_kinds).issubset(artifact_kinds),
                "artifact_kinds": sorted(artifact_kinds),
            },
            {
                "check": "strategy_spec_present",
                "passed": bool(baseline_variant and baseline_variant.strategy_spec),
            },
        ]
        passed = all(bool(item.get("passed")) for item in checks)
        summary = "Distillation worker validation passed." if passed else "Distillation worker validation failed."
        validation_payload = {"passed": passed, "checks": checks}
        state = {
            **dict(request.state),
            "validation": validation_payload,
        }
        return DistillationWorkerBridgeResponse(
            plugin_version=request.plugin_version,
            workflow_id=request.workflow_id,
            workflow_step_id=request.workflow_step_id,
            operation=request.operation,
            status="validated" if passed else "failed",
            summary=summary,
            baseline_variant=baseline_variant,
            artifacts=artifacts,
            raw_result=raw_result,
            compat_payload=dict(request.state.get("compat_payload") or {}),
            events=[self._event(request=request, summary=summary, status="validated" if passed else "failed", metadata=validation_payload)],
            state=state,
            metadata={"validation": validation_payload},
        )

    def _handle_summarize(self, request: DistillationWorkerBridgeRequest) -> DistillationWorkerBridgeResponse:
        baseline_variant = _variant_from_state(request.state)
        artifacts = _artifacts_from_state(request.state)
        raw_result = _raw_result_from_state(request.state)
        validation_payload = dict(request.state.get("validation") or {})
        summary_payload = {
            "wallet": request.wallet,
            "chain": request.chain,
            "skill_title": baseline_variant.title if baseline_variant is not None else request.skill_name,
            "job_id": raw_result.get("job_id"),
            "artifact_count": len(artifacts),
            "validation_passed": bool(validation_payload.get("passed")),
            "summary": _string(
                dict(raw_result.get("summary") or {}).get("summary")
                or raw_result.get("summary")
                or (baseline_variant.title if baseline_variant is not None else "distillation"),
                "Distillation worker summary generated.",
            ),
        }
        compat_payload = dict(request.state.get("compat_payload") or {})
        state = {
            **dict(request.state),
            "summary": summary_payload,
        }
        return DistillationWorkerBridgeResponse(
            plugin_version=request.plugin_version,
            workflow_id=request.workflow_id,
            workflow_step_id=request.workflow_step_id,
            operation=request.operation,
            status="summarized",
            summary=_string(summary_payload.get("summary"), "Distillation worker summary generated."),
            baseline_variant=baseline_variant,
            artifacts=artifacts,
            raw_result=raw_result,
            compat_payload=compat_payload,
            events=[self._event(request=request, summary=_string(summary_payload.get("summary"), "Distillation worker summary generated."), status="summarized", metadata=summary_payload)],
            state=state,
            metadata={"summary": summary_payload},
        )


def build_distillation_worker_handler(distillation_service: Any) -> DistillationWorkerHandler:
    return DistillationWorkerHandler(distillation_service)
