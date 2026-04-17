from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field, model_validator

from ot_skill_enterprise.shared.contracts.common import ContractModel, utc_now


class SkillCandidate(ContractModel):
    candidate_id: str = Field(min_length=1)
    source_run_id: str = Field(min_length=1)
    source_evaluation_id: str = Field(min_length=1)
    candidate_type: str = Field(default="general", min_length=1)
    target_skill_name: str = Field(min_length=1)
    target_skill_kind: str = Field(min_length=1)
    change_summary: str = Field(min_length=1)
    generation_spec: dict[str, Any] = Field(default_factory=dict)
    manifest_preview: dict[str, Any] = Field(default_factory=dict)
    status: str = Field(default="pending", min_length=1)
    validation_status: str = Field(default="pending", min_length=1)
    package_path: str | None = None
    bundle_sha256: str | None = None
    runtime_session_id: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def _sync_statuses(self) -> "SkillCandidate":
        if not self.validation_status.strip():
            self.validation_status = self.status
        return self


class PromotionRecord(ContractModel):
    promotion_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    source_run_id: str = Field(min_length=1)
    source_evaluation_id: str = Field(min_length=1)
    candidate_type: str = Field(default="general", min_length=1)
    candidate_slug: str = Field(min_length=1)
    target_skill_name: str = Field(min_length=1)
    target_skill_kind: str = Field(min_length=1)
    package_path: str | None = None
    bundle_sha256: str = Field(min_length=1)
    validation_status: str = Field(default="pending", min_length=1)
    registry_status: str = Field(default="pending", min_length=1)
    package_manifest: dict[str, Any] = Field(default_factory=dict)
    validation_report: dict[str, Any] = Field(default_factory=dict)
    lineage: dict[str, Any] = Field(default_factory=dict)
    runtime_session_id: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


PromotionSubmission = PromotionRecord


__all__ = ["SkillCandidate", "PromotionRecord", "PromotionSubmission"]
