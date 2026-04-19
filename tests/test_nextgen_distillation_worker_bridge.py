from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from ot_skill_enterprise.nextgen.adapters.registry import AdapterRegistry
from ot_skill_enterprise.nextgen.plugins.registry import build_default_plugin_registry
from ot_skill_enterprise.nextgen.worker_bridge import (
    DistillationWorkerBridgeRequest,
    DistillationWorkerProtocol,
    build_distillation_worker_handler,
    load_distillation_worker_protocol,
)
from ot_skill_enterprise.nextgen.workflows import WorkflowRunRequest, build_nextgen_workflow_service


REPO_ROOT = Path(__file__).resolve().parents[1]


class FakeDistillationService:
    def distill_wallet_style(
        self,
        *,
        wallet: str,
        chain: str | None = None,
        skill_name: str | None = None,
        **_: object,
    ) -> dict[str, object]:
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
            "candidate": {"candidate_id": "candidate-001"},
            "package": {"package_id": "package-001"},
            "promotion": {"promotion_id": "promotion-001", "skill_slug": skill_name or "seed-skill"},
            "qa": {"status": "passed"},
            "backtest": {"confidence_score": 0.74},
            "review": {"status": "approved"},
            "summary": {"summary": "Baseline distillation completed"},
            "artifacts": {"style_profile": "/tmp/style_profile.json"},
        }


def _protocol() -> DistillationWorkerProtocol:
    return load_distillation_worker_protocol(build_default_plugin_registry(), workflow_id="distillation_seed")


def test_worker_bridge_request_requires_wallet() -> None:
    protocol = _protocol()

    with pytest.raises(ValidationError, match="String should have at least 1 character"):
        DistillationWorkerBridgeRequest(
            plugin_version=protocol.plugin_version,
            workflow_id=protocol.workflow_id,
            workflow_step_id=protocol.workflow_step_id,
            operation="plan",
            capability_id=protocol.capability_bindings["plan"],
            wallet="",
            chain="bsc",
            protocol=protocol,
        )


def test_worker_bridge_request_rejects_unknown_operation() -> None:
    protocol = _protocol()

    with pytest.raises(ValidationError, match="Input should be"):
        DistillationWorkerBridgeRequest(
            plugin_version=protocol.plugin_version,
            workflow_id=protocol.workflow_id,
            workflow_step_id=protocol.workflow_step_id,
            operation="hydrate",  # type: ignore[arg-type]
            capability_id=protocol.capability_bindings["plan"],
            wallet="0xabc",
            chain="bsc",
            protocol=protocol,
        )


def test_protocol_loader_reads_distillation_manifest_semantics() -> None:
    protocol = _protocol()

    assert protocol.protocol_id == "distillation.wallet_style"
    assert protocol.operation_order == ["plan", "execute", "validate", "summarize"]
    assert protocol.capability_bindings == {
        "plan": "distill_wallet_style",
        "execute": "emit_seed_skill",
        "validate": "emit_seed_skill",
        "summarize": "emit_seed_skill",
    }
    assert protocol.compat_result_key == "raw_distillation_result"


def test_distillation_worker_handler_returns_normalized_outputs_and_raw_result() -> None:
    protocol = _protocol()
    handler = build_distillation_worker_handler(FakeDistillationService())

    response = handler.run_protocol(
        workflow_request=WorkflowRunRequest(
            workflow_id="distillation_seed",
            wallet="0xabc",
            chain="bsc",
            skill_name="desk-alpha",
        ),
        protocol=protocol,
    )

    assert response.operation == "summarize"
    assert response.status == "summarized"
    assert response.baseline_variant is not None
    assert response.baseline_variant.variant_id == "baseline"
    assert response.baseline_variant.title == "desk-alpha"
    assert response.baseline_variant.strategy_spec["setup_label"] == "momentum-breakout"
    assert {item.ref.kind for item in response.artifacts} >= {
        "style_profile",
        "strategy_spec",
        "execution_intent",
        "distillation_report",
        "seed_skill_package",
    }
    assert response.raw_result["promotion"]["promotion_id"] == "promotion-001"
    assert response.compat_payload["raw_distillation_result"]["job_id"] == "job-001"
    assert response.state["validation"]["passed"] is True
    assert [item.operation for item in response.events] == ["plan", "execute", "validate", "summarize"]


def test_run_distillation_seed_uses_worker_bridge_protocol_trace() -> None:
    service = build_nextgen_workflow_service(
        project_root=REPO_ROOT,
        plugin_registry=build_default_plugin_registry(),
        adapter_registry=AdapterRegistry(),
        distillation_service=FakeDistillationService(),
    )

    result = service.run_distillation_seed(
        WorkflowRunRequest(
            workflow_id="distillation_seed",
            wallet="0xabc",
            chain="bsc",
            skill_name="desk-alpha",
        )
    )

    assert result.baseline_variant.variant_id == "baseline"
    assert result.metadata["raw_distillation_result"]["job_id"] == "job-001"
    assert result.metadata["distillation_protocol"]["operation_order"] == [
        "plan",
        "execute",
        "validate",
        "summarize",
    ]
    assert [item["operation"] for item in result.metadata["worker_bridge"]["events"]] == [
        "plan",
        "execute",
        "validate",
        "summarize",
    ]
