from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping, Optional, Protocol

from ot_skill_enterprise.registry import EvolutionRegistry
from ot_skill_enterprise.shared.contracts import AnalysisCase, AnalysisFeedback, AnalysisProposal, ArtifactRef

from .models import PromotionSubmission


class EvolutionRegistryProtocol(Protocol):
    def record_feedback(self, feedback: AnalysisFeedback) -> Mapping[str, Any]: ...

    def record_case(self, case: AnalysisCase) -> Mapping[str, Any]: ...

    def record_proposal(self, proposal: AnalysisProposal) -> Mapping[str, Any]: ...

    def record_submission(self, submission: PromotionSubmission) -> Mapping[str, Any]: ...

    def case_path(self, case_id: str) -> Path | None: ...

    def proposal_path(self, proposal_id: str) -> Path | None: ...

    def submission_path(self, submission_id: str) -> Path | None: ...


_CASEFUL_STATUSES = {"failed", "partial"}


def _dump_model(value: Any) -> Any:
    dumper = getattr(value, "model_dump", None)
    if dumper is not None:
        return dumper(mode="json")
    return value


def _stable_payload(value: Any) -> str:
    return json.dumps(_dump_model(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hashed_id(prefix: str, value: Any, *, length: int = 12) -> str:
    digest = hashlib.sha256(_stable_payload(value).encode("utf-8")).hexdigest()
    return f"{prefix}-{digest[:length]}"


def _normalize_feedback(feedback: AnalysisFeedback | Mapping[str, Any]) -> AnalysisFeedback:
    if isinstance(feedback, AnalysisFeedback):
        return feedback
    return AnalysisFeedback.model_validate(dict(feedback))


def _slugify(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", value.lower().strip())
    return text.strip("-") or "analysis-core"


def intake_feedback(
    feedback: AnalysisFeedback | Mapping[str, Any],
    *,
    registry: EvolutionRegistryProtocol | None = None,
) -> AnalysisFeedback:
    normalized = _normalize_feedback(feedback)
    if registry is not None:
        registry.record_feedback(normalized)
    return normalized


def create_case(
    feedback: AnalysisFeedback | Mapping[str, Any],
    *,
    registry: EvolutionRegistryProtocol | None = None,
) -> AnalysisCase:
    normalized = _normalize_feedback(feedback)
    if registry is not None and not isinstance(feedback, AnalysisFeedback):
        registry.record_feedback(normalized)
    status = normalized.status.strip().lower()
    if status not in _CASEFUL_STATUSES:
        raise ValueError(f"feedback status does not create a case: {normalized.status}")

    source_payload = {
        "run_id": normalized.run_id,
        "skill_id": normalized.skill_id,
        "action_id": normalized.action_id,
        "status": status,
        "summary": normalized.summary.strip(),
        "error_code": normalized.error_code,
        "artifacts": [artifact.model_dump(mode="json") for artifact in normalized.artifacts],
        "metadata": normalized.metadata,
    }
    case_id = _hashed_id("case", source_payload)
    problem_type = "failed_run" if status == "failed" else "partial_run"
    severity = "high" if status == "failed" else "medium"
    pattern_summary = f"{normalized.skill_id}/{normalized.action_id} returned {status}: {normalized.summary.strip()}"
    tags = [normalized.skill_id, normalized.action_id, status]
    if normalized.error_code:
        tags.append(normalized.error_code)
    case = AnalysisCase(
        case_id=case_id,
        source={
            "run_id": normalized.run_id,
            "skill_id": normalized.skill_id,
            "action_id": normalized.action_id,
            "status": normalized.status,
            "summary": normalized.summary,
            "artifacts": [artifact.model_dump(mode="json") for artifact in normalized.artifacts],
            "error_code": normalized.error_code,
            "metadata": normalized.metadata,
        },
        pattern={"problem_type": problem_type, "summary": pattern_summary, "tags": tags},
        evidence=[artifact for artifact in normalized.artifacts],
        severity=severity,
        metadata={
            "analysis_scope": "analysis-core",
            "feedback_status": normalized.status,
            "case_signature": _hashed_id("sig", source_payload),
        },
    )
    if registry is not None:
        registry.record_case(case)
    return case


def create_candidate_proposal(
    case: AnalysisCase | Mapping[str, Any],
    *,
    target_skill_name: str = "analysis-core",
    decision_mode: str = "candidate",
    target_layer: str = "analysis",
    registry: EvolutionRegistryProtocol | None = None,
) -> AnalysisProposal:
    normalized = case if isinstance(case, AnalysisCase) else AnalysisCase.model_validate(dict(case))
    change_summary = (
        "Harden partial-run recovery and completeness checks"
        if normalized.pattern.problem_type == "partial_run"
        else "Add explicit failure surfacing and fallback handling"
    )
    proposal_id = _hashed_id(
        "proposal",
        {
            "case_id": normalized.case_id,
            "problem_type": normalized.pattern.problem_type,
            "summary": normalized.pattern.summary,
            "severity": normalized.severity,
            "target_skill_name": target_skill_name,
            "change_summary": change_summary,
            "decision_mode": decision_mode,
            "target_layer": target_layer,
        },
    )
    proposal = AnalysisProposal(
        proposal_id=proposal_id,
        case_id=normalized.case_id,
        target_skill_name=target_skill_name,
        decision_mode=decision_mode,
        change_summary=change_summary,
        target_layer=target_layer,
        metadata={
            "analysis_scope": "analysis-core",
            "case_problem_type": normalized.pattern.problem_type,
            "case_severity": normalized.severity,
            "case_signature": normalized.metadata.get("case_signature", ""),
        },
    )
    if registry is not None:
        registry.record_proposal(proposal)
    return proposal


def create_promotion_submission(
    proposal: AnalysisProposal | Mapping[str, Any],
    *,
    case: AnalysisCase | Mapping[str, Any] | None = None,
    run_id: str | None = None,
    registry: EvolutionRegistryProtocol | None = None,
) -> PromotionSubmission:
    normalized_proposal = proposal if isinstance(proposal, AnalysisProposal) else AnalysisProposal.model_validate(dict(proposal))
    normalized_case = None
    if case is not None:
        normalized_case = case if isinstance(case, AnalysisCase) else AnalysisCase.model_validate(dict(case))
    case_id = normalized_proposal.case_id if normalized_case is None else normalized_case.case_id
    resolved_run_id = run_id or (normalized_case.source.run_id if normalized_case is not None else f"run-{case_id}")
    candidate_id = _hashed_id("candidate", {"proposal_id": normalized_proposal.proposal_id, "case_id": case_id, "run_id": resolved_run_id})
    candidate_slug = _slugify(f"{normalized_proposal.target_skill_name}-{candidate_id}")
    manifest = {
        "candidate": {
            "id": candidate_id,
            "slug": candidate_slug,
            "proposal_id": normalized_proposal.proposal_id,
            "case_id": case_id,
            "target_skill_name": normalized_proposal.target_skill_name,
            "decision_mode": normalized_proposal.decision_mode,
            "change_summary": normalized_proposal.change_summary,
            "target_layer": normalized_proposal.target_layer,
        },
        "lineage": {
            "case_id": case_id,
            "proposal_id": normalized_proposal.proposal_id,
            "decision_mode": normalized_proposal.decision_mode,
            "target_skill_name": normalized_proposal.target_skill_name,
        },
    }
    submission_id = _hashed_id(
        "submission",
        {
            "candidate_id": candidate_id,
            "proposal_id": normalized_proposal.proposal_id,
            "case_id": case_id,
            "run_id": resolved_run_id,
            "manifest": manifest,
        },
    )
    bundle_sha256 = hashlib.sha256(_stable_payload(manifest).encode("utf-8")).hexdigest()
    bundle_path_obj = registry.submission_path(submission_id) if registry is not None else None
    bundle_path = str(bundle_path_obj) if bundle_path_obj is not None else None
    submission = PromotionSubmission(
        submission_id=submission_id,
        case_id=case_id,
        proposal_id=normalized_proposal.proposal_id,
        candidate_id=candidate_id,
        candidate_slug=candidate_slug,
        run_id=resolved_run_id,
        target_skill_name=normalized_proposal.target_skill_name,
        decision_mode=normalized_proposal.decision_mode,
        bundle_path=bundle_path,
        bundle_sha256=bundle_sha256,
        evaluation_summary={
            "case_id": case_id,
            "proposal_id": normalized_proposal.proposal_id,
            "decision_mode": normalized_proposal.decision_mode,
            "change_summary": normalized_proposal.change_summary,
            "target_layer": normalized_proposal.target_layer,
        },
        manifest=manifest,
        lineage=manifest["lineage"],
        metadata={
            "analysis_scope": "analysis-core",
            "candidate_id": candidate_id,
            "candidate_slug": candidate_slug,
        },
    )
    if registry is not None:
        registry.record_submission(submission)
    return submission


def advance_feedback(
    feedback: AnalysisFeedback | Mapping[str, Any],
    *,
    registry: EvolutionRegistryProtocol | None = None,
) -> dict[str, Any]:
    normalized = intake_feedback(feedback, registry=registry)
    case = create_case(normalized, registry=registry)
    proposal = create_candidate_proposal(case, registry=registry)
    submission = create_promotion_submission(proposal, case=case, run_id=normalized.run_id, registry=registry)
    return {
        "feedback": normalized,
        "case": case,
        "proposal": proposal,
        "submission": submission,
    }
