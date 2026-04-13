from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .contracts import RuntimeAdapter
from .models import RuntimeArtifact, RuntimeEvent
from .transcript import RuntimeTranscript


@dataclass(slots=True)
class RuntimeTranslation:
    runtime_events: list[RuntimeEvent]
    artifacts: list[RuntimeArtifact]


@dataclass(slots=True)
class DefaultRuntimeTranslator:
    def normalize_transcript(
        self,
        payload: Any,
        *,
        runtime_id: str,
        session_id: str,
        invocation_id: str | None = None,
        stdout: str = "",
        stderr: str = "",
    ) -> RuntimeTranscript:
        if isinstance(payload, RuntimeTranscript):
            return payload
        if not isinstance(payload, dict):
            raise TypeError("runtime transcript payload must be a mapping or RuntimeTranscript")
        return RuntimeTranscript.from_payload(
            payload,
            runtime_id=runtime_id,
            session_id=session_id,
            invocation_id=invocation_id,
            stdout=stdout,
            stderr=stderr,
        )

    def apply(
        self,
        adapter: RuntimeAdapter,
        *,
        transcript: RuntimeTranscript,
        session_id: str,
        invocation_id: str,
    ) -> RuntimeTranslation:
        runtime_events: list[RuntimeEvent] = []
        artifacts: list[RuntimeArtifact] = []
        for raw_event in transcript.events:
            runtime_events.extend(adapter.record_event(raw_event, session_id=session_id, invocation_id=invocation_id))
        for raw_artifact in transcript.artifacts:
            artifacts.append(adapter.record_artifact(raw_artifact, session_id=session_id, invocation_id=invocation_id))
        return RuntimeTranslation(runtime_events=runtime_events, artifacts=artifacts)
