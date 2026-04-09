from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import Field

from ot_skill_enterprise.shared.contracts.common import ContractModel, utc_now


class PromotionSubmission(ContractModel):
    submission_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    proposal_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    candidate_slug: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    target_skill_name: str = Field(min_length=1)
    decision_mode: str = Field(min_length=1)
    bundle_path: Optional[str] = None
    bundle_sha256: str = Field(min_length=1)
    evaluation_summary: dict[str, str] = Field(default_factory=dict)
    manifest: dict[str, Any] = Field(default_factory=dict)
    lineage: dict[str, str] = Field(default_factory=dict)
    metadata: dict[str, str] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
