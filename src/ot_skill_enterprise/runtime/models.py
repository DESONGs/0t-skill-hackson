from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field, field_validator, model_validator

from ot_skill_enterprise.shared.contracts import ArtifactRef
from ot_skill_enterprise.shared.contracts.common import ContractModel, ServiceError, utc_now
from .execution import RuntimeExecutionRequest, RuntimeExecutionResult
from .transcript import RuntimeTranscript


def _coerce_mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
        if isinstance(dumped, dict):
            return dict(dumped)
    return {"value": value}


def _normalize_text(value: Any) -> str:
    return str(value).strip()


class RuntimeDescriptor(ContractModel):
    runtime_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    runtime_type: str = Field(default="runtime", min_length=1)
    version: str = Field(default="0.0.0", min_length=1)
    execution_mode: str = Field(default="embedded", min_length=1)
    source_root: str | None = None
    bundle_root: str | None = None
    entrypoint: str | None = None
    supported_actions: list[str] = Field(default_factory=list)
    tool_surface: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("runtime_id", "name", "runtime_type", "execution_mode", mode="before")
    @classmethod
    def _normalize_strings(cls, value: Any) -> str:
        text = _normalize_text(value)
        if not text:
            raise ValueError("runtime descriptor fields must not be empty")
        return text

    @field_validator("supported_actions", "tool_surface", mode="before")
    @classmethod
    def _normalize_string_list(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, tuple):
            return [str(item).strip() for item in value if str(item).strip()]
        text = str(value).strip()
        return [text] if text else []


class RuntimeToolCall(ContractModel):
    tool_call_id: str = Field(min_length=1)
    runtime_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    invocation_id: str | None = None
    tool_name: str = Field(min_length=1)
    args: dict[str, Any] = Field(default_factory=dict)
    status: str = Field(default="pending", min_length=1)
    result: dict[str, Any] | None = None
    error: ServiceError | None = None
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("tool_call_id", "runtime_id", "session_id", "tool_name", "status", mode="before")
    @classmethod
    def _normalize_required_text(cls, value: Any) -> str:
        text = _normalize_text(value)
        if not text:
            raise ValueError("tool call fields must not be empty")
        return text


class RuntimeArtifact(ContractModel):
    artifact_id: str = Field(min_length=1)
    runtime_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    invocation_id: str | None = None
    tool_call_id: str | None = None
    kind: str = Field(min_length=1)
    uri: str | None = None
    label: str | None = None
    content_type: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    checksum: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("artifact_id", "runtime_id", "session_id", "kind", mode="before")
    @classmethod
    def _normalize_required_text(cls, value: Any) -> str:
        text = _normalize_text(value)
        if not text:
            raise ValueError("artifact fields must not be empty")
        return text

    def as_ref(self) -> ArtifactRef:
        return ArtifactRef.model_validate(
            {
                "artifact_id": self.artifact_id,
                "kind": self.kind,
                "uri": self.uri,
                "label": self.label,
                "metadata": dict(self.metadata),
            }
        )


class RuntimeEvent(ContractModel):
    event_id: str = Field(min_length=1)
    runtime_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    invocation_id: str | None = None
    event_type: str = Field(min_length=1)
    category: str = Field(default="runtime", min_length=1)
    source: str = Field(default="pi", min_length=1)
    message: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    tool_call: RuntimeToolCall | None = None
    artifact: RuntimeArtifact | None = None
    timestamp: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("event_id", "runtime_id", "session_id", "event_type", "category", "source", mode="before")
    @classmethod
    def _normalize_required_text(cls, value: Any) -> str:
        text = _normalize_text(value)
        if not text:
            raise ValueError("event fields must not be empty")
        return text


class RuntimeInvocation(ContractModel):
    invocation_id: str = Field(min_length=1)
    runtime_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    kind: str = Field(default="turn", min_length=1)
    status: str = Field(default="pending", min_length=1)
    ok: bool = True
    prompt: str | None = None
    input_payload: dict[str, Any] = Field(default_factory=dict)
    output_payload: dict[str, Any] = Field(default_factory=dict)
    events: list[RuntimeEvent] = Field(default_factory=list)
    tool_calls: list[RuntimeToolCall] = Field(default_factory=list)
    artifacts: list[RuntimeArtifact] = Field(default_factory=list)
    error: ServiceError | None = None
    summary: str = Field(default="")
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("invocation_id", "runtime_id", "session_id", "kind", "status", mode="before")
    @classmethod
    def _normalize_required_text(cls, value: Any) -> str:
        text = _normalize_text(value)
        if not text:
            raise ValueError("invocation fields must not be empty")
        return text


class RuntimeSession(ContractModel):
    session_id: str = Field(min_length=1)
    runtime_id: str = Field(min_length=1)
    descriptor: RuntimeDescriptor | None = None
    status: str = Field(default="created", min_length=1)
    cwd: str | None = None
    parent_session_id: str | None = None
    input_payload: dict[str, Any] = Field(default_factory=dict)
    output_payload: dict[str, Any] = Field(default_factory=dict)
    invocations: list[RuntimeInvocation] = Field(default_factory=list)
    events: list[RuntimeEvent] = Field(default_factory=list)
    artifacts: list[RuntimeArtifact] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("session_id", "runtime_id", "status", mode="before")
    @classmethod
    def _normalize_required_text(cls, value: Any) -> str:
        text = _normalize_text(value)
        if not text:
            raise ValueError("session fields must not be empty")
        return text

    @model_validator(mode="after")
    def _ensure_updated_at(self) -> "RuntimeSession":
        if self.updated_at < self.started_at:
            self.updated_at = self.started_at
        return self


def runtime_artifact_ref(artifact: RuntimeArtifact | ArtifactRef | dict[str, Any]) -> ArtifactRef:
    if isinstance(artifact, ArtifactRef):
        return artifact
    if isinstance(artifact, RuntimeArtifact):
        return artifact.as_ref()
    if isinstance(artifact, dict):
        return ArtifactRef.model_validate(artifact)
    raise TypeError("unsupported artifact payload")


def runtime_model_payload(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        payload = value.model_dump(mode="json")
        if isinstance(payload, dict):
            return dict(payload)
    if isinstance(value, dict):
        return dict(value)
    return _coerce_mapping(value)


RuntimeDescriptor.model_rebuild()
RuntimeToolCall.model_rebuild()
RuntimeArtifact.model_rebuild()
RuntimeEvent.model_rebuild()
RuntimeInvocation.model_rebuild()
RuntimeSession.model_rebuild()
RuntimeExecutionRequest.model_rebuild()
RuntimeTranscript.model_rebuild()
RuntimeExecutionResult.model_rebuild()
