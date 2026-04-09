from __future__ import annotations

import json
from pathlib import Path

import pytest

from ot_skill_enterprise.lab import (
    PromotionSubmission,
    create_case,
    create_candidate_proposal,
    create_promotion_submission,
    intake_feedback,
)
from ot_skill_enterprise.registry import EvolutionRegistry


@pytest.mark.parametrize("status,expected_severity,problem_type", [("partial", "medium", "partial_run"), ("failed", "high", "failed_run")])
def test_feedback_becomes_case(status: str, expected_severity: str, problem_type: str, tmp_path: Path) -> None:
    registry = EvolutionRegistry(root=tmp_path / "registry")
    feedback = intake_feedback(
        {
            "run_id": "run-001",
            "skill_id": "analysis-core",
            "action_id": "write_report",
            "status": status,
            "summary": "report missing critical evidence",
            "error_code": "missing_evidence",
            "artifacts": [{"artifact_id": "art-1", "kind": "json", "uri": "memory://report.json"}],
        },
        registry=registry,
    )

    case = create_case(feedback, registry=registry)

    assert case.source.status == status
    assert case.severity == expected_severity
    assert case.pattern.problem_type == problem_type
    assert case.source.skill_id == "analysis-core"
    assert registry.get_case(case.case_id) is not None
    assert (tmp_path / "registry" / "cases" / f"{case.case_id}.json").exists()


def test_case_becomes_candidate_proposal(tmp_path: Path) -> None:
    registry = EvolutionRegistry(root=tmp_path / "registry")
    case = create_case(
        {
            "run_id": "run-002",
            "skill_id": "analysis-core",
            "action_id": "synthesize_evidence",
            "status": "partial",
            "summary": "report synthesis missed one source",
            "artifacts": [{"artifact_id": "art-2", "kind": "json", "uri": "memory://findings.json"}],
        },
        registry=registry,
    )

    proposal = create_candidate_proposal(case, registry=registry)

    assert proposal.case_id == case.case_id
    assert proposal.target_skill_name == "analysis-core"
    assert proposal.decision_mode == "candidate"
    assert proposal.change_summary
    assert registry.get_proposal(proposal.proposal_id) is not None
    assert (tmp_path / "registry" / "proposals" / f"{proposal.proposal_id}.json").exists()


def test_candidate_becomes_promotion_submission(tmp_path: Path) -> None:
    registry = EvolutionRegistry(root=tmp_path / "registry")
    case = create_case(
        {
            "run_id": "run-003",
            "skill_id": "analysis-core",
            "action_id": "plan_data_needs",
            "status": "failed",
            "summary": "planner could not infer enough data needs",
            "error_code": "plan_error",
        },
        registry=registry,
    )
    proposal = create_candidate_proposal(case, registry=registry)

    submission = create_promotion_submission(proposal, case=case, registry=registry)

    assert isinstance(submission, PromotionSubmission)
    assert submission.case_id == case.case_id
    assert submission.proposal_id == proposal.proposal_id
    assert submission.candidate_id.startswith("candidate-")
    assert submission.manifest["candidate"]["proposal_id"] == proposal.proposal_id
    assert submission.lineage["case_id"] == case.case_id
    assert submission.bundle_sha256
    assert registry.get_submission(submission.submission_id) is not None
    stored_path = tmp_path / "registry" / "submissions" / f"{submission.submission_id}.json"
    assert stored_path.exists()
    stored = json.loads(stored_path.read_text(encoding="utf-8"))
    assert stored["candidate_slug"] == submission.candidate_slug
