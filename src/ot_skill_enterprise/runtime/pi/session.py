from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from ot_skill_enterprise.shared.contracts import ServiceError
from ot_skill_enterprise.shared.contracts.common import utc_now

from ..models import RuntimeArtifact, RuntimeDescriptor, RuntimeEvent, RuntimeInvocation, RuntimeSession, RuntimeToolCall


def _short_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:10]}"


def _normalize_text(value: Any, default: str) -> str:
    text = str(value).strip() if value is not None else ""
    return text or default


def _normalize_error(error: Any | None) -> ServiceError | None:
    if error is None:
        return None
    if isinstance(error, ServiceError):
        return error
    if isinstance(error, Mapping):
        return ServiceError.model_validate(dict(error))
    return ServiceError(code="runtime_error", message=str(error), details={})


@dataclass(slots=True)
class PiRuntimeSession:
    descriptor: RuntimeDescriptor
    session: RuntimeSession
    runtime_root: Path | None = None
    workspace_dir: Path | None = None
    invocations: dict[str, RuntimeInvocation] = field(default_factory=dict)
    tool_calls: dict[str, RuntimeToolCall] = field(default_factory=dict)
    artifacts: dict[str, RuntimeArtifact] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        descriptor: RuntimeDescriptor,
        *,
        session_id: str | None = None,
        cwd: Path | str | None = None,
        inputs: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
        runtime_root: Path | str | None = None,
        workspace_dir: Path | str | None = None,
        parent_session_id: str | None = None,
    ) -> "PiRuntimeSession":
        resolved_session_id = session_id or _short_id("pi-session")
        cwd_text = str(Path(cwd).expanduser().resolve()) if cwd is not None else None
        session = RuntimeSession(
            session_id=resolved_session_id,
            runtime_id=descriptor.runtime_id,
            descriptor=descriptor,
            status="created",
            cwd=cwd_text,
            parent_session_id=parent_session_id,
            input_payload=dict(inputs or {}),
            metadata=dict(metadata or {}),
            started_at=utc_now(),
            updated_at=utc_now(),
        )
        return cls(
            descriptor=descriptor,
            session=session,
            runtime_root=Path(runtime_root).expanduser().resolve() if runtime_root is not None else None,
            workspace_dir=Path(workspace_dir).expanduser().resolve() if workspace_dir is not None else None,
        )

    @property
    def runtime_id(self) -> str:
        return self.descriptor.runtime_id

    @property
    def session_id(self) -> str:
        return self.session.session_id

    def _touch(self) -> None:
        self.session.updated_at = utc_now()

    def _active_invocation_id(self) -> str | None:
        for invocation in reversed(self.session.invocations):
            if invocation.status in {"running", "pending"}:
                return invocation.invocation_id
        return self.session.invocations[-1].invocation_id if self.session.invocations else None

    def start_invocation(
        self,
        *,
        kind: str = "turn",
        prompt: str | None = None,
        input_payload: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> RuntimeInvocation:
        invocation = RuntimeInvocation(
            invocation_id=_short_id("pi-invocation"),
            runtime_id=self.runtime_id,
            session_id=self.session_id,
            kind=_normalize_text(kind, "turn"),
            status="running",
            prompt=prompt,
            input_payload=dict(input_payload or {}),
            metadata=dict(metadata or {}),
            started_at=utc_now(),
        )
        self.session.status = "running"
        self.session.invocations.append(invocation)
        self.invocations[invocation.invocation_id] = invocation
        self._touch()
        return invocation

    def record_event(self, event: RuntimeEvent) -> RuntimeEvent:
        if event.invocation_id is None:
            event.invocation_id = self._active_invocation_id()
        self.session.events.append(event)
        invocation = self.invocations.get(event.invocation_id or "")
        if invocation is not None:
            invocation.events.append(event)
        self._touch()
        return event

    def record_tool_call(self, tool_call: RuntimeToolCall) -> RuntimeToolCall:
        if tool_call.invocation_id is None:
            tool_call.invocation_id = self._active_invocation_id()
        self.tool_calls[tool_call.tool_call_id] = tool_call
        invocation = self.invocations.get(tool_call.invocation_id or "")
        if invocation is not None:
            invocation.tool_calls.append(tool_call)
        self._touch()
        return tool_call

    def finish_tool_call(
        self,
        tool_call_id: str,
        *,
        status: str = "succeeded",
        result: Mapping[str, Any] | None = None,
        error: Any | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> RuntimeToolCall:
        tool_call = self.tool_calls[tool_call_id]
        tool_call.status = _normalize_text(status, "succeeded")
        tool_call.result = dict(result or {}) if result is not None else tool_call.result
        tool_call.error = _normalize_error(error)
        tool_call.finished_at = utc_now()
        if metadata:
            tool_call.metadata.update(dict(metadata))
        self._touch()
        return tool_call

    def record_artifact(self, artifact: RuntimeArtifact) -> RuntimeArtifact:
        if artifact.invocation_id is None:
            artifact.invocation_id = self._active_invocation_id()
        self.artifacts[artifact.artifact_id] = artifact
        self.session.artifacts.append(artifact)
        invocation = self.invocations.get(artifact.invocation_id or "")
        if invocation is not None:
            invocation.artifacts.append(artifact)
        self._touch()
        return artifact

    def finish_invocation(
        self,
        invocation_id: str,
        *,
        status: str = "succeeded",
        ok: bool | None = None,
        summary: str = "",
        output_payload: Mapping[str, Any] | None = None,
        error: Any | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> RuntimeInvocation:
        invocation = self.invocations[invocation_id]
        invocation.status = _normalize_text(status, "succeeded")
        invocation.ok = bool(ok) if ok is not None else invocation.status not in {"failed", "error", "aborted"}
        invocation.summary = summary.strip()
        invocation.output_payload = dict(output_payload or {})
        invocation.error = _normalize_error(error)
        invocation.finished_at = utc_now()
        if metadata:
            invocation.metadata.update(dict(metadata))
        self.session.output_payload.update(invocation.output_payload)
        self.session.status = "running" if invocation.status == "running" else self.session.status
        self._touch()
        return invocation

    def close(self, *, status: str = "stopped", metadata: Mapping[str, Any] | None = None) -> RuntimeSession:
        self.session.status = _normalize_text(status, "stopped")
        self.session.finished_at = utc_now()
        if metadata:
            self.session.metadata.update(dict(metadata))
        self._touch()
        return self.session

    def snapshot(self) -> RuntimeSession:
        return self.session.model_copy(deep=True)

    def summary(self) -> dict[str, Any]:
        return {
            "session": self.session.model_dump(mode="json"),
            "invocation_count": len(self.invocations),
            "event_count": len(self.session.events),
            "tool_call_count": len(self.tool_calls),
            "artifact_count": len(self.artifacts),
        }

    def to_dict(self) -> dict[str, Any]:
        return self.summary()
