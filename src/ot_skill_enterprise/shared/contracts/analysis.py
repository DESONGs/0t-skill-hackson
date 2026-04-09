from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import Field, field_validator

from .common import ArtifactRef, ContractModel, utc_now


class ReportFinding(ContractModel):
    title: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    severity: Literal["info", "low", "medium", "high", "critical"] = "medium"
    evidence_refs: list[str] = Field(default_factory=list)


class AnalysisReportDocument(ContractModel):
    task_summary: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    key_findings: list[ReportFinding] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    data_sources: list[ArtifactRef] = Field(default_factory=list)
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, str] = Field(default_factory=dict)


class AnalysisReportBundle(ContractModel):
    report_md: str = Field(min_length=1)
    report_json: AnalysisReportDocument


class RunFeedbackRecord(ContractModel):
    run_id: str = Field(min_length=1)
    skill_id: str = Field(min_length=1)
    action_id: str = Field(min_length=1)
    status: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    error_code: Optional[str] = None
    metadata: dict[str, str] = Field(default_factory=dict)


class CasePattern(ContractModel):
    problem_type: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)

    @field_validator("tags", mode="before")
    @classmethod
    def _normalize_tags(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return [str(value).strip()] if str(value).strip() else []


class CaseSource(ContractModel):
    run_id: str = Field(min_length=1)
    skill_id: str = Field(min_length=1)
    action_id: str = Field(min_length=1)
    status: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    error_code: Optional[str] = None
    metadata: dict[str, str] = Field(default_factory=dict)


class AnalysisCase(ContractModel):
    case_id: str = Field(min_length=1)
    source: CaseSource
    pattern: CasePattern
    evidence: list[ArtifactRef] = Field(default_factory=list)
    severity: Literal["low", "medium", "high", "critical"] = "medium"
    metadata: dict[str, str] = Field(default_factory=dict)


class ProposalRecord(ContractModel):
    proposal_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    target_skill_name: str = Field(min_length=1)
    decision_mode: str = Field(min_length=1)
    change_summary: str = Field(min_length=1)
    target_layer: str = Field(min_length=1)
    metadata: dict[str, str] = Field(default_factory=dict)


class AnalysisFeedback(ContractModel):
    run_id: str = Field(min_length=1)
    skill_id: str = Field(min_length=1)
    action_id: str = Field(min_length=1)
    status: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    error_code: Optional[str] = None
    metadata: dict[str, str] = Field(default_factory=dict)


RunFeedbackRecord = AnalysisFeedback


AnalysisProposal = ProposalRecord
