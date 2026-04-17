from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable

from .execution import RuntimeExecutionRequest, RuntimeExecutionResult
from .models import RuntimeArtifact, RuntimeDescriptor, RuntimeEvent, RuntimeInvocation, RuntimeSession, RuntimeToolCall
from .transcript import RuntimeTranscript


@runtime_checkable
class RuntimeEventMapper(Protocol):
    def map_event(
        self,
        event: Any,
        *,
        runtime_id: str,
        session_id: str,
        invocation_id: str | None = None,
    ) -> list[RuntimeEvent]: ...


@runtime_checkable
class RuntimeToolBridge(Protocol):
    def register_tool(
        self,
        tool_name: str,
        handler: Any,
        *,
        description: str = "",
        metadata: Mapping[str, Any] | None = None,
    ) -> None: ...

    def describe(self) -> list[dict[str, Any]]: ...

    def dispatch(
        self,
        tool_name: str,
        args: Mapping[str, Any] | None = None,
        *,
        runtime_id: str,
        session_id: str,
        invocation_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> RuntimeToolCall: ...


@runtime_checkable
class RuntimeExecutor(Protocol):
    def execute(self, request: RuntimeExecutionRequest) -> RuntimeExecutionResult: ...


@runtime_checkable
class RuntimeTranslator(Protocol):
    def normalize_transcript(self, payload: Any, *, runtime_id: str, session_id: str, invocation_id: str | None = None, stdout: str = "", stderr: str = "") -> RuntimeTranscript: ...


@runtime_checkable
class RuntimeAdapter(Protocol):
    descriptor: RuntimeDescriptor

    def start_session(
        self,
        *,
        session_id: str | None = None,
        cwd: Path | str | None = None,
        inputs: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> RuntimeSession: ...

    def get_session(self, session_id: str) -> RuntimeSession | None: ...

    def list_sessions(self) -> list[RuntimeSession]: ...

    def start_invocation(
        self,
        session_id: str,
        *,
        kind: str = "turn",
        prompt: str | None = None,
        input_payload: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> RuntimeInvocation: ...

    def record_event(
        self,
        event: Any,
        *,
        session_id: str,
        invocation_id: str | None = None,
    ) -> list[RuntimeEvent]: ...

    def record_tool_call(
        self,
        tool_name: str,
        args: Mapping[str, Any] | None = None,
        *,
        session_id: str,
        invocation_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> RuntimeToolCall: ...

    def record_artifact(
        self,
        artifact: RuntimeArtifact | Mapping[str, Any],
        *,
        session_id: str,
        invocation_id: str | None = None,
    ) -> RuntimeArtifact: ...

    def finish_invocation(
        self,
        session_id: str,
        invocation_id: str,
        *,
        status: str = "succeeded",
        summary: str = "",
        output_payload: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> RuntimeInvocation: ...

    def close_session(
        self,
        session_id: str,
        *,
        status: str = "stopped",
        metadata: Mapping[str, Any] | None = None,
    ) -> RuntimeSession: ...

    def snapshot_session(self, session_id: str) -> RuntimeSession: ...

    def launch_plan(self) -> dict[str, Any]: ...
