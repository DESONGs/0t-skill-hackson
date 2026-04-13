from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from pydantic import Field, field_validator

from ot_skill_enterprise.shared.contracts.common import ContractModel, utc_now


def _normalize_text(value: Any) -> str:
    return str(value).strip()


class RuntimeTranscript(ContractModel):
    transcript_id: str = Field(default_factory=lambda: f"transcript-{uuid4().hex[:12]}")
    runtime_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    invocation_id: str | None = None
    ok: bool = True
    status: str = Field(default="succeeded", min_length=1)
    summary: str = Field(default="")
    input_payload: dict[str, Any] = Field(default_factory=dict)
    output_payload: dict[str, Any] = Field(default_factory=dict)
    events: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    provider_ids: list[str] = Field(default_factory=list)
    skill_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    stdout: str = ""
    stderr: str = ""
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("transcript_id", "runtime_id", "session_id", "status", mode="before")
    @classmethod
    def _normalize_required_text(cls, value: Any) -> str:
        text = _normalize_text(value)
        if not text:
            raise ValueError("transcript fields must not be empty")
        return text

    @classmethod
    def from_payload(
        cls,
        payload: dict[str, Any],
        *,
        runtime_id: str,
        session_id: str,
        invocation_id: str | None = None,
        stdout: str = "",
        stderr: str = "",
    ) -> "RuntimeTranscript":
        return cls(
            runtime_id=runtime_id,
            session_id=session_id,
            invocation_id=invocation_id,
            ok=bool(payload.get("ok", True)),
            status=str(payload.get("status") or "succeeded"),
            summary=str(payload.get("summary") or ""),
            input_payload=dict(payload.get("input") or payload.get("input_payload") or {}),
            output_payload=dict(payload.get("output") or payload.get("output_payload") or {}),
            events=[dict(item) for item in list(payload.get("events") or []) if isinstance(item, dict)],
            artifacts=[dict(item) for item in list(payload.get("artifacts") or []) if isinstance(item, dict)],
            provider_ids=[str(item) for item in list(payload.get("provider_ids") or []) if str(item).strip()],
            skill_ids=[str(item) for item in list(payload.get("skill_ids") or []) if str(item).strip()],
            metadata=dict(payload.get("metadata") or {}),
            stdout=stdout,
            stderr=stderr,
        )


RuntimeTranscript.model_rebuild()
