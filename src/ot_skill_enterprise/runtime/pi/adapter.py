from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
import os
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from ..contracts import RuntimeAdapter
from ..models import RuntimeArtifact, RuntimeDescriptor, RuntimeEvent, RuntimeInvocation, RuntimeSession, RuntimeToolCall, runtime_model_payload
from .event_mapper import PiEventMapper
from .session import PiRuntimeSession
from .tool_bridge import PiToolBridge


def _short_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:10]}"


def _string(value: Any, default: str) -> str:
    text = str(value).strip() if value is not None else ""
    return text or default


def _newest_source_mtime(source_root: Path | None) -> float:
    if source_root is None or not source_root.exists():
        return 0.0
    newest = 0.0
    try:
        for candidate in source_root.rglob("*.ts"):
            try:
                newest = max(newest, candidate.stat().st_mtime)
            except OSError:
                continue
    except OSError:
        return 0.0
    return newest


@dataclass(slots=True)
class PiRuntimeAdapter(RuntimeAdapter):
    descriptor: RuntimeDescriptor
    runtime_root: Path | None = None
    workspace_dir: Path | None = None
    event_mapper: PiEventMapper = field(default_factory=PiEventMapper)
    tool_bridge: PiToolBridge = field(default_factory=PiToolBridge)

    def __post_init__(self) -> None:
        if self.runtime_root is not None:
            self.runtime_root = Path(self.runtime_root).expanduser().resolve()
        if self.workspace_dir is not None:
            self.workspace_dir = Path(self.workspace_dir).expanduser().resolve()
        if not self.descriptor.supported_actions and self.tool_bridge.describe():
            self.descriptor.supported_actions = [item["tool_name"] for item in self.tool_bridge.describe()]
        if not self.descriptor.tool_surface and self.tool_bridge.describe():
            self.descriptor.tool_surface = [item["tool_name"] for item in self.tool_bridge.describe()]
        self._sessions: dict[str, PiRuntimeSession] = {}

    def launch_plan(self) -> dict[str, Any]:
        node_binary = os.getenv("OT_PI_NODE", "node")
        built_artifact = (self.runtime_root / "dist" / "pi-runtime.mjs").resolve() if self.runtime_root is not None else None
        dev_entrypoint = (self.runtime_root / "upstream/coding_agent/src/ot_runtime_entry.ts").resolve() if self.runtime_root is not None else None
        coding_agent_source = (self.runtime_root / "upstream/coding_agent/src").resolve() if self.runtime_root is not None else None
        built_mode = built_artifact is not None and built_artifact.exists()
        if built_mode and coding_agent_source is not None and coding_agent_source.exists():
            try:
                built_mode = built_artifact.stat().st_mtime >= _newest_source_mtime(coding_agent_source)
            except OSError:
                built_mode = built_artifact.exists()
        source_trees = {
            "coding_agent": str(coding_agent_source) if coding_agent_source is not None else None,
            "agent": str((self.runtime_root / "upstream/agent/src").resolve()) if self.runtime_root is not None else None,
            "ai": str((self.runtime_root / "upstream/ai/src").resolve()) if self.runtime_root is not None else None,
            "tui": str((self.runtime_root / "upstream/tui/src").resolve()) if self.runtime_root is not None else None,
        }
        runtime_launcher = [node_binary, str(built_artifact)] if built_mode and built_artifact is not None else [node_binary, "--import", "tsx", str(dev_entrypoint)]
        return {
            "runtime_id": self.descriptor.runtime_id,
            "runtime_type": self.descriptor.runtime_type,
            "execution_mode": self.descriptor.execution_mode,
            "runtime_root": str(self.runtime_root) if self.runtime_root is not None else None,
            "workspace_dir": str(self.workspace_dir) if self.workspace_dir is not None else None,
            "mode": "release" if built_mode else "dev",
            "entrypoint": str(built_artifact if built_mode and built_artifact is not None else dev_entrypoint) if (built_artifact is not None or dev_entrypoint is not None) else None,
            "source_trees": source_trees,
            "node_entrypoints": {
                "coding_agent": "packages/coding-agent/src/main.ts",
                "agent": "packages/agent/src/agent.ts",
                "ai": "packages/ai/src/index.ts",
                "tui": "packages/tui/src/index.ts",
            },
            "runtime_launcher": runtime_launcher,
            "notes": [
                "managed in-repo Pi runtime interface",
                "default path uses built artifact when available",
                "tsx fallback is dev-only",
            ],
        }

    def _session(self, session_id: str) -> PiRuntimeSession:
        session = self._sessions.get(session_id)
        if session is None:
            raise KeyError(f"no Pi session registered for {session_id!r}")
        return session

    def start_session(
        self,
        *,
        session_id: str | None = None,
        cwd: Path | str | None = None,
        inputs: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> RuntimeSession:
        session = PiRuntimeSession.create(
            self.descriptor,
            session_id=session_id,
            cwd=cwd,
            inputs=inputs,
            metadata=metadata,
            runtime_root=self.runtime_root,
            workspace_dir=self.workspace_dir,
        )
        self._sessions[session.session_id] = session
        return session.snapshot()

    def get_session(self, session_id: str) -> RuntimeSession | None:
        session = self._sessions.get(session_id)
        return None if session is None else session.snapshot()

    def list_sessions(self) -> list[RuntimeSession]:
        return [session.snapshot() for session in self._sessions.values()]

    def start_invocation(
        self,
        session_id: str,
        *,
        kind: str = "turn",
        prompt: str | None = None,
        input_payload: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> RuntimeInvocation:
        session = self._session(session_id)
        invocation = session.start_invocation(kind=kind, prompt=prompt, input_payload=input_payload, metadata=metadata)
        return invocation.model_copy(deep=True)

    def record_event(
        self,
        event: Any,
        *,
        session_id: str,
        invocation_id: str | None = None,
    ) -> list[RuntimeEvent]:
        session = self._session(session_id)
        runtime_events = self.event_mapper.map_event(
            event,
            runtime_id=self.descriptor.runtime_id,
            session_id=session.session_id,
            invocation_id=invocation_id,
        )
        for runtime_event in runtime_events:
            if runtime_event.invocation_id is None and invocation_id is not None:
                runtime_event.invocation_id = invocation_id
            session.record_event(runtime_event)
        return [item.model_copy(deep=True) for item in runtime_events]

    def record_tool_call(
        self,
        tool_name: str,
        args: Mapping[str, Any] | None = None,
        *,
        session_id: str,
        invocation_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> RuntimeToolCall:
        session = self._session(session_id)
        tool_call = self.tool_bridge.dispatch(
            tool_name,
            args,
            runtime_id=self.descriptor.runtime_id,
            session_id=session.session_id,
            invocation_id=invocation_id,
            metadata=metadata,
        )
        session.record_tool_call(tool_call)
        session.record_event(
            RuntimeEvent(
                event_id=_short_id("evt"),
                runtime_id=self.descriptor.runtime_id,
                session_id=session.session_id,
                invocation_id=invocation_id,
                event_type="runtime.tool_call_recorded",
                category="tool",
                source="pi",
                payload=tool_call.model_dump(mode="json"),
                tool_call=tool_call,
                metadata=dict(metadata or {}),
            )
        )
        return tool_call.model_copy(deep=True)

    def record_artifact(
        self,
        artifact: RuntimeArtifact | Mapping[str, Any],
        *,
        session_id: str,
        invocation_id: str | None = None,
    ) -> RuntimeArtifact:
        session = self._session(session_id)
        if isinstance(artifact, RuntimeArtifact):
            runtime_artifact = artifact
        else:
            payload = runtime_model_payload(artifact)
            runtime_artifact = RuntimeArtifact.model_validate(
                {
                    "artifact_id": _string(payload.get("artifact_id"), _short_id("artifact")),
                    "runtime_id": self.descriptor.runtime_id,
                    "session_id": session.session_id,
                    "invocation_id": invocation_id,
                    "tool_call_id": payload.get("tool_call_id"),
                    "kind": _string(payload.get("kind"), "artifact"),
                    "uri": payload.get("uri"),
                    "label": payload.get("label"),
                    "content_type": payload.get("content_type"),
                    "payload": dict(payload.get("payload") or {}),
                    "checksum": payload.get("checksum"),
                    "metadata": dict(payload.get("metadata") or {}),
                }
            )
        session.record_artifact(runtime_artifact)
        session.record_event(
            RuntimeEvent(
                event_id=_short_id("evt"),
                runtime_id=self.descriptor.runtime_id,
                session_id=session.session_id,
                invocation_id=invocation_id,
                event_type="runtime.artifact_recorded",
                category="artifact",
                source="pi",
                payload=runtime_artifact.model_dump(mode="json"),
                artifact=runtime_artifact,
            )
        )
        return runtime_artifact.model_copy(deep=True)

    def finish_invocation(
        self,
        session_id: str,
        invocation_id: str,
        *,
        status: str = "succeeded",
        summary: str = "",
        output_payload: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> RuntimeInvocation:
        session = self._session(session_id)
        invocation = session.finish_invocation(
            invocation_id,
            status=status,
            summary=summary,
            output_payload=output_payload,
            metadata=metadata,
        )
        session.record_event(
            RuntimeEvent(
                event_id=_short_id("evt"),
                runtime_id=self.descriptor.runtime_id,
                session_id=session.session_id,
                invocation_id=invocation_id,
                event_type="runtime.invocation_finished",
                category="invocation",
                source="pi",
                payload=invocation.model_dump(mode="json"),
                metadata=dict(metadata or {}),
            )
        )
        return invocation.model_copy(deep=True)

    def close_session(
        self,
        session_id: str,
        *,
        status: str = "stopped",
        metadata: Mapping[str, Any] | None = None,
    ) -> RuntimeSession:
        session = self._session(session_id)
        closed = session.close(status=status, metadata=metadata)
        session.record_event(
            RuntimeEvent(
                event_id=_short_id("evt"),
                runtime_id=self.descriptor.runtime_id,
                session_id=session.session_id,
                event_type="runtime.session_finished",
                category="lifecycle",
                source="pi",
                payload=closed.model_dump(mode="json"),
                metadata=dict(metadata or {}),
            )
        )
        return closed.model_copy(deep=True)

    def snapshot_session(self, session_id: str) -> RuntimeSession:
        return self._session(session_id).snapshot()


def build_pi_runtime_adapter(
    *,
    runtime_root: Path | str | None = None,
    workspace_dir: Path | str | None = None,
    descriptor: RuntimeDescriptor | None = None,
    tool_bridge: PiToolBridge | None = None,
    event_mapper: PiEventMapper | None = None,
) -> PiRuntimeAdapter:
    runtime_root_path = Path(runtime_root).expanduser().resolve() if runtime_root is not None else None
    workspace_path = Path(workspace_dir).expanduser().resolve() if workspace_dir is not None else None
    resolved_descriptor = descriptor or RuntimeDescriptor(
        runtime_id="pi",
        name="Pi",
        runtime_type="pi",
        version="0.66.1",
        execution_mode="embedded",
        source_root=str(runtime_root_path) if runtime_root_path is not None else None,
        bundle_root=str(runtime_root_path) if runtime_root_path is not None else None,
        entrypoint=str((runtime_root_path / "dist" / "pi-runtime.mjs").resolve()) if runtime_root_path is not None else None,
        metadata={
            "source_layout": {
                "coding_agent": "packages/coding-agent/src",
                "agent": "packages/agent/src",
                "ai": "packages/ai/src",
                "tui": "packages/tui/src",
            },
            "node_runtime": "node >=20.6.0",
            "package_manager": "npm/pnpm compatible node/ts workspace",
        },
    )
    bridge = tool_bridge or PiToolBridge()
    mapper = event_mapper or PiEventMapper()
    return PiRuntimeAdapter(
        descriptor=resolved_descriptor,
        runtime_root=runtime_root_path,
        workspace_dir=workspace_path,
        event_mapper=mapper,
        tool_bridge=bridge,
    )
