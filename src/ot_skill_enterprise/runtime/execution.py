from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator

from ot_skill_enterprise.shared.contracts.common import ContractModel, ServiceError, utc_now

from .transcript import RuntimeTranscript


def _normalize_text(value: Any) -> str:
    return str(value).strip()


class RuntimeLaunchSpec(ContractModel):
    runtime_id: str = Field(min_length=1)
    launcher: list[str] = Field(default_factory=list)
    entrypoint: str | None = None
    cwd: str | None = None
    environment: dict[str, str] = Field(default_factory=dict)
    mode: str = Field(default="release", min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("runtime_id", "mode", mode="before")
    @classmethod
    def _normalize_required_text(cls, value: Any) -> str:
        text = _normalize_text(value)
        if not text:
            raise ValueError("launch spec text fields must not be empty")
        return text

    @field_validator("launcher", mode="before")
    @classmethod
    def _normalize_launcher(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, (list, tuple)):
            return [str(item).strip() for item in value if str(item).strip()]
        text = str(value).strip()
        return [text] if text else []


class RuntimeExecutionRequest(ContractModel):
    runtime_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    invocation_id: str = Field(min_length=1)
    workspace_dir: str = Field(min_length=1)
    session_workspace: str = Field(min_length=1)
    cwd: str = Field(min_length=1)
    prompt: str = ""
    input_payload: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    launch_spec: RuntimeLaunchSpec
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("runtime_id", "session_id", "invocation_id", "workspace_dir", "session_workspace", "cwd", mode="before")
    @classmethod
    def _normalize_required_text(cls, value: Any) -> str:
        text = _normalize_text(value)
        if not text:
            raise ValueError("execution request identifiers must not be empty")
        return text

    @property
    def workspace_path(self) -> Path:
        return Path(self.workspace_dir)

    @property
    def session_workspace_path(self) -> Path:
        return Path(self.session_workspace)


class RuntimeExecutionResult(ContractModel):
    runtime_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    invocation_id: str = Field(min_length=1)
    launch_spec: RuntimeLaunchSpec
    command: list[str] = Field(default_factory=list)
    returncode: int = 0
    transcript: RuntimeTranscript
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime = Field(default_factory=utc_now)
    error: ServiceError | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("runtime_id", "session_id", "invocation_id", mode="before")
    @classmethod
    def _normalize_required_text(cls, value: Any) -> str:
        text = _normalize_text(value)
        if not text:
            raise ValueError("execution result identifiers must not be empty")
        return text


RuntimeLaunchSpec.model_rebuild()
RuntimeExecutionRequest.model_rebuild()
RuntimeExecutionResult.model_rebuild()
