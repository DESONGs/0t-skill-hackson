from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import Field, model_validator

from ot_skill_enterprise.shared.contracts import ArtifactRef
from ot_skill_enterprise.shared.contracts.common import ContractModel, ServiceError, utc_now


class ArtifactRecord(ContractModel):
    artifact_id: str = Field(min_length=1)
    runtime_session_id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    uri: Optional[str] = None
    label: Optional[str] = None
    source_step_id: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_ref(cls, ref: ArtifactRef, *, runtime_session_id: str, source_step_id: str | None = None) -> "ArtifactRecord":
        return cls(
            artifact_id=ref.artifact_id,
            runtime_session_id=runtime_session_id,
            kind=ref.kind,
            uri=ref.uri,
            label=ref.label,
            source_step_id=source_step_id,
            metadata=dict(ref.metadata),
        )


class RuntimeEvent(ContractModel):
    event_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    runtime_session_id: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    trace_id: Optional[str] = None
    step_id: Optional[str] = None
    skill_id: Optional[str] = None
    action_id: Optional[str] = None
    subject_kind: Optional[str] = None
    subject_id: Optional[str] = None
    status: Optional[str] = None
    summary: Optional[str] = None
    artifact_id: Optional[str] = None
    timestamp: datetime = Field(default_factory=utc_now)
    payload: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunTrace(ContractModel):
    trace_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    runtime_session_id: str = Field(min_length=1)
    summary: Optional[str] = None
    blob_uri: Optional[str] = None
    events: list[RuntimeEvent] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunRecord(ContractModel):
    run_id: str = Field(min_length=1)
    runtime_id: str = Field(min_length=1)
    runtime_session_id: str = Field(min_length=1)
    subject_kind: str = Field(default="run", min_length=1)
    subject_id: Optional[str] = None
    agent_id: str = Field(min_length=1)
    flow_id: str = Field(min_length=1)
    status: str = Field(min_length=1)
    ok: bool = True
    summary: str = Field(min_length=1)
    input_payload: dict[str, Any] = Field(default_factory=dict)
    output_payload: dict[str, Any] = Field(default_factory=dict)
    skill_ids: list[str] = Field(default_factory=list)
    provider_ids: list[str] = Field(default_factory=list)
    trace_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    event_count: int = 0
    trace_count: int = 0
    artifact_count: int = 0
    evaluation_id: str | None = None
    runtime_events: list[RuntimeEvent] = Field(default_factory=list)
    artifacts: list[ArtifactRecord] = Field(default_factory=list)
    traces: list[RunTrace] = Field(default_factory=list)
    trace: RunTrace = Field(default_factory=lambda: RunTrace(trace_id="trace-pending", run_id="pending", runtime_session_id="session-pending"))
    failure: ServiceError | None = None
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _sync_runtime_session_id(self) -> "RunRecord":
        session_id = self.runtime_session_id
        for event in self.runtime_events:
            event.runtime_session_id = session_id
        for trace in self.traces:
            trace.runtime_session_id = session_id
            for event in trace.events:
                event.runtime_session_id = session_id
        for artifact in self.artifacts:
            artifact.runtime_session_id = session_id
        if self.trace is not None:
            self.trace.runtime_session_id = session_id
            for event in self.trace.events:
                event.runtime_session_id = session_id
        return self


TraceEvent = RuntimeEvent
TraceRecord = RunTrace
