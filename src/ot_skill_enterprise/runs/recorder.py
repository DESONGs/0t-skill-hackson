from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from ot_skill_enterprise.shared.contracts import ArtifactRef

from .models import ArtifactRecord, RunRecord, RunTrace, TraceEvent


@dataclass
class RunRecorder:
    """Thin run recorder used by the compat workflow runtime."""

    root: Path | None = None
    traces: dict[str, RunTrace] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.root is not None:
            self.root = Path(self.root).expanduser().resolve()
            self.root.mkdir(parents=True, exist_ok=True)

    def start_trace(self, run_id: str, *, metadata: dict[str, Any] | None = None) -> RunTrace:
        trace = RunTrace(trace_id=f"trace-{run_id}", run_id=run_id, metadata=metadata or {})
        self.traces[run_id] = trace
        return trace

    def record_event(
        self,
        run_id: str,
        *,
        event_type: str,
        step_id: str | None = None,
        skill_id: str | None = None,
        action_id: str | None = None,
        payload: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TraceEvent:
        return self.append_event(
            run_id,
            event_type=event_type,
            step_id=step_id,
            skill_id=skill_id,
            action_id=action_id,
            payload=payload,
            metadata=metadata,
        )

    def append_event(
        self,
        run_id: str,
        *,
        event_type: str,
        step_id: str | None = None,
        skill_id: str | None = None,
        action_id: str | None = None,
        payload: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TraceEvent:
        trace = self.traces.setdefault(run_id, RunTrace(trace_id=f"trace-{run_id}", run_id=run_id))
        event = TraceEvent(
            event_id=f"evt-{uuid4().hex[:10]}",
            run_id=run_id,
            event_type=event_type,
            trace_id=trace.trace_id,
            step_id=step_id,
            skill_id=skill_id,
            action_id=action_id,
            payload=payload or {},
            metadata=metadata or {},
        )
        trace.events.append(event)
        return event

    def record_run(self, run: RunRecord) -> RunRecord:
        stored_trace = self.traces.get(
            run.run_id,
            run.trace if run.trace.run_id != "pending" else RunTrace(trace_id=f"trace-{run.run_id}", run_id=run.run_id),
        )
        if run.traces:
            stored_trace = run.traces[0]
        if not stored_trace.events and run.runtime_events:
            stored_trace.events = list(run.runtime_events)
        if not run.runtime_events and stored_trace.events:
            run.runtime_events = list(stored_trace.events)
        run.trace = stored_trace
        run.traces = [stored_trace, *[trace for trace in run.traces[1:] if trace.trace_id != stored_trace.trace_id]] if run.traces else [stored_trace]
        self.traces[run.run_id] = run.trace
        if self.root is not None:
            path = self.root / f"{run.run_id}.json"
            path.write_text(json.dumps(run.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")
        return run

    @staticmethod
    def artifact_records(artifacts: list[ArtifactRef], *, source_step_id: str | None = None) -> list[ArtifactRecord]:
        return [ArtifactRecord.from_ref(item, source_step_id=source_step_id) for item in artifacts]
