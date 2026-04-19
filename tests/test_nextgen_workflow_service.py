from __future__ import annotations

import json
from pathlib import Path

import pytest

from ot_skill_enterprise.nextgen.adapters.models import (
    AdapterCapability,
    AdapterManifest,
    DataSourceAdapter,
    ExecutionAdapter,
)
from ot_skill_enterprise.nextgen.adapters.registry import AdapterRegistry
from ot_skill_enterprise.nextgen.kernel_bridge import build_nextgen_kernel_bridge
from ot_skill_enterprise.nextgen.plugins.registry import build_default_plugin_registry
from ot_skill_enterprise.nextgen.workflows import WorkflowRunRequest, build_nextgen_workflow_service


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_ADAPTERS = {
    "data_source_adapter_id": "fake-data",
    "execution_adapter_id": "fake-execution",
}


def manifest_capability(capability_id: str) -> AdapterCapability:
    return AdapterCapability(
        capability_id=capability_id,
        display_name=capability_id.replace("_", " ").title(),
        description=f"Capability {capability_id} for tests.",
    )


class FakeDataSourceAdapter(DataSourceAdapter):
    manifest = AdapterManifest(
        adapter_id="fake-data",
        adapter_type="data_source",
        adapter_version="1.0.0",
        title="Fake Data Adapter",
        summary="Deterministic test adapter.",
        capabilities=(
            manifest_capability("market_context"),
            manifest_capability("wallet_profile"),
        ),
        is_builtin=True,
    )

    def describe(self) -> dict[str, object]:
        return self.manifest.as_dict()

    def supports_capability(self, capability_id: str) -> bool:
        return self.manifest.supports(capability_id)

    def invoke(self, capability_id: str, payload: dict[str, object], *, workspace_dir=None, request_id=None) -> dict[str, object]:
        return {"capability_id": capability_id, "payload": dict(payload), "request_id": request_id}


class FakeExecutionAdapter(ExecutionAdapter):
    manifest = AdapterManifest(
        adapter_id="fake-execution",
        adapter_type="execution",
        adapter_version="1.0.0",
        title="Fake Execution Adapter",
        summary="Deterministic test execution adapter.",
        capabilities=(
            manifest_capability("execution_prepare_only"),
            manifest_capability("dry_run"),
        ),
        is_builtin=True,
    )

    def describe(self) -> dict[str, object]:
        return self.manifest.as_dict()

    def supports_capability(self, capability_id: str) -> bool:
        return self.manifest.supports(capability_id)

    def invoke(self, capability_id: str, payload: dict[str, object], *, workspace_dir=None, request_id=None) -> dict[str, object]:
        return {"capability_id": capability_id, "payload": dict(payload), "request_id": request_id}


class FakeDistillationService:
    def distill_wallet_style(self, *, wallet: str, chain: str | None = None, skill_name: str | None = None, **_: object) -> dict[str, object]:
        return {
            "job_id": "job-001",
            "wallet": wallet,
            "chain": chain or "bsc",
            "profile": {
                "wallet": wallet,
                "chain": chain or "bsc",
                "style_label": "momentum",
                "summary": "Momentum trader with selective follow-through.",
                "preferred_tokens": ["SOL", "WETH"],
            },
            "strategy": {
                "setup_label": "momentum-breakout",
                "summary": "Enter on momentum continuation with strict invalidation.",
                "entry_conditions": [
                    {"condition": "breakout volume", "data_source": "wallet_profile", "weight": 0.8},
                    {"condition": "trend continuation", "data_source": "market_context", "weight": 0.7},
                ],
                "risk_controls": ["stop below breakout"],
                "position_sizing": {"mode": "fixed_fraction"},
            },
            "execution_intent": {
                "adapter": "fake-execution",
                "mode": "review",
                "preferred_workflow": "prepare_only",
                "preflight_checks": ["allowlist"],
                "max_position_pct": 0.18,
                "metadata": {"tempo": "fast"},
            },
            "candidate": {"candidate_id": "candidate-001", "runtime_session_id": "runtime-001", "source_run_id": "run-001"},
            "package": {"package_id": "package-001"},
            "promotion": {"promotion_id": "promotion-001", "skill_slug": skill_name or "seed-skill"},
            "qa": {
                "status": "passed",
                "checks": [{"check": "seed", "passed": True}],
                "strategy_qa": {"status": "passed", "checks": [{"check": "seed", "passed": True}]},
                "execution_qa": {"status": "passed", "checks": [{"check": "seed", "passed": True}]},
            },
            "backtest": {"confidence_score": 0.74, "confidence_label": "high", "metadata": {"market_context_count": 2}},
            "review": {"status": "approved"},
            "summary": {"summary": "Baseline distillation completed"},
            "artifacts": {"style_profile": "/tmp/style_profile.json"},
        }


class CapturingKernelBridge:
    def __init__(self, runtime_mode: str = "ts-kernel") -> None:
        self.runtime_mode = runtime_mode
        self.calls: list[dict[str, object]] = []

    def dispatch(self, *, workflow_id: str, request_payload: dict[str, object], fallback_runner=None, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(
            {
                "workflow_id": workflow_id,
                "request_payload": dict(request_payload),
                "fallback_runner": fallback_runner,
                **dict(kwargs),
            }
        )
        return {
            "status": "ran",
            "workflow_id": workflow_id,
            "runtime_mode": self.runtime_mode,
            "final_result": {
                "workflow_id": workflow_id,
                "session_id": request_payload.get("session_id"),
                "baseline_variant": {
                    "variant_id": "baseline",
                    "title": "baseline",
                    "source": "distillation",
                    "strategy_spec": {},
                    "execution_intent": {},
                    "style_profile": {},
                },
                "artifacts": [],
                "metadata": {},
            },
        }


def build_test_registry() -> AdapterRegistry:
    registry = AdapterRegistry()
    registry.register(FakeDataSourceAdapter(), default=True)
    registry.register(FakeExecutionAdapter(), default=True)
    return registry


def test_build_nextgen_workflow_service_does_not_eagerly_construct_distillation_service_without_explicit_adapter(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def _explode(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("distillation service should not be built eagerly without an explicit adapter selection")

    monkeypatch.setattr("ot_skill_enterprise.nextgen.workflows.service.build_wallet_style_distillation_service", _explode)

    service = build_nextgen_workflow_service(
        project_root=REPO_ROOT,
        workspace_root=tmp_path,
        plugin_registry=build_default_plugin_registry(),
        adapter_registry=build_test_registry(),
    )

    assert service.distillation_worker_handler is None


def test_run_distillation_seed_returns_normalized_baseline_variant(tmp_path: Path) -> None:
    service = build_nextgen_workflow_service(
        project_root=REPO_ROOT,
        workspace_root=tmp_path,
        plugin_registry=build_default_plugin_registry(),
        adapter_registry=build_test_registry(),
        distillation_service=FakeDistillationService(),
        runtime_mode="python-compat",
    )

    result = service.run_distillation_seed(
        WorkflowRunRequest(
            workflow_id="distillation_seed",
            session_id="seed-session-1",
            workspace_id="desk-alpha",
            wallet="0xabc",
            chain="bsc",
            skill_name="desk-alpha",
            workspace_dir=str(tmp_path),
            **WORKFLOW_ADAPTERS,
        )
    )

    assert result.workflow_id == "distillation_seed"
    assert result.session_id == "seed-session-1"
    assert result.metadata["kernel_dispatch"]["workflow_id"] == "distillation_seed"
    assert result.baseline_variant.variant_id == "baseline"
    assert result.baseline_variant.strategy_spec["setup_label"] == "momentum-breakout"
    assert result.baseline_variant.execution_intent["adapter"] == "fake-execution"
    artifact_kinds = {artifact.ref.kind for artifact in result.baseline_variant.artifacts}
    assert {
        "baseline_materialization",
        "skill_package",
        "validation_report",
        "qa_report",
        "kernel_replay_payload",
    } <= artifact_kinds
    assert result.baseline_variant.metadata["materialization"]["source"] == "skill-creation"
    assert result.metadata["selected_adapters"] == {
        "data_source": "fake-data",
        "execution": "fake-execution",
    }
    assert result.metadata["raw_distillation_result"]["promotion"]["promotion_id"] == "promotion-001"


def test_ts_kernel_default_does_not_supply_python_fallback_runner(tmp_path: Path) -> None:
    kernel_bridge = CapturingKernelBridge(runtime_mode="ts-kernel")
    service = build_nextgen_workflow_service(
        project_root=REPO_ROOT,
        workspace_root=tmp_path,
        plugin_registry=build_default_plugin_registry(),
        adapter_registry=build_test_registry(),
        kernel_bridge=kernel_bridge,
        distillation_service=FakeDistillationService(),
    )

    service.run_distillation_seed(
        WorkflowRunRequest(
            workflow_id="distillation_seed",
            session_id="seed-session-kernel",
            workspace_id="desk-alpha",
            wallet="0xabc",
            chain="bsc",
            skill_name="desk-alpha",
            workspace_dir=str(tmp_path),
            **WORKFLOW_ADAPTERS,
        )
    )

    assert kernel_bridge.calls[0]["fallback_runner"] is None


def test_python_compat_mode_supplies_fallback_runner(tmp_path: Path) -> None:
    kernel_bridge = CapturingKernelBridge(runtime_mode="python-compat")
    service = build_nextgen_workflow_service(
        project_root=REPO_ROOT,
        workspace_root=tmp_path,
        plugin_registry=build_default_plugin_registry(),
        adapter_registry=build_test_registry(),
        kernel_bridge=kernel_bridge,
        distillation_service=FakeDistillationService(),
    )

    service.run_distillation_seed(
        WorkflowRunRequest(
            workflow_id="distillation_seed",
            session_id="seed-session-compat",
            workspace_id="desk-alpha",
            wallet="0xabc",
            chain="bsc",
            skill_name="desk-alpha",
            workspace_dir=str(tmp_path),
            **WORKFLOW_ADAPTERS,
        )
    )

    assert callable(kernel_bridge.calls[0]["fallback_runner"])


def test_build_nextgen_workflow_service_defaults_to_ts_kernel_even_with_injected_services(tmp_path: Path) -> None:
    service = build_nextgen_workflow_service(
        project_root=REPO_ROOT,
        workspace_root=tmp_path,
        plugin_registry=build_default_plugin_registry(),
        adapter_registry=build_test_registry(),
        distillation_service=FakeDistillationService(),
    )

    assert service.kernel_bridge.runtime_mode == "ts-kernel"


def test_build_nextgen_workflow_service_honors_explicit_python_compat_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OT_WORKFLOW_RUNTIME", "python-compat")

    service = build_nextgen_workflow_service(
        project_root=REPO_ROOT,
        workspace_root=tmp_path,
        plugin_registry=build_default_plugin_registry(),
        adapter_registry=build_test_registry(),
        distillation_service=FakeDistillationService(),
    )

    assert service.kernel_bridge.runtime_mode == "python-compat"


def test_kernel_bridge_does_not_silently_fallback_when_ts_kernel_run_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = build_nextgen_kernel_bridge(project_root=REPO_ROOT, workspace_root=tmp_path)
    monkeypatch.setattr(type(bridge), "launch_plan", lambda self: {"status": "ready", "pi_mode": "workflow"})

    class _FailingRuntime:
        def run(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("kernel failed")

    bridge.runtime_service = _FailingRuntime()  # type: ignore[assignment]
    monkeypatch.setenv("OT_WORKFLOW_ENABLE_PYTHON_FALLBACK", "1")

    with pytest.raises(RuntimeError, match="kernel failed"):
        bridge.dispatch(
            workflow_id="distillation_seed",
            request_payload={"workflow_id": "distillation_seed", "wallet": "0xabc", "workspace_id": "desk-alpha"},
            fallback_runner=lambda payload: {"status": "should-not-run"},
        )


def test_run_autonomous_research_persists_session_and_governance_outputs(tmp_path: Path) -> None:
    service = build_nextgen_workflow_service(
        project_root=REPO_ROOT,
        workspace_root=tmp_path,
        plugin_registry=build_default_plugin_registry(),
        adapter_registry=build_test_registry(),
        distillation_service=FakeDistillationService(),
        runtime_mode="python-compat",
    )

    recommendation = service.run_autonomous_research(
        WorkflowRunRequest(
            workflow_id="autonomous_research",
            session_id="research-session-1",
            workspace_id="desk-alpha",
            wallet="0xabc",
            chain="bsc",
            skill_name="desk-alpha",
            workspace_dir=str(tmp_path),
            objective="improve quality while keeping the momentum style intact",
            max_variants=2,
            iteration_budget=2,
            **WORKFLOW_ADAPTERS,
        )
    )

    session_root = Path(recommendation.metadata["session_root"])
    assert recommendation.workflow_id == "autonomous_research"
    assert recommendation.session_id == "research-session-1"
    assert recommendation.workspace_id == "desk-alpha"
    assert recommendation.iteration_count >= 1
    assert recommendation.stop_decision is not None
    assert recommendation.metadata["selected_adapters"] == {
        "data_source": "fake-data",
        "execution": "fake-execution",
    }
    assert session_root.joinpath("session.json").is_file()
    assert session_root.joinpath("leaderboard.json").is_file()
    assert session_root.joinpath("recommendation.json").is_file()
    assert session_root.joinpath("variants", "baseline.json").is_file()
    assert any(session_root.joinpath("benchmarks").glob("*.json"))
    assert any(session_root.joinpath("reviews").glob("*.json"))
    assert any(session_root.joinpath("iterations").glob("*.json"))
    assert any(session_root.joinpath("artifacts").glob("plan-*.json"))
    assert any(session_root.joinpath("artifacts").glob("*materialization.json"))
    assert any(session_root.joinpath("artifacts").glob("*benchmark-scorecard.json"))
    assert any(session_root.joinpath("artifacts").glob("*retry-suggestion.json"))
    assert any(session_root.joinpath("artifacts").glob("handoff-*.json"))

    session_payload = json.loads(session_root.joinpath("session.json").read_text(encoding="utf-8"))
    assert session_payload["workspace_id"] == "desk-alpha"

    variant_files = sorted(session_root.joinpath("variants").glob("variant-*.json"))
    assert variant_files
    first_variant = json.loads(variant_files[0].read_text(encoding="utf-8"))
    assert first_variant["source"] == "skill-creation"
    assert first_variant["metadata"]["lineage"]["session_id"] == "research-session-1"
    assert first_variant["metadata"]["lineage"]["baseline_variant_id"] == "baseline"
    assert first_variant["metadata"]["plan_id"]

    review_files = sorted(session_root.joinpath("reviews").glob("*.json"))
    review_payload = json.loads(review_files[0].read_text(encoding="utf-8"))
    assert review_payload["governance"]["governance_status"] in {"recommended", "keep", "review_required", "discard"}
    review_artifact_kinds = {artifact["ref"]["kind"] for artifact in review_payload["artifacts"]}
    assert {"review_governance", "review_notes", "retry_suggestion", "kernel_handoff_payload"} <= review_artifact_kinds

    iteration_files = sorted(session_root.joinpath("iterations").glob("*.json"))
    iteration_payload = json.loads(iteration_files[0].read_text(encoding="utf-8"))
    assert iteration_payload["plan_ids"]
    assert iteration_payload["generated_variant_ids"]
    assert iteration_payload["benchmarked_variant_ids"]
    assert iteration_payload["reviewed_variant_ids"]

    recommendation_artifact_kinds = {artifact.ref.kind for artifact in recommendation.artifacts}
    assert {"leaderboard", "recommendation_bundle", "kernel_handoff_payload"} <= recommendation_artifact_kinds


def test_run_autonomous_research_replays_completed_session(tmp_path: Path) -> None:
    service = build_nextgen_workflow_service(
        project_root=REPO_ROOT,
        workspace_root=tmp_path,
        plugin_registry=build_default_plugin_registry(),
        adapter_registry=build_test_registry(),
        distillation_service=FakeDistillationService(),
        runtime_mode="python-compat",
    )

    request = WorkflowRunRequest(
        workflow_id="autonomous_research",
        session_id="research-session-replay",
        workspace_id="desk-alpha",
        wallet="0xabc",
        chain="bsc",
        skill_name="desk-alpha",
        workspace_dir=str(tmp_path),
        objective="improve quality while keeping the momentum style intact",
        **WORKFLOW_ADAPTERS,
    )
    first = service.run_autonomous_research(request)
    replay = service.run_autonomous_research(request)

    assert first.session_id == "research-session-replay"
    assert replay.session_id == "research-session-replay"
    assert replay.metadata["replayed"] is True
    assert replay.metadata["selected_adapters"] == {
        "data_source": "fake-data",
        "execution": "fake-execution",
    }
    assert replay.recommended_variant_id == first.recommended_variant_id
    assert replay.leaderboard == first.leaderboard


def test_run_distillation_seed_requires_explicit_or_workspace_derived_data_source_adapter(tmp_path: Path) -> None:
    service = build_nextgen_workflow_service(
        project_root=REPO_ROOT,
        workspace_root=tmp_path,
        plugin_registry=build_default_plugin_registry(),
        adapter_registry=build_test_registry(),
        distillation_service=FakeDistillationService(),
        runtime_mode="python-compat",
    )

    with pytest.raises(ValueError, match="explicit or workspace-derived data_source_adapter_id"):
        service.run_distillation_seed(
            WorkflowRunRequest(
                workflow_id="distillation_seed",
                session_id="seed-session-missing-adapter",
                workspace_id="desk-alpha",
                wallet="0xabc",
                chain="bsc",
                skill_name="desk-alpha",
                workspace_dir=str(tmp_path),
            )
        )


def test_run_autonomous_research_accepts_workspace_derived_adapter_selection(tmp_path: Path) -> None:
    service = build_nextgen_workflow_service(
        project_root=REPO_ROOT,
        workspace_root=tmp_path,
        plugin_registry=build_default_plugin_registry(),
        adapter_registry=build_test_registry(),
        distillation_service=FakeDistillationService(),
        runtime_mode="python-compat",
    )

    recommendation = service.run_autonomous_research(
        WorkflowRunRequest(
            workflow_id="autonomous_research",
            session_id="research-session-workspace-adapters",
            workspace_id="desk-alpha",
            wallet="0xabc",
            chain="bsc",
            skill_name="desk-alpha",
            workspace_dir=str(tmp_path),
            objective="improve quality while keeping the momentum style intact",
            metadata={
                "workspace_adapters": {
                    "data_source": "fake-data",
                    "execution": "fake-execution",
                }
            },
        )
    )

    assert recommendation.metadata["selected_adapters"] == {
        "data_source": "fake-data",
        "execution": "fake-execution",
    }


def test_run_approval_convergence_persists_replayable_approval_and_activation_artifacts(tmp_path: Path) -> None:
    service = build_nextgen_workflow_service(
        project_root=REPO_ROOT,
        workspace_root=tmp_path,
        plugin_registry=build_default_plugin_registry(),
        adapter_registry=build_test_registry(),
        distillation_service=FakeDistillationService(),
        runtime_mode="python-compat",
    )

    recommendation = service.run_autonomous_research(
        WorkflowRunRequest(
            workflow_id="autonomous_research",
            session_id="research-session-approval",
            workspace_id="desk-alpha",
            wallet="0xabc",
            chain="bsc",
            skill_name="desk-alpha",
            workspace_dir=str(tmp_path),
            objective="improve quality while keeping the momentum style intact",
            **WORKFLOW_ADAPTERS,
        )
    )
    result = service.run_approval_convergence(
        WorkflowRunRequest(
            workflow_id="approval_convergence",
            session_id="research-session-approval",
            workspace_id="desk-alpha",
            workspace_dir=str(tmp_path),
            metadata={"approval_granted": True, "activation_requested": True},
            **WORKFLOW_ADAPTERS,
        )
    )

    session_root = Path(result.metadata["session_root"])
    assert recommendation.recommended_variant_id or recommendation.selected_variant is not None
    assert result.workflow_id == "approval_convergence"
    assert result.session_id == "research-session-approval"
    assert result.workspace_id == "desk-alpha"
    assert result.status == "activated"
    assert result.approval.status == "activated"
    assert result.approval.approval_granted is True
    assert result.approval.activation_requested is True
    assert result.approval.activation_allowed is True
    assert result.recommendation_bundle.workflow_id == "autonomous_research"
    assert result.review_decision is not None
    assert result.benchmark_scorecard is not None
    assert result.metadata["source"] == "python-worker-bridge"
    assert session_root.joinpath("approval-convergence.json").is_file()
    approval_payload = json.loads(session_root.joinpath("approval-convergence.json").read_text(encoding="utf-8"))
    assert approval_payload["status"] == "activated"
    assert approval_payload["approval"]["status"] == "activated"

    artifact_kinds = {artifact.ref.kind for artifact in result.artifacts}
    assert {
        "benchmark_scorecard",
        "review_governance",
        "approval_decision",
        "activation_record",
        "approval_convergence_bundle",
        "kernel_handoff_payload",
    } <= artifact_kinds
    artifacts_dir = session_root / "artifacts"
    assert any(artifacts_dir.glob("*approval-finalist-benchmark-scorecard.json"))
    assert any(artifacts_dir.glob("*approval-finalist-review-governance.json"))
    assert any(artifacts_dir.glob("*approval-decision.json"))
    assert any(artifacts_dir.glob("*activation-record.json"))

    session_payload = json.loads(session_root.joinpath("session.json").read_text(encoding="utf-8"))
    assert session_payload["status"] == "activated"
    assert session_payload["recommendation_variant_id"] == result.recommended_variant_id


def test_worker_runtime_supports_approval_convergence_actions(tmp_path: Path) -> None:
    from ot_skill_enterprise.nextgen.worker_bridge.runtime import WorkerBridgeInvocationRequest, build_worker_runtime

    runtime = build_worker_runtime(
        project_root=REPO_ROOT,
        workspace_root=tmp_path,
        plugin_registry=build_default_plugin_registry(),
        adapter_registry=build_test_registry(),
        distillation_service=FakeDistillationService(),
    )

    baseline_variant = {
        "variant_id": "baseline",
        "title": "Baseline",
        "source": "distillation",
        "strategy_spec": {"setup_label": "baseline"},
        "execution_intent": {"adapter": "fake-execution", "preflight_checks": ["allowlist"]},
        "style_profile": {"wallet": "0xabc", "chain": "bsc", "preferred_tokens": ["SOL", "WETH"]},
        "artifacts": [],
    }
    finalist_variant = {
        "variant_id": "variant-1",
        "title": "Variant 1",
        "source": "skill-creation",
        "strategy_spec": {"setup_label": "baseline", "entry_conditions": [{"condition": "breakout"}], "risk_controls": ["stop"]},
        "execution_intent": {"adapter": "fake-execution", "preflight_checks": ["allowlist"], "max_position_pct": 0.12},
        "style_profile": {"wallet": "0xabc", "chain": "bsc", "preferred_tokens": ["SOL", "WETH"]},
        "artifacts": [],
        "metadata": {},
    }
    baseline_scorecard = {
        "variant_id": "baseline",
        "primary_quality_score": 0.6,
        "backtest_confidence": 0.62,
        "execution_readiness": "ready",
        "strategy_quality": "acceptable",
        "style_distance": 0.1,
        "risk_penalty": 0.05,
        "confidence_vs_noise": 0.5,
        "hard_gates_passed": True,
        "notes": [],
        "artifacts": [],
        "metadata": {},
    }
    benchmark_request = WorkerBridgeInvocationRequest.model_validate(
        {
            "bridge_id": "python-worker-bridge",
            "bridge_version": "1.0.0",
            "action_id": "benchmark.score_finalist",
            "workflow_id": "approval_convergence",
            "workflow_step_id": "benchmark_finalist",
            "workspace_dir": str(tmp_path),
            "request": {
                "workflow_id": "approval_convergence",
                "session_id": "approval-session-1",
                "workspace_id": "desk-alpha",
                "workspace_dir": str(tmp_path),
                "wallet": "0xabc",
                "chain": "bsc",
                "skill_name": "desk-alpha",
                "data_source_adapter_id": "fake-data",
                "execution_adapter_id": "fake-execution",
            },
            "state": {
                "baseline_variant": baseline_variant,
                "selected_variant": finalist_variant,
                "baseline_scorecard": baseline_scorecard,
                "iteration_index": 1,
            },
        }
    )
    benchmark_response = runtime.invoke(benchmark_request)
    assert benchmark_response.ok is True
    assert benchmark_response.status == "benchmarked"
    assert len(benchmark_response.state_patch["candidate_scorecards"]) == 1
    assert benchmark_response.state_patch["benchmark_scorecard"]["variant_id"] == "variant-1"

    review_request = WorkerBridgeInvocationRequest.model_validate(
        {
            "bridge_id": "python-worker-bridge",
            "bridge_version": "1.0.0",
            "action_id": "review.finalize_candidate",
            "workflow_id": "approval_convergence",
            "workflow_step_id": "review_finalist",
            "workspace_dir": str(tmp_path),
            "request": {
                "workflow_id": "approval_convergence",
                "session_id": "approval-session-1",
                "workspace_id": "desk-alpha",
                "workspace_dir": str(tmp_path),
                "wallet": "0xabc",
                "chain": "bsc",
                "skill_name": "desk-alpha",
                "data_source_adapter_id": "fake-data",
                "execution_adapter_id": "fake-execution",
                "objective": "final approval",
            },
            "state": {
                "baseline_variant": baseline_variant,
                "baseline_scorecard": baseline_scorecard,
                "selected_variant": finalist_variant,
                "candidate_scorecards": benchmark_response.state_patch["candidate_scorecards"],
                "iteration_index": 1,
            },
        }
    )
    review_response = runtime.invoke(review_request)
    assert review_response.ok is True
    assert review_response.status == "reviewed"
    assert len(review_response.state_patch["review_decisions"]) == 1
    assert review_response.state_patch["review_decision"]["variant_id"] == "variant-1"

    approval_request = WorkerBridgeInvocationRequest.model_validate(
        {
            "bridge_id": "python-worker-bridge",
            "bridge_version": "1.0.0",
            "action_id": "approval_convergence.converge_approval",
            "workflow_id": "approval_convergence",
            "workflow_step_id": "converge_approval",
            "workspace_dir": str(tmp_path),
            "request": {
                "workflow_id": "approval_convergence",
                "session_id": "approval-session-1",
                "workspace_id": "desk-alpha",
                "workspace_dir": str(tmp_path),
                "chain": "bsc",
                "skill_name": "desk-alpha",
                "data_source_adapter_id": "fake-data",
                "execution_adapter_id": "fake-execution",
                "metadata": {"approval_granted": True, "activation_requested": True},
            },
            "state": {
                "baseline_variant": baseline_variant,
                "selected_variant": finalist_variant,
                "recommendation_bundle": {
                    "workflow_id": "autonomous_research",
                    "baseline_variant_id": "baseline",
                    "session_id": "approval-session-1",
                    "workspace_id": "desk-alpha",
                    "status": "recommended",
                    "summary": "Variant 1 outperformed baseline.",
                    "recommended_variant_id": "variant-1",
                    "selected_variant": finalist_variant,
                    "leaderboard": [{"variant_id": "variant-1", "status": "recommended"}],
                    "scorecards": benchmark_response.state_patch["candidate_scorecards"],
                    "review_decisions": review_response.state_patch["review_decisions"],
                    "iterations": [],
                    "artifacts": [],
                    "metadata": {},
                },
                "benchmark_scorecard": benchmark_response.state_patch["benchmark_scorecard"],
                "review_decision": review_response.state_patch["review_decision"],
                "candidate_scorecards": benchmark_response.state_patch["candidate_scorecards"],
                "review_decisions": review_response.state_patch["review_decisions"],
            },
        }
    )
    approval_response = runtime.invoke(approval_request)
    assert approval_response.ok is True
    assert approval_response.status == "activated"
    assert approval_response.state_patch["approval"]["status"] == "activated"
    assert approval_response.state_patch["approval_activation_bundle"]["status"] == "activated"
    assert approval_response.state_patch["approval_activation_bundle"]["approval"]["activation_allowed"] is True
    assert approval_response.state_patch["approval_recommendation"]["recommended_variant_id"] == "variant-1"
    artifact_kinds = {artifact.ref.kind for artifact in approval_response.artifacts}
    assert {
        "benchmark_scorecard",
        "review_governance",
        "review_notes",
        "retry_suggestion",
        "approval_decision",
        "activation_record",
        "approval_activation_bundle",
        "approval_convergence_bundle",
        "approval_recommendation",
        "kernel_handoff_payload",
    } <= artifact_kinds


def test_run_approval_convergence_replays_matching_request_hints(tmp_path: Path) -> None:
    service = build_nextgen_workflow_service(
        project_root=REPO_ROOT,
        workspace_root=tmp_path,
        plugin_registry=build_default_plugin_registry(),
        adapter_registry=build_test_registry(),
        distillation_service=FakeDistillationService(),
        runtime_mode="python-compat",
    )
    service.run_autonomous_research(
        WorkflowRunRequest(
            workflow_id="autonomous_research",
            session_id="research-session-approval-replay",
            workspace_id="desk-alpha",
            wallet="0xabc",
            chain="bsc",
            skill_name="desk-alpha",
            workspace_dir=str(tmp_path),
            **WORKFLOW_ADAPTERS,
        )
    )
    request = WorkflowRunRequest(
        workflow_id="approval_convergence",
        session_id="research-session-approval-replay",
        workspace_id="desk-alpha",
        workspace_dir=str(tmp_path),
        metadata={"approval_granted": True, "activation_requested": False},
        **WORKFLOW_ADAPTERS,
    )

    first = service.run_approval_convergence(request)
    replay = service.run_approval_convergence(request)

    assert first.status == "approved"
    assert replay.status == "approved"
    assert replay.metadata["replayed"] is True
    assert replay.approval.status == "approved"
