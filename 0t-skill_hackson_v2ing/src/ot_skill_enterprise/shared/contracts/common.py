from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Generic, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ArtifactRef(ContractModel):
    artifact_id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    uri: Optional[str] = None
    label: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ServiceError(ContractModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    details: dict[str, Any] = Field(default_factory=dict)


class EnvelopeMeta(ContractModel):
    provider: str = "ave"
    request_id: Optional[str] = None
    latency_ms: Optional[int] = None
    cached: bool = False
    source: Optional[str] = None
    timestamp: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


DataT = TypeVar("DataT")


class ServiceEnvelope(ContractModel, Generic[DataT]):
    ok: bool = True
    operation: str = Field(min_length=1)
    request_id: Optional[str] = None
    data: Optional[DataT] = None
    error: Optional[ServiceError] = None
    meta: EnvelopeMeta = Field(default_factory=EnvelopeMeta)

    @model_validator(mode="after")
    def _validate_consistency(self) -> "ServiceEnvelope[DataT]":
        if self.ok and self.error is not None:
            raise ValueError("successful envelope must not include error")
        if not self.ok and self.error is None:
            raise ValueError("failed envelope must include error")
        return self
