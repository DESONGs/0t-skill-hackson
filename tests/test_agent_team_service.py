from __future__ import annotations

import json
from pathlib import Path

from ot_skill_enterprise.team.cli import main as ot_team_main
from ot_skill_enterprise.team.protocol import load_team_protocol_bundle
from ot_skill_enterprise.team.service import build_agent_team_service


REPO_ROOT = Path(__file__).resolve().parents[1]


def _build_service(tmp_path: Path):
    return build_agent_team_service(project_root_path=REPO_ROOT, workspace_root=tmp_path / ".ot-workspace")


def test_team_protocol_bundle_normalizes_repo_tracked_specs() -> None:
    bundle = load_team_protocol_bundle(REPO_ROOT)

    workflow = bundle.workflow("autoresearch")
    module = bundle.module("autoresearch")

    assert workflow.module_id == "autoresearch"
    assert workflow.roles == ["planner", "optimizer", "reviewer"]
    assert workflow.team_topology == "homogeneous"
    assert "strategy_spec" in module.search_space_schema["allowed_fields"]
    assert module.capability_type == "workflow_optimizer"


def test_ot_team_doctor_cli_reports_protocol_readiness(tmp_path, capsys) -> None:
    exit_code = ot_team_main(["--workspace-dir", str(tmp_path / ".ot-workspace"), "doctor"])
    captured = capsys.readouterr()

    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["status"] == "ready"
    assert payload["workflow_count"] >= 1
    assert {item["adapter_id"] for item in payload["adapters"]} >= {"codex", "claude-code"}


def test_agent_team_service_end_to_end_session_flow(tmp_path) -> None:
    service = _build_service(tmp_path)

    started = service.start_session(
        "autoresearch",
        workspace_id="desk-alpha",
        skill_ref="sample-skill",
        adapter_family="codex",
    )

    session_id = started["session"]["session_id"]
    planner_item_id = started["work_items"][0]["work_item_id"]

    handoff = service.handoff(session_id, role_id="planner")
    assert handoff["agent_session"]["adapter_id"] == "codex"
    assert "Planner Handoff" in handoff["launch"]["handoff_markdown"]

    service.submit_work(
        session_id,
        work_item_id=planner_item_id,
        agent_id="codex/planner-1",
        payload={
            "summary": "Planner scoped the session.",
            "constraints": {"max_iterations": 3},
            "hard_gates": ["hard_gates_passed == true", "style_distance <= 0.35"],
        },
    )

    status_after_planner = service.status(session_id)
    optimizer_item = next(item for item in status_after_planner["work_items"] if item["role_id"] == "optimizer")
    assert optimizer_item["status"] == "queued"

    optimizer_result = service.submit_work(
        session_id,
        role_id="optimizer",
        agent_id="codex/optimizer-1",
        payload={
            "variants": [
                {
                    "title": "Trend Routed",
                    "summary": "Tighter route preference and slower pacing.",
                    "strategy_patch": {"entry": "higher conviction"},
                    "execution_patch": {"route_preference": "preferred pools", "cooldown_minutes": 20},
                }
            ]
        },
    )

    variant_id = optimizer_result["variants"][0]["variant_id"]
    benchmark_item = next(item for item in optimizer_result["spawned_work_items"] if item["role_id"] == "benchmark-runner")
    reviewer_item = next(item for item in optimizer_result["spawned_work_items"] if item["role_id"] == "reviewer")

    benchmark_result = service.submit_work(
        session_id,
        work_item_id=benchmark_item["work_item_id"],
        agent_id="system/benchmark-runner",
        payload={
            "variant_id": variant_id,
            "summary": "Benchmark completed.",
            "scorecard": {
                "primary_quality_score": 0.82,
                "backtest_confidence": 0.76,
                "execution_readiness": 0.90,
                "strategy_quality": 0.79,
                "style_distance": 0.20,
                "risk_penalty": 0.12,
                "confidence_vs_noise": 0.21,
                "hard_gates_passed": True,
            },
            "metrics": {"sharpe_proxy": 1.4},
        },
    )

    assert benchmark_result["variant"]["scorecard"]["primary_quality_score"] == 0.82

    reviewer_result = service.submit_work(
        session_id,
        work_item_id=reviewer_item["work_item_id"],
        agent_id="codex/reviewer-1",
        payload={
            "variant_id": variant_id,
            "decision": "recommended",
            "summary": "Variant beats baseline and keeps style drift inside policy.",
            "reviewer_confidence": 0.88,
        },
    )

    assert reviewer_result["recommendation"]["status"] == "recommended"

    review_payload = service.review(session_id)
    assert review_payload["recommendation"]["variant_id"] == variant_id

    leaderboard = service.leaderboard(session_id)["leaderboard"]
    assert leaderboard[0]["variant_id"] == variant_id
    assert leaderboard[0]["decision"] == "recommended"

    approval = service.approve(session_id, variant_id=variant_id, approved_by="human", activate=True)
    assert approval["activation"]["status"] == "activated"
    assert approval["session"]["status"] == "activated"

    archived = service.archive(session_id)
    assert archived["session"]["status"] == "archived"
