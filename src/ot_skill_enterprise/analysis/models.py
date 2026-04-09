from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import Field, field_validator

from ot_skill_enterprise.shared.contracts import ArtifactRef
from ot_skill_enterprise.shared.contracts.analysis import ReportFinding
from ot_skill_enterprise.shared.contracts.common import ContractModel, utc_now


class AnalysisRequest(ContractModel):
    topic: str = Field(min_length=1)
    objective: Optional[str] = None
    scope: str = "AVE-backed analysis"
    questions: list[str] = Field(default_factory=list)
    focus_domains: list[str] = Field(default_factory=list)
    request_id: Optional[str] = None
    workspace_hint: Optional[str] = None
    metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator("topic", "objective", "scope", "workspace_hint", mode="before")
    @classmethod
    def _normalize_text(cls, value: object) -> object:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @field_validator("questions", "focus_domains", mode="before")
    @classmethod
    def _normalize_list(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, (list, tuple, set)):
            return [str(item).strip() for item in value if str(item).strip()]
        text = str(value).strip()
        return [text] if text else []


class AnalysisDataNeed(ContractModel):
    action: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    inputs: dict[str, Any] = Field(default_factory=dict)


class AnalysisPlan(ContractModel):
    plan_id: str = Field(min_length=1)
    request: AnalysisRequest
    scope: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    questions: list[str] = Field(default_factory=list)
    data_needs: list[AnalysisDataNeed] = Field(default_factory=list)
    ordered_actions: list[str] = Field(default_factory=list)
    data_artifacts: list[ArtifactRef] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, str] = Field(default_factory=dict)


class EvidenceFinding(ReportFinding):
    implication: Optional[str] = None
    recommendation: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceBundle(ContractModel):
    bundle_id: str = Field(min_length=1)
    plan_id: str = Field(min_length=1)
    task_summary: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    findings: list[EvidenceFinding] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    data_sources: list[ArtifactRef] = Field(default_factory=list)
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, str] = Field(default_factory=dict)
