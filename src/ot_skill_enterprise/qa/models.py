from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field, model_validator

from ot_skill_enterprise.shared.contracts.common import ContractModel, utc_now


class RuntimeEvaluationResult(ContractModel):
    passed: bool = False
    status: str = Field(default="pending", min_length=1)
    summary: str = ""
    error_code: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ContractEvaluationResult(ContractModel):
    passed: bool = False
    status: str = Field(default="pending", min_length=1)
    summary: str = ""
    missing_fields: list[str] = Field(default_factory=list)
    violations: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskMatchEvaluationResult(ContractModel):
    passed: bool = False
    status: str = Field(default="pending", min_length=1)
    summary: str = ""
    score: float = 0.0
    threshold: float = 0.8
    evidence_refs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


def _grade_from_results(runtime_pass: bool, contract_pass: bool, task_match_score: float) -> str:
    if not runtime_pass or not contract_pass:
        return "fail"
    if task_match_score >= 0.8:
        return "pass"
    if task_match_score >= 0.5:
        return "warn"
    return "fail"


def _grade_from_review_status(review_status: str, base_grade: str) -> str:
    normalized = review_status.strip().lower()
    if normalized == "runtime_failed":
        return "fail"
    if normalized == "generate":
        return "pass" if base_grade == "pass" else base_grade
    if normalized in {"generate_with_low_confidence", "insufficient_signal", "no_pattern_detected"}:
        if base_grade == "fail":
            return "fail"
        return "warn"
    return base_grade


class EvaluationRecord(ContractModel):
    evaluation_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    runtime_session_id: str = Field(min_length=1)
    subject_type: str = Field(min_length=1, default="run")
    subject_id: str = Field(min_length=1)
    runtime_result: RuntimeEvaluationResult = Field(default_factory=RuntimeEvaluationResult)
    contract_result: ContractEvaluationResult = Field(default_factory=ContractEvaluationResult)
    task_match_result: TaskMatchEvaluationResult = Field(default_factory=TaskMatchEvaluationResult)
    runtime_pass: bool = False
    contract_pass: bool = False
    task_match_score: float = 0.0
    overall_grade: str = Field(default="pending", min_length=1)
    review_status: str = Field(default="pending", min_length=1)
    failure_reason: str | None = None
    suggested_action: str | None = None
    grade: str = Field(default="pending", min_length=1)
    summary: str = Field(min_length=1)
    trace_ids: list[str] = Field(default_factory=list)
    event_ids: list[str] = Field(default_factory=list)
    event_types: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    checks: list[str] = Field(default_factory=list)
    findings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def _sync_quality_fields(self) -> "EvaluationRecord":
        self.runtime_pass = bool(self.runtime_result.passed)
        self.contract_pass = bool(self.contract_result.passed)
        self.task_match_score = float(self.task_match_result.score or 0.0)
        normalized_grade = self.overall_grade.strip().lower() if self.overall_grade else ""
        if normalized_grade in {"pending", ""}:
            normalized_grade = _grade_from_results(self.runtime_pass, self.contract_pass, self.task_match_score)
        review_status = str(self.review_status or "").strip().lower()
        if review_status and review_status != "pending":
            normalized_grade = _grade_from_review_status(review_status, normalized_grade)
        self.overall_grade = normalized_grade
        self.grade = self.overall_grade
        if not review_status or review_status == "pending":
            self.review_status = "runtime_failed" if not self.runtime_pass else "generate"
        if self.failure_reason is None:
            if not self.runtime_pass and self.runtime_result.summary:
                self.failure_reason = self.runtime_result.summary
            elif not self.contract_pass and self.contract_result.summary:
                self.failure_reason = self.contract_result.summary
            elif self.task_match_result.status in {"failed", "partial"} and self.task_match_result.summary:
                self.failure_reason = self.task_match_result.summary
        if self.suggested_action is None:
            self.suggested_action = self.metadata.get("suggested_action") or self.metadata.get("recommendation")
        combined_evidence = list(self.evidence_refs)
        for value in (
            self.runtime_result.evidence_refs,
            self.contract_result.evidence_refs,
            self.task_match_result.evidence_refs,
        ):
            for ref in value:
                if ref and ref not in combined_evidence:
                    combined_evidence.append(ref)
        self.evidence_refs = combined_evidence
        if not self.checks:
            self.checks = [
                self.runtime_result.summary or f"runtime:{'pass' if self.runtime_pass else 'fail'}",
                self.contract_result.summary or f"contract:{'pass' if self.contract_pass else 'fail'}",
                self.task_match_result.summary or f"task-match:{self.task_match_score:.2f}",
            ]
        return self
