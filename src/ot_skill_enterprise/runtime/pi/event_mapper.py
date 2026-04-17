from __future__ import annotations

from typing import Any, Mapping
from uuid import uuid4

from ..models import RuntimeArtifact, RuntimeEvent, RuntimeToolCall, runtime_model_payload


_AGENT_EVENT_MAP = {
    "agent_start": ("runtime.session_started", "lifecycle"),
    "agent_end": ("runtime.session_finished", "lifecycle"),
    "turn_start": ("runtime.invocation_started", "invocation"),
    "turn_end": ("runtime.invocation_finished", "invocation"),
    "message_start": ("runtime.message_started", "message"),
    "message_update": ("runtime.message_updated", "message"),
    "message_end": ("runtime.message_finished", "message"),
    "tool_execution_start": ("runtime.tool_call_started", "tool"),
    "tool_execution_update": ("runtime.tool_call_updated", "tool"),
    "tool_execution_end": ("runtime.tool_call_finished", "tool"),
}

_PROXY_EVENT_MAP = {
    "start": ("runtime.message_started", "message"),
    "text_start": ("runtime.message_text_started", "message"),
    "text_delta": ("runtime.message_text_delta", "message"),
    "text_end": ("runtime.message_text_finished", "message"),
    "thinking_start": ("runtime.thinking_started", "thinking"),
    "thinking_delta": ("runtime.thinking_delta", "thinking"),
    "thinking_end": ("runtime.thinking_finished", "thinking"),
    "toolcall_start": ("runtime.tool_call_started", "tool"),
    "toolcall_delta": ("runtime.tool_call_updated", "tool"),
    "toolcall_end": ("runtime.tool_call_finished", "tool"),
    "done": ("runtime.session_finished", "lifecycle"),
    "error": ("runtime.session_error", "error"),
}


def _short_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:10]}"


def _mapping(event: Any) -> dict[str, Any]:
    payload = runtime_model_payload(event)
    return payload if isinstance(payload, dict) else {"value": payload}


def _string(value: Any, default: str) -> str:
    text = str(value).strip() if value is not None else ""
    return text or default


def _build_runtime_event(
    *,
    runtime_id: str,
    session_id: str,
    invocation_id: str | None,
    event_type: str,
    category: str,
    source: str,
    payload: Mapping[str, Any],
    metadata: Mapping[str, Any] | None = None,
    message: str | None = None,
    tool_call: RuntimeToolCall | None = None,
    artifact: RuntimeArtifact | None = None,
) -> RuntimeEvent:
    return RuntimeEvent(
        event_id=_short_id("evt"),
        runtime_id=runtime_id,
        session_id=session_id,
        invocation_id=invocation_id,
        event_type=event_type,
        category=category,
        source=source,
        message=message,
        payload=dict(payload),
        tool_call=tool_call,
        artifact=artifact,
        metadata=dict(metadata or {}),
    )


class PiEventMapper:
    def map_event(
        self,
        event: Any,
        *,
        runtime_id: str,
        session_id: str,
        invocation_id: str | None = None,
    ) -> list[RuntimeEvent]:
        if isinstance(event, RuntimeEvent):
            return [event]
        if isinstance(event, RuntimeToolCall):
            return [
                _build_runtime_event(
                    runtime_id=runtime_id,
                    session_id=session_id,
                    invocation_id=event.invocation_id or invocation_id,
                    event_type="runtime.tool_call_recorded",
                    category="tool",
                    source="pi",
                    payload=event.model_dump(mode="json"),
                    tool_call=event,
                )
            ]
        if isinstance(event, RuntimeArtifact):
            return [
                _build_runtime_event(
                    runtime_id=runtime_id,
                    session_id=session_id,
                    invocation_id=event.invocation_id or invocation_id,
                    event_type="runtime.artifact_recorded",
                    category="artifact",
                    source="pi",
                    payload=event.model_dump(mode="json"),
                    artifact=event,
                )
            ]

        payload = _mapping(event)
        raw_type = _string(payload.get("type") or payload.get("event_type") or payload.get("eventType"), "runtime.event")
        event_type, category = _AGENT_EVENT_MAP.get(raw_type, _PROXY_EVENT_MAP.get(raw_type, (f"runtime.{raw_type}", "runtime")))
        message: str | None = None
        if isinstance(payload.get("message"), str):
            message = payload["message"].strip() or None
        elif isinstance(payload.get("text"), str):
            message = payload["text"].strip() or None

        metadata: dict[str, Any] = {}
        if isinstance(payload.get("metadata"), dict):
            metadata.update(payload["metadata"])
        if isinstance(payload.get("partial"), dict):
            metadata.setdefault("partial", payload["partial"])

        tool_call: RuntimeToolCall | None = None
        if raw_type in {"tool_execution_start", "tool_execution_update", "tool_execution_end", "toolcall_start", "toolcall_delta", "toolcall_end"}:
            tool_call = RuntimeToolCall(
                tool_call_id=_string(payload.get("toolCallId") or payload.get("tool_call_id") or payload.get("id"), _short_id("toolcall")),
                runtime_id=runtime_id,
                session_id=session_id,
                invocation_id=invocation_id,
                tool_name=_string(payload.get("toolName") or payload.get("tool_name"), "tool"),
                args=dict(payload.get("args") or payload.get("arguments") or {}),
                status=_string(payload.get("status") or payload.get("state"), "running"),
                result=payload.get("result") if isinstance(payload.get("result"), dict) else None,
                metadata=metadata,
            )

        artifact: RuntimeArtifact | None = None
        if isinstance(payload.get("artifact"), dict):
            artifact = RuntimeArtifact.model_validate(
                {
                    "artifact_id": _string(payload["artifact"].get("artifact_id"), _short_id("artifact")),
                    "runtime_id": runtime_id,
                    "session_id": session_id,
                    "invocation_id": invocation_id,
                    "kind": _string(payload["artifact"].get("kind"), "artifact"),
                    "uri": payload["artifact"].get("uri"),
                    "label": payload["artifact"].get("label"),
                    "content_type": payload["artifact"].get("content_type"),
                    "payload": dict(payload["artifact"].get("payload") or {}),
                    "metadata": dict(payload["artifact"].get("metadata") or {}),
                }
            )

        return [
            _build_runtime_event(
                runtime_id=runtime_id,
                session_id=session_id,
                invocation_id=invocation_id,
                event_type=event_type,
                category=category,
                source="pi",
                payload=payload,
                metadata=metadata,
                message=message,
                tool_call=tool_call,
                artifact=artifact,
            )
        ]

