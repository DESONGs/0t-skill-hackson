from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Literal, Mapping

from pydantic import Field

from ot_skill_enterprise.shared.contracts.common import ContractModel, ServiceError
from ot_skill_enterprise.skills_compiler import build_skill_package_compiler
from ot_skill_enterprise.style_distillation import build_wallet_style_distillation_service

from ..adapters import AdapterRegistry, build_builtin_adapter_registry
from ..plugins import WorkflowPluginRegistry, build_default_plugin_registry
from ..workflows.executors import (
    ApprovalConvergencePluginExecutor,
    AutoresearchPluginExecutor,
    BenchmarkPluginExecutor,
    ReviewPluginExecutor,
    SkillCreationPluginExecutor,
)
from ..workflows.models import (
    ApprovalConvergenceResult,
    BenchmarkScorecard,
    RecommendationBundle,
    ResearchSessionState,
    ResearchStopDecision,
    ReviewDecision,
    ResearchVariantPlan,
    WorkflowArtifact,
    WorkflowRunRequest,
    WorkflowVariant,
)
from ..workflows.store import ResearchLoopStore
from .distillation import DistillationWorkerHandler, build_distillation_worker_handler
from .models import DistillationWorkerProtocol


WorkerOperation = Literal["plan", "execute", "validate", "summarize"]


def _default_project_root() -> Path:
    return Path(__file__).resolve().parents[4]


class WorkerBridgeEvent(ContractModel):
    operation: WorkerOperation
    status: str = Field(min_length=1)
    message: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkerBridgeInvocationRequest(ContractModel):
    contract_version: str = Field(default="nextgen.worker.request.v1", min_length=1)
    bridge_id: str = Field(min_length=1)
    bridge_version: str = Field(min_length=1)
    action_id: str = Field(min_length=1)
    workflow_id: str = Field(min_length=1)
    workflow_step_id: str = Field(min_length=1)
    workspace_dir: str | None = None
    request: WorkflowRunRequest
    state: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkerBridgeInvocationResponse(ContractModel):
    contract_version: str = Field(default="nextgen.worker.response.v1", min_length=1)
    bridge_id: str = Field(min_length=1)
    bridge_version: str = Field(min_length=1)
    action_id: str = Field(min_length=1)
    workflow_id: str = Field(min_length=1)
    workflow_step_id: str = Field(min_length=1)
    operation: WorkerOperation
    status: str = Field(min_length=1)
    ok: bool = True
    outputs: dict[str, Any] = Field(default_factory=dict)
    state_patch: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[WorkflowArtifact] = Field(default_factory=list)
    events: list[WorkerBridgeEvent] = Field(default_factory=list)
    compat_payload: dict[str, Any] = Field(default_factory=dict)
    raw_result: dict[str, Any] = Field(default_factory=dict)
    error: ServiceError | None = None
    control: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


def _variant_from_state(state: dict[str, Any], key: str) -> WorkflowVariant:
    return WorkflowVariant.model_validate(state[key])


def _variants_from_state(state: dict[str, Any], key: str) -> list[WorkflowVariant]:
    return [WorkflowVariant.model_validate(item) for item in list(state.get(key) or [])]


def _scorecards_from_state(state: dict[str, Any], key: str) -> list[BenchmarkScorecard]:
    return [BenchmarkScorecard.model_validate(item) for item in list(state.get(key) or [])]


def _candidate_variants_for_request(state: dict[str, Any]) -> list[WorkflowVariant]:
    variants = _variants_from_state(state, "candidate_variants")
    if variants:
        return variants
    selected = state.get("selected_variant")
    if isinstance(selected, Mapping):
        return [WorkflowVariant.model_validate(selected)]
    return []


def _candidate_scorecards_for_request(state: dict[str, Any]) -> list[BenchmarkScorecard]:
    scorecards = _scorecards_from_state(state, "candidate_scorecards")
    if scorecards:
        return scorecards
    finalist = state.get("benchmark_scorecard")
    if isinstance(finalist, Mapping):
        return [BenchmarkScorecard.model_validate(finalist)]
    return []


def _baseline_scorecard_for_request(state: dict[str, Any]) -> BenchmarkScorecard:
    if isinstance(state.get("baseline_scorecard"), Mapping):
        return BenchmarkScorecard.model_validate(state["baseline_scorecard"])
    recommendation = state.get("recommendation_bundle")
    if isinstance(recommendation, Mapping):
        baseline_variant_id = str(recommendation.get("baseline_variant_id") or "").strip()
        for item in _scorecards_from_state(state, "scorecards"):
            if not baseline_variant_id or item.variant_id == baseline_variant_id:
                return item
    raise ValueError("review worker requires baseline_scorecard in state")


def _request_hints(request: WorkflowRunRequest) -> dict[str, bool]:
    operator_hints = dict(request.operator_hints)
    metadata = dict(request.metadata)
    return {
        "approval_granted": bool(operator_hints.get("approval_granted") or metadata.get("approval_granted")),
        "activation_requested": bool(
            operator_hints.get("activation_requested")
            or metadata.get("activation_requested")
            or metadata.get("activate")
        ),
    }

class WorkflowWorkerRuntime:
    def __init__(
        self,
        *,
        project_root: Path | None = None,
        workspace_root: Path | None = None,
        plugin_registry: WorkflowPluginRegistry | None = None,
        adapter_registry: AdapterRegistry | None = None,
        distillation_service: Any | None = None,
    ) -> None:
        self.project_root = (project_root or _default_project_root()).expanduser().resolve()
        self.workspace_root = (
            Path(workspace_root).expanduser().resolve()
            if workspace_root is not None
            else (self.project_root / ".ot-workspace").resolve()
        )
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.plugin_registry = plugin_registry or build_default_plugin_registry(project_root=self.project_root)
        self.adapter_registry = adapter_registry or build_builtin_adapter_registry(project_root=self.project_root)
        self.store = ResearchLoopStore(self.workspace_root)
        self.compiler = build_skill_package_compiler(self.project_root, self.workspace_root)
        self._distillation_service_override = distillation_service
        self._distillation_handler_adapter_id: str | None = None
        self.distillation_handler = (
            build_distillation_worker_handler(distillation_service)
            if distillation_service is not None
            else None
        )
        self.autoresearch_executor = AutoresearchPluginExecutor(self.plugin_registry, self.store)
        self.benchmark_executor = BenchmarkPluginExecutor(self.plugin_registry, self.adapter_registry, self.compiler, self.store)
        self.review_executor = ReviewPluginExecutor(self.plugin_registry, self.store)
        self.skill_creation_executor = SkillCreationPluginExecutor(self.compiler, self.store)
        self.approval_convergence_executor = ApprovalConvergencePluginExecutor(self.plugin_registry, self.store)
        self._action_handlers = {
            "distillation.execute": self._run_distillation,
            "skill_creation.materialize_baseline": self._run_skill_creation_materialize_baseline,
            "benchmark.score_baseline": self._run_benchmark_baseline,
            "autoresearch.plan_iteration": self._run_autoresearch_plan_iteration,
            "skill_creation.create_variants": self._run_skill_creation_create_variants,
            "autoresearch.generate_variants": self._run_autoresearch_generate,
            "benchmark.score_candidates": self._run_benchmark_candidates,
            "benchmark.score_finalist": self._run_benchmark_candidates,
            "review.evaluate_candidates": self._run_review,
            "review.finalize_candidate": self._run_review,
            "approval_convergence.converge_approval": self._run_approval_convergence,
            "autoresearch.decide_iteration": self._run_autoresearch_decide_iteration,
            "autoresearch.finalize_iteration": self._run_autoresearch_finalize,
        }

    def _session_id(self, request: WorkerBridgeInvocationRequest) -> str:
        return str(request.metadata.get("session_id") or request.request.session_id or "nextgen-bridge-session")

    def _workspace_adapter_ids(self, request: WorkerBridgeInvocationRequest) -> dict[str, str]:
        resolved: dict[str, str] = {}
        for container in (request.request.metadata, request.metadata, request.state.get("adapter_ids")):
            payloads = [container]
            if isinstance(container, Mapping):
                payloads.extend([container.get("workspace_adapters"), container.get("adapter_ids")])
            for payload in payloads:
                if not isinstance(payload, Mapping):
                    continue
                data_source = str(payload.get("data_source") or payload.get("data_source_adapter_id") or "").strip()
                execution = str(payload.get("execution") or payload.get("execution_adapter_id") or "").strip()
                if data_source and "data_source" not in resolved:
                    resolved["data_source"] = data_source
                if execution and "execution" not in resolved:
                    resolved["execution"] = execution
        return resolved

    def _request_with_resolved_adapters(
        self,
        request: WorkerBridgeInvocationRequest,
        *,
        require_data_source: bool,
        require_execution: bool,
    ) -> WorkflowRunRequest:
        workspace_adapters = self._workspace_adapter_ids(request)
        data_source_adapter_id = (
            str(request.request.data_source_adapter_id or "").strip()
            or workspace_adapters.get("data_source")
        )
        execution_adapter_id = (
            str(request.request.execution_adapter_id or "").strip()
            or workspace_adapters.get("execution")
        )
        if require_data_source and not data_source_adapter_id:
            raise ValueError("worker bridge requires an explicit or workspace-derived data_source_adapter_id")
        if require_execution and not execution_adapter_id:
            raise ValueError("worker bridge requires an explicit or workspace-derived execution_adapter_id")
        if (
            data_source_adapter_id == (request.request.data_source_adapter_id or "")
            and execution_adapter_id == (request.request.execution_adapter_id or "")
        ):
            return request.request
        return request.request.model_copy(
            update={
                "data_source_adapter_id": data_source_adapter_id or None,
                "execution_adapter_id": execution_adapter_id or None,
            }
        )

    def _adapter_state_patch(self, request: WorkflowRunRequest) -> dict[str, Any]:
        return {
            "adapter_ids": {
                "data_source": request.data_source_adapter_id,
                "execution": request.execution_adapter_id,
            }
        }

    def _distillation_handler_for(self, request: WorkerBridgeInvocationRequest) -> DistillationWorkerHandler:
        if self.distillation_handler is not None and self._distillation_service_override is not None:
            return self.distillation_handler
        normalized_request = self._request_with_resolved_adapters(
            request,
            require_data_source=True,
            require_execution=False,
        )
        adapter_id = normalized_request.data_source_adapter_id
        if (
            self.distillation_handler is not None
            and self._distillation_handler_adapter_id
            and self._distillation_handler_adapter_id == adapter_id
        ):
            return self.distillation_handler
        service = build_wallet_style_distillation_service(
            project_root=self.project_root,
            workspace_root=self.workspace_root,
            adapter_registry=self.adapter_registry,
            data_source_adapter_id=adapter_id,
            require_explicit_data_source_adapter=True,
            allow_builtin_adapter_registry_fallback=False,
        )
        self.distillation_handler = build_distillation_worker_handler(service)
        self._distillation_handler_adapter_id = adapter_id
        return self.distillation_handler

    def invoke(self, request: WorkerBridgeInvocationRequest) -> WorkerBridgeInvocationResponse:
        handler = self._action_handlers.get(request.action_id)
        if handler is None:
            raise ValueError(f"unsupported worker bridge action: {request.action_id}")
        return handler(request)

    def _session(self, request: WorkerBridgeInvocationRequest) -> ResearchSessionState:
        session_id = str(request.request.session_id or request.metadata.get("session_id") or "nextgen-bridge-session")
        objective = request.request.objective
        baseline_variant_id = str(request.state.get("baseline_variant", {}).get("variant_id") or "baseline")
        baseline_profile = dict(request.state.get("baseline_variant", {}).get("style_profile") or {})
        selected_profile = dict(request.state.get("selected_variant", {}).get("style_profile") or {})
        wallet = str(
            request.request.wallet
            or baseline_profile.get("wallet")
            or selected_profile.get("wallet")
            or ""
        )
        chain = str(
            request.request.chain
            or baseline_profile.get("chain")
            or selected_profile.get("chain")
            or "bsc"
        )
        return ResearchSessionState(
            session_id=session_id,
            workflow_id=request.workflow_id,
            workspace_id=request.request.workspace_id,
            workspace_dir=str(Path(request.workspace_dir or self.workspace_root).expanduser().resolve()),
            wallet=wallet,
            chain=chain,
            skill_name=request.request.skill_name,
            objective=objective,
            baseline_variant_id=baseline_variant_id,
            current_iteration=int(request.state.get("iteration_index") or 0),
            max_iterations=request.request.iteration_budget,
            status="running",
            active_parent_variant_id=str(
                request.state.get("active_parent_variant_id")
                or request.state.get("parent_variant_id")
                or baseline_variant_id
            ),
            metadata={"bridge": "python-worker-bridge"},
        )

    def _run_distillation(self, request: WorkerBridgeInvocationRequest) -> WorkerBridgeInvocationResponse:
        normalized_request = self._request_with_resolved_adapters(
            request,
            require_data_source=self._distillation_service_override is None,
            require_execution=False,
        )
        handler = self._distillation_handler_for(request)
        protocol = DistillationWorkerProtocol(
            protocol_id="distillation.wallet_style",
            plugin_version=self.plugin_registry.resolve_plugin("distillation").plugin_version,
            workflow_id=request.workflow_id,
            workflow_step_id=request.workflow_step_id,
            operation_order=["plan", "execute", "validate", "summarize"],
            capability_bindings={
                "plan": "distill_wallet_style",
                "execute": "emit_seed_skill",
                "validate": "emit_seed_skill",
                "summarize": "emit_seed_skill",
            },
            baseline_artifact_kinds=["style_profile", "strategy_spec", "execution_intent", "distillation_report", "seed_skill_package"],
            compat_result_key="raw_distillation_result",
        )
        response = handler.run_protocol(workflow_request=normalized_request, protocol=protocol)
        return WorkerBridgeInvocationResponse(
            bridge_id=request.bridge_id,
            bridge_version=request.bridge_version,
            action_id=request.action_id,
            workflow_id=request.workflow_id,
            workflow_step_id=request.workflow_step_id,
            operation=response.operation,
            status=response.status,
            outputs={
                "baseline_variant": response.baseline_variant.model_dump(mode="json") if response.baseline_variant else None,
                "baseline_artifacts": [item.model_dump(mode="json") for item in response.artifacts],
                "raw_distillation_result": response.raw_result,
            },
            state_patch={
                "baseline_variant": response.baseline_variant.model_dump(mode="json") if response.baseline_variant else None,
                "baseline_artifacts": [item.model_dump(mode="json") for item in response.artifacts],
                "raw_distillation_result": response.raw_result,
                "validation": dict(response.state.get("validation") or {}),
                **self._adapter_state_patch(normalized_request),
            },
            artifacts=list(response.artifacts),
            events=[
                WorkerBridgeEvent(
                    operation=event.operation,
                    status=event.status,
                    message=event.summary,
                    metadata=dict(event.metadata),
                )
                for event in response.events
            ],
            compat_payload=dict(response.compat_payload),
            raw_result=dict(response.raw_result),
            metadata=dict(response.metadata),
        )

    def _run_skill_creation_materialize_baseline(
        self,
        request: WorkerBridgeInvocationRequest,
    ) -> WorkerBridgeInvocationResponse:
        baseline_variant = _variant_from_state(request.state, "baseline_variant")
        normalized_request = self._request_with_resolved_adapters(
            request,
            require_data_source=False,
            require_execution=False,
        )
        materialized_variant = self.skill_creation_executor.dispatch_action(
            request.action_id,
            request=normalized_request,
            session_id=self._session_id(request),
            baseline_variant=baseline_variant,
            raw_distillation_result=dict(request.state.get("raw_distillation_result") or {}),
        )
        artifacts = [
            item
            for item in materialized_variant.artifacts
            if item.ref.kind in {"baseline_materialization", "skill_package", "validation_report", "qa_report", "kernel_replay_payload"}
        ]
        return WorkerBridgeInvocationResponse(
            bridge_id=request.bridge_id,
            bridge_version=request.bridge_version,
            action_id=request.action_id,
            workflow_id=request.workflow_id,
            workflow_step_id=request.workflow_step_id,
            operation="execute",
            status="materialized",
            outputs={
                "baseline_variant": materialized_variant.model_dump(mode="json"),
                "baseline_materialization_bundle": [item.model_dump(mode="json") for item in artifacts],
            },
            state_patch={
                "baseline_variant": materialized_variant.model_dump(mode="json"),
                "baseline_materialization_bundle": [item.model_dump(mode="json") for item in artifacts],
                **self._adapter_state_patch(normalized_request),
            },
            artifacts=artifacts,
            events=[
                WorkerBridgeEvent(
                    operation="execute",
                    status="materialized",
                    message="Materialized baseline skill package artifacts.",
                    metadata={"variant_id": materialized_variant.variant_id},
                )
            ],
        )

    def _run_autoresearch_plan_iteration(self, request: WorkerBridgeInvocationRequest) -> WorkerBridgeInvocationResponse:
        normalized_request = self._request_with_resolved_adapters(
            request,
            require_data_source=False,
            require_execution=False,
        )
        baseline_variant = _variant_from_state(request.state, "baseline_variant")
        parent_variant = WorkflowVariant.model_validate(
            request.state.get("parent_variant")
            or request.state.get("selected_variant")
            or request.state.get("baseline_variant")
        )
        iteration_index = max(1, int(request.state.get("iteration_index") or 1))
        plans = self.autoresearch_executor.dispatch_action(
            request.action_id,
            request=normalized_request,
            baseline_variant=baseline_variant,
            parent_variant=parent_variant,
            session_id=self._session_id(request),
            iteration_index=iteration_index,
        )
        return WorkerBridgeInvocationResponse(
            bridge_id=request.bridge_id,
            bridge_version=request.bridge_version,
            action_id=request.action_id,
            workflow_id=request.workflow_id,
            workflow_step_id=request.workflow_step_id,
            operation="execute",
            status="planned",
            outputs={"variant_plans": [item.model_dump(mode="json") for item in plans]},
            state_patch={
                "variant_plans": [item.model_dump(mode="json") for item in plans],
                "iteration_index": iteration_index,
                "parent_variant": parent_variant.model_dump(mode="json"),
                **self._adapter_state_patch(normalized_request),
            },
            events=[
                WorkerBridgeEvent(
                    operation="execute",
                    status="planned",
                    message=f"Planned {len(plans)} candidate variants.",
                    metadata={"iteration_index": iteration_index},
                )
            ],
        )

    def _run_skill_creation_create_variants(self, request: WorkerBridgeInvocationRequest) -> WorkerBridgeInvocationResponse:
        normalized_request = self._request_with_resolved_adapters(
            request,
            require_data_source=False,
            require_execution=False,
        )
        baseline_variant = _variant_from_state(request.state, "baseline_variant")
        parent_variant = WorkflowVariant.model_validate(
            request.state.get("parent_variant")
            or request.state.get("selected_variant")
            or request.state.get("baseline_variant")
        )
        plans = [
            ResearchVariantPlan.model_validate(item)
            for item in list(request.state.get("variant_plans") or [])
        ]
        iteration_index = int(request.state.get("iteration_index") or 1)
        variants = self.skill_creation_executor.dispatch_action(
            request.action_id,
            request=normalized_request,
            session_id=self._session_id(request),
            baseline_variant=baseline_variant,
            parent_variant=parent_variant,
            plans=plans,
            iteration_index=iteration_index,
        )
        artifacts: list[WorkflowArtifact] = []
        for item in variants:
            artifacts.extend(item.artifacts)
        return WorkerBridgeInvocationResponse(
            bridge_id=request.bridge_id,
            bridge_version=request.bridge_version,
            action_id=request.action_id,
            workflow_id=request.workflow_id,
            workflow_step_id=request.workflow_step_id,
            operation="execute",
            status="materialized",
            outputs={
                "candidate_variants": [item.model_dump(mode="json") for item in variants],
                "variant_materialization_bundle": [item.model_dump(mode="json") for item in artifacts],
            },
            state_patch={
                "candidate_variants": [item.model_dump(mode="json") for item in variants],
                "variant_materialization_bundle": [item.model_dump(mode="json") for item in artifacts],
                **self._adapter_state_patch(normalized_request),
            },
            artifacts=artifacts,
            events=[
                WorkerBridgeEvent(
                    operation="execute",
                    status="materialized",
                    message=f"Materialized {len(variants)} candidate variants.",
                    metadata={"iteration_index": iteration_index},
                )
            ],
        )

    def _run_autoresearch_generate(self, request: WorkerBridgeInvocationRequest) -> WorkerBridgeInvocationResponse:
        plan_response = self._run_autoresearch_plan_iteration(request)
        merged_state = {**dict(request.state), **dict(plan_response.state_patch)}
        materialize_request = request.model_copy(update={"action_id": "skill_creation.create_variants", "state": merged_state})
        materialize_response = self._run_skill_creation_create_variants(materialize_request)
        return materialize_response.model_copy(
            update={
                "action_id": request.action_id,
                "events": [*list(plan_response.events), *list(materialize_response.events)],
            }
        )

    def _run_benchmark_baseline(self, request: WorkerBridgeInvocationRequest) -> WorkerBridgeInvocationResponse:
        normalized_request = self._request_with_resolved_adapters(
            request,
            require_data_source=True,
            require_execution=True,
        )
        session = self._session(request)
        baseline_variant = _variant_from_state(request.state, "baseline_variant")
        scorecard = self.benchmark_executor.dispatch_action(
            request.action_id,
            request=normalized_request,
            session=session,
            iteration_index=0,
            baseline_variant=baseline_variant,
            variants=[baseline_variant],
        )[0]
        return WorkerBridgeInvocationResponse(
            bridge_id=request.bridge_id,
            bridge_version=request.bridge_version,
            action_id=request.action_id,
            workflow_id=request.workflow_id,
            workflow_step_id=request.workflow_step_id,
            operation="execute",
            status="benchmarked",
            outputs={"baseline_scorecard": scorecard.model_dump(mode="json")},
            state_patch={
                "baseline_scorecard": scorecard.model_dump(mode="json"),
                **self._adapter_state_patch(normalized_request),
            },
            artifacts=list(scorecard.artifacts),
            events=[
                WorkerBridgeEvent(
                    operation="execute",
                    status="benchmarked",
                    message="Benchmarked baseline variant.",
                    metadata={"variant_id": baseline_variant.variant_id},
                )
            ],
        )

    def _run_benchmark_candidates(self, request: WorkerBridgeInvocationRequest) -> WorkerBridgeInvocationResponse:
        normalized_request = self._request_with_resolved_adapters(
            request,
            require_data_source=True,
            require_execution=True,
        )
        session = self._session(request)
        baseline_variant = _variant_from_state(request.state, "baseline_variant")
        variants = _candidate_variants_for_request(request.state)
        if not variants:
            raise ValueError("benchmark worker requires candidate_variants or selected_variant in state")
        scorecards = self.benchmark_executor.dispatch_action(
            request.action_id,
            request=normalized_request,
            session=session,
            iteration_index=int(request.state.get("iteration_index") or 1),
            baseline_variant=baseline_variant,
            variants=variants,
            persist_record=request.action_id != "benchmark.score_finalist",
            artifact_suffix="approval-finalist" if request.action_id == "benchmark.score_finalist" else "",
        )
        artifacts: list[WorkflowArtifact] = []
        for item in scorecards:
            artifacts.extend(item.artifacts)
        scorecard_payloads = [item.model_dump(mode="json") for item in scorecards]
        finalist_payload = scorecard_payloads[0] if len(scorecard_payloads) == 1 else None
        return WorkerBridgeInvocationResponse(
            bridge_id=request.bridge_id,
            bridge_version=request.bridge_version,
            action_id=request.action_id,
            workflow_id=request.workflow_id,
            workflow_step_id=request.workflow_step_id,
            operation="execute",
            status="benchmarked",
            outputs={
                "candidate_scorecards": scorecard_payloads,
                **({"benchmark_scorecard": finalist_payload} if finalist_payload is not None else {}),
            },
            state_patch={
                "candidate_scorecards": scorecard_payloads,
                **({"benchmark_scorecard": finalist_payload} if finalist_payload is not None else {}),
                **self._adapter_state_patch(normalized_request),
            },
            artifacts=artifacts,
            events=[
                WorkerBridgeEvent(
                    operation="execute",
                    status="benchmarked",
                    message=f"Benchmarked {len(scorecards)} candidate variants.",
                    metadata={"iteration_index": int(request.state.get('iteration_index') or 1)},
                )
            ],
        )

    def _run_review(self, request: WorkerBridgeInvocationRequest) -> WorkerBridgeInvocationResponse:
        normalized_request = self._request_with_resolved_adapters(
            request,
            require_data_source=False,
            require_execution=False,
        )
        session = self._session(request)
        baseline_scorecard = _baseline_scorecard_for_request(request.state)
        scorecards = _candidate_scorecards_for_request(request.state)
        if not scorecards:
            raise ValueError("review worker requires candidate_scorecards or benchmark_scorecard in state")
        decisions = self.review_executor.dispatch_action(
            request.action_id,
            request=normalized_request,
            session=session,
            iteration_index=int(request.state.get("iteration_index") or 1),
            baseline_scorecard=baseline_scorecard,
            scorecards=scorecards,
            persist_record=request.action_id != "review.finalize_candidate",
            artifact_suffix="approval-finalist" if request.action_id == "review.finalize_candidate" else "",
        )
        artifacts: list[WorkflowArtifact] = []
        for item in decisions:
            artifacts.extend(item.artifacts)
        decision_payloads = [item.model_dump(mode="json") for item in decisions]
        finalist_payload = decision_payloads[0] if len(decision_payloads) == 1 else None
        return WorkerBridgeInvocationResponse(
            bridge_id=request.bridge_id,
            bridge_version=request.bridge_version,
            action_id=request.action_id,
            workflow_id=request.workflow_id,
            workflow_step_id=request.workflow_step_id,
            operation="execute",
            status="reviewed",
            outputs={
                "review_decisions": decision_payloads,
                **({"review_decision": finalist_payload} if finalist_payload is not None else {}),
            },
            state_patch={
                "review_decisions": decision_payloads,
                **({"review_decision": finalist_payload} if finalist_payload is not None else {}),
                **self._adapter_state_patch(normalized_request),
            },
            artifacts=artifacts,
            events=[
                WorkerBridgeEvent(
                    operation="execute",
                    status="reviewed",
                    message=f"Reviewed {len(decisions)} candidate variants.",
                    metadata={"iteration_index": int(request.state.get('iteration_index') or 1)},
                )
            ],
        )

    def _run_approval_convergence(self, request: WorkerBridgeInvocationRequest) -> WorkerBridgeInvocationResponse:
        normalized_request = self._request_with_resolved_adapters(
            request,
            require_data_source=False,
            require_execution=False,
        )
        recommendation = RecommendationBundle.model_validate(request.state["recommendation_bundle"])
        baseline_variant = _variant_from_state(request.state, "baseline_variant")
        session = self._session(request)
        scorecards = _candidate_scorecards_for_request(request.state)
        review_decisions = [ReviewDecision.model_validate(item) for item in list(request.state.get("review_decisions") or [])]
        review_decision_payload = request.state.get("review_decision")
        if isinstance(review_decision_payload, Mapping) and review_decision_payload:
            review_decisions.insert(0, ReviewDecision.model_validate(review_decision_payload))
        approval_result = self.approval_convergence_executor.dispatch_action(
            request.action_id,
            request=normalized_request,
            session=session,
            baseline_variant=baseline_variant,
            recommendation_bundle=recommendation,
            scorecards=scorecards,
            review_decisions=review_decisions,
        )
        artifacts = list(approval_result.artifacts)
        artifacts_by_kind = {artifact.ref.kind: artifact for artifact in artifacts}
        default_approval_recommendation = {
            "workflow_id": request.workflow_id,
            "session_id": approval_result.session_id,
            "recommended_variant_id": approval_result.recommended_variant_id,
            "status": approval_result.status,
            "summary": approval_result.summary,
            "approval_required": approval_result.approval.approval_required,
            "approval_granted": approval_result.approval.approval_granted,
            "activation_requested": approval_result.approval.activation_requested,
            "activation_allowed": approval_result.approval.activation_allowed,
        }
        default_handoff_payload = {
            "workflow_id": request.workflow_id,
            "session_id": approval_result.session_id,
            "action_id": request.action_id,
            "variant_id": approval_result.recommended_variant_id,
            "status": approval_result.status,
            "approval_granted": approval_result.approval.approval_granted,
            "activation_requested": approval_result.approval.activation_requested,
        }
        approval_recommendation = dict(
            artifacts_by_kind.get("approval_recommendation").payload
            if "approval_recommendation" in artifacts_by_kind
            else default_approval_recommendation
        )
        handoff_payload = dict(
            artifacts_by_kind.get("kernel_handoff_payload").payload
            if "kernel_handoff_payload" in artifacts_by_kind
            else default_handoff_payload
        )
        result_payload = approval_result.model_dump(mode="json")
        return WorkerBridgeInvocationResponse(
            bridge_id=request.bridge_id,
            bridge_version=request.bridge_version,
            action_id=request.action_id,
            workflow_id=request.workflow_id,
            workflow_step_id=request.workflow_step_id,
            operation="summarize",
            status=approval_result.status,
            outputs={
                "approval_convergence_result": result_payload,
                "approval_activation_bundle": result_payload,
                "approval_recommendation": approval_recommendation,
                "kernel_handoff_payload": handoff_payload,
                "approval": approval_result.approval.model_dump(mode="json"),
            },
            state_patch={
                "approval_convergence_result": result_payload,
                "approval_activation_bundle": result_payload,
                "approval_recommendation": approval_recommendation,
                "kernel_handoff_payload": handoff_payload,
                "approval": approval_result.approval.model_dump(mode="json"),
                **self._adapter_state_patch(normalized_request),
            },
            artifacts=artifacts,
            events=[
                WorkerBridgeEvent(
                    operation="summarize",
                    status=approval_result.status,
                    message="Converged approval state for the selected finalist.",
                    metadata={
                        "variant_id": approval_result.recommended_variant_id,
                        "approval_status": approval_result.status,
                    },
                )
            ],
            metadata={
                **dict(approval_result.metadata),
                "plugin_source": dict(approval_result.metadata).get("source"),
                "source": "python-worker-bridge",
            },
        )

    def _run_autoresearch_decide_iteration(self, request: WorkerBridgeInvocationRequest) -> WorkerBridgeInvocationResponse:
        normalized_request = self._request_with_resolved_adapters(
            request,
            require_data_source=False,
            require_execution=False,
        )
        baseline_variant = _variant_from_state(request.state, "baseline_variant")
        variants = _variants_from_state(request.state, "candidate_variants")
        scorecards = _scorecards_from_state(request.state, "candidate_scorecards")
        review_decisions = [ReviewDecision.model_validate(item) for item in list(request.state.get("review_decisions") or [])]
        iteration_index = int(request.state.get("iteration_index") or 1)
        recommendation = self.autoresearch_executor.dispatch_action(
            request.action_id,
            workflow_id=request.workflow_id,
            session_id=self._session_id(request),
            request=normalized_request,
            iteration_index=iteration_index,
            baseline_variant=baseline_variant,
            variants=variants,
            scorecards=scorecards,
            review_decisions=review_decisions,
            iterations=[],
            baseline_scorecard=BenchmarkScorecard.model_validate(request.state["baseline_scorecard"]),
        )
        selected_variant = recommendation.selected_variant
        stop_decision = recommendation.stop_decision or ResearchStopDecision(
            decision="continue",
            reason="continue iteration",
        )
        remaining_iterations = max(0, request.request.iteration_budget - iteration_index)
        should_continue = stop_decision.decision == "continue"
        return WorkerBridgeInvocationResponse(
            bridge_id=request.bridge_id,
            bridge_version=request.bridge_version,
            action_id=request.action_id,
            workflow_id=request.workflow_id,
            workflow_step_id=request.workflow_step_id,
            operation="summarize",
            status="summarized",
            outputs={"recommendation_bundle": recommendation.model_dump(mode="json")},
            state_patch={
                "recommendation_bundle": recommendation.model_dump(mode="json"),
                "selected_variant": selected_variant.model_dump(mode="json") if selected_variant is not None else None,
                "remaining_iterations": remaining_iterations,
            },
            artifacts=list(recommendation.artifacts),
            events=[
                WorkerBridgeEvent(
                    operation="summarize",
                    status="summarized",
                    message="Finalized autoresearch iteration.",
                    metadata={"should_continue": should_continue, "stop_reason": stop_decision.reason},
                )
            ],
            control={
                "should_continue": should_continue,
                "remaining_iterations": remaining_iterations,
                "stop_reason": stop_decision.reason,
            },
            metadata={"stop_decision": stop_decision.model_dump(mode="json")},
        )

    def _run_autoresearch_finalize(self, request: WorkerBridgeInvocationRequest) -> WorkerBridgeInvocationResponse:
        response = self._run_autoresearch_decide_iteration(request)
        return response.model_copy(update={"action_id": request.action_id})


def _load_request(path: Path) -> WorkerBridgeInvocationRequest:
    return WorkerBridgeInvocationRequest.model_validate(json.loads(path.read_text(encoding="utf-8")))


def _write_response(path: Path, payload: WorkerBridgeInvocationResponse) -> None:
    path.write_text(json.dumps(payload.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")


def build_worker_runtime(
    *,
    project_root: Path | None = None,
    workspace_root: Path | None = None,
    plugin_registry: WorkflowPluginRegistry | None = None,
    adapter_registry: AdapterRegistry | None = None,
    distillation_service: Any | None = None,
) -> WorkflowWorkerRuntime:
    return WorkflowWorkerRuntime(
        project_root=project_root,
        workspace_root=workspace_root,
        plugin_registry=plugin_registry,
        adapter_registry=adapter_registry,
        distillation_service=distillation_service,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ot-nextgen-worker-bridge")
    parser.add_argument("--request-file", required=True)
    parser.add_argument("--response-file", required=True)
    parser.add_argument("--project-root", default=".")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    project_root = Path(args.project_root).expanduser().resolve()
    runtime = build_worker_runtime(project_root=project_root)
    request = _load_request(Path(args.request_file).expanduser().resolve())
    response = runtime.invoke(request)
    _write_response(Path(args.response_file).expanduser().resolve(), response)
    return 0
