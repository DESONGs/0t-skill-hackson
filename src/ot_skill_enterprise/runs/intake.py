from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from ot_skill_enterprise.agents.models import AgentAdapter
from ot_skill_enterprise.qa.models import EvaluationRecord
from ot_skill_enterprise.runs.models import ArtifactRecord, RunRecord, RunTrace, RuntimeEvent
from ot_skill_enterprise.shared.contracts.common import ServiceError, utc_now
from .pipeline import RunIngestionPipeline, RunPipelineResult


_COMPANION_KEYS = {"run", "record", "trace", "traces", "events", "runtime_events", "artifacts", "evaluation", "feedback", "evolution", "agent"}
_FAILURE_STATUSES = {"failed", "partial", "error"}


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _first(body: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        value = body.get(key)
        if value is not None and value != "":
            return value
    return default


def _payload_run_body(body: Mapping[str, Any]) -> dict[str, Any]:
    run_body: dict[str, Any] = {}
    for key in ("run", "record"):
        nested = body.get(key)
        if isinstance(nested, Mapping):
            run_body.update(dict(nested))
            break
    for key, value in body.items():
        if key in _COMPANION_KEYS or key in {"run", "record"}:
            continue
        if key == "metadata" and isinstance(value, Mapping):
            run_body["metadata"] = _merge_metadata(_mapping(run_body.get("metadata")), value)
        else:
            run_body.setdefault(key, value)
    return run_body


def _nested_value(body: Mapping[str, Any], key: str) -> Any:
    for container_key in ("run", "record"):
        nested = body.get(container_key)
        if isinstance(nested, Mapping) and nested.get(key) is not None:
            return nested.get(key)
    return None


def _stable_payload(value: Any) -> str:
    payload = getattr(value, "model_dump", lambda **_: value)(mode="json") if hasattr(value, "model_dump") else value
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_safe(value: Any) -> Any:
    return json.loads(
        json.dumps(
            value,
            ensure_ascii=False,
            default=lambda item: item.model_dump(mode="json") if hasattr(item, "model_dump") else str(item),
        )
    )


def _hashed_id(prefix: str, value: Any, *, length: int = 12) -> str:
    digest = hashlib.sha256(_stable_payload(value).encode("utf-8")).hexdigest()
    return f"{prefix}-{digest[:length]}"


def _merge_metadata(*parts: Mapping[str, Any] | None) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for part in parts:
        if part:
            merged.update(dict(part))
    return merged


def _event_payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, RuntimeEvent):
        return raw.model_dump(mode="json")
    body = _mapping(raw)
    if body:
        return body
    dumper = getattr(raw, "model_dump", None)
    if dumper is not None:
        payload = dumper(mode="json")
        if isinstance(payload, Mapping):
            return dict(payload)
    return {}


def _raw_runtime_event_entries(body: Mapping[str, Any]) -> list[Any]:
    events = body.get("events") or body.get("runtime_events")
    if isinstance(events, list):
        return list(events)
    run_body = _payload_run_body(body)
    events = run_body.get("events") or run_body.get("runtime_events")
    if isinstance(events, list):
        return list(events)
    traces = body.get("traces")
    if isinstance(traces, list):
        collected: list[Any] = []
        for trace in traces:
            trace_body = _mapping(trace)
            collected.extend(trace_body.get("events") or [])
        return collected
    traces = run_body.get("traces")
    if isinstance(traces, list):
        collected = []
        for trace in traces:
            trace_body = _mapping(trace)
            collected.extend(trace_body.get("events") or [])
        return collected
    trace = body.get("trace")
    if isinstance(trace, Mapping):
        return list(trace.get("events") or [])
    trace = run_body.get("trace")
    if isinstance(trace, Mapping):
        return list(trace.get("events") or [])
    return []


def _normalize_runtime_event(
    raw: Any,
    *,
    run_id: str,
    runtime_session_id: str,
    trace_id: str | None = None,
    index: int = 0,
) -> RuntimeEvent:
    body = _event_payload(raw)
    metadata = _mapping(body.get("metadata"))
    nested_payload = body.get("payload")
    if nested_payload is None:
        nested_payload = body.get("data") or body.get("body") or body.get("details")
    payload: dict[str, Any]
    extra_keys = {
        "event_id",
        "id",
        "eventId",
        "run_id",
        "trace_id",
        "traceId",
        "event_type",
        "eventType",
        "type",
        "kind",
        "name",
        "step_id",
        "stepId",
        "skill_id",
        "skillId",
        "action_id",
        "actionId",
        "subject_kind",
        "subjectKind",
        "subject_id",
        "subjectId",
        "status",
        "summary",
        "artifact_id",
        "artifactId",
        "timestamp",
        "created_at",
        "createdAt",
        "time",
        "payload",
        "data",
        "body",
        "details",
        "metadata",
    }
    extras = {key: value for key, value in body.items() if key not in extra_keys}
    if isinstance(nested_payload, Mapping):
        payload = dict(nested_payload)
        metadata = _merge_metadata(metadata, extras)
    elif nested_payload is not None:
        payload = {"value": nested_payload}
        metadata = _merge_metadata(metadata, extras)
    else:
        payload = extras

    return RuntimeEvent(
        event_id=str(_first(body, "event_id", "id", "eventId", default=f"evt-{run_id}-{index + 1}")),
        run_id=str(_first(body, "run_id", "runId", default=run_id)),
        runtime_session_id=str(_first(body, "runtime_session_id", "runtimeSessionId", default=runtime_session_id)),
        event_type=str(_first(body, "event_type", "eventType", "type", "kind", "name", default="runtime_event")),
        trace_id=str(_first(body, "trace_id", "traceId", default=trace_id)) if _first(body, "trace_id", "traceId", default=trace_id) is not None else None,
        step_id=_first(body, "step_id", "stepId"),
        skill_id=_first(body, "skill_id", "skillId"),
        action_id=_first(body, "action_id", "actionId"),
        subject_kind=_first(body, "subject_kind", "subjectKind"),
        subject_id=_first(body, "subject_id", "subjectId"),
        status=_first(body, "status"),
        summary=_first(body, "summary"),
        artifact_id=_first(body, "artifact_id", "artifactId"),
        timestamp=_first(body, "timestamp", "created_at", "createdAt", "time", default=utc_now()),
        payload=payload,
        metadata=metadata,
    )


def _normalize_runtime_events(body: Mapping[str, Any], *, run_id: str, runtime_session_id: str, traces: list[RunTrace]) -> list[RuntimeEvent]:
    raw_events = _raw_runtime_event_entries(body)
    if not raw_events:
        collected: list[RuntimeEvent] = []
        for trace in traces:
            collected.extend(trace.events)
        return collected

    events: list[RuntimeEvent] = []
    default_trace_id = _first(body, "trace_id", "traceId", default=f"trace-{run_id}")
    for index, raw in enumerate(raw_events):
        event = _normalize_runtime_event(raw, run_id=run_id, runtime_session_id=runtime_session_id, trace_id=default_trace_id, index=index)
        events.append(event)
    return events


def _normalize_artifact(raw: Any, *, run_id: str, runtime_session_id: str, index: int = 0) -> ArtifactRecord:
    body = _event_payload(raw)
    metadata = _mapping(body.get("metadata"))
    extra_keys = {
        "artifact_id",
        "artifactId",
        "id",
        "kind",
        "type",
        "artifact_type",
        "uri",
        "url",
        "href",
        "path",
        "label",
        "name",
        "title",
        "source_step_id",
        "sourceStepId",
        "step_id",
        "stepId",
        "metadata",
    }
    extras = {key: value for key, value in body.items() if key not in extra_keys}
    metadata = _merge_metadata(metadata, extras, {"run_id": run_id, "runtime_session_id": runtime_session_id})
    uri = _first(body, "uri", "url", "href", "path")
    if uri is None and "value" in body:
        uri = str(body["value"])
    return ArtifactRecord(
        artifact_id=str(_first(body, "artifact_id", "artifactId", "id", default=f"artifact-{run_id}-{index + 1}")),
        runtime_session_id=str(_first(body, "runtime_session_id", "runtimeSessionId", default=runtime_session_id)),
        kind=str(_first(body, "kind", "type", "artifact_type", default="artifact")),
        uri=str(uri) if uri is not None else None,
        label=_first(body, "label", "name", "title"),
        source_step_id=_first(body, "source_step_id", "sourceStepId", "step_id", "stepId"),
        metadata=metadata,
    )


def _normalize_artifacts(body: Mapping[str, Any], *, run_id: str, runtime_session_id: str, runtime_events: list[RuntimeEvent]) -> list[ArtifactRecord]:
    run_body = _payload_run_body(body)
    raw_artifacts = body.get("artifacts") or run_body.get("artifacts")
    if isinstance(raw_artifacts, list) and raw_artifacts:
        return [_normalize_artifact(item, run_id=run_id, runtime_session_id=runtime_session_id, index=index) for index, item in enumerate(raw_artifacts)]
    artifacts: list[ArtifactRecord] = []
    for index, event in enumerate(runtime_events):
        if event.artifact_id:
            artifacts.append(
                ArtifactRecord(
                    artifact_id=event.artifact_id,
                    runtime_session_id=event.runtime_session_id,
                    kind=str(event.metadata.get("artifact_kind") or "artifact"),
                    uri=str(event.metadata.get("artifact_uri") or event.metadata.get("uri") or "") or None,
                    label=str(event.metadata.get("artifact_label") or event.metadata.get("label") or event.summary or event.event_type),
                    source_step_id=event.step_id,
                    metadata=_merge_metadata(event.metadata, {"event_id": event.event_id, "event_type": event.event_type, "run_id": run_id, "runtime_session_id": event.runtime_session_id}),
                )
            )
    return artifacts


def _normalize_traces(body: Mapping[str, Any], *, run_id: str, runtime_session_id: str, runtime_events: list[RuntimeEvent]) -> list[RunTrace]:
    run_body = _payload_run_body(body)
    raw_traces = body.get("traces") or run_body.get("traces")
    if isinstance(raw_traces, list) and raw_traces:
        traces: list[RunTrace] = []
        for index, raw_trace in enumerate(raw_traces):
            trace_body = _event_payload(raw_trace)
            trace_id = str(_first(trace_body, "trace_id", "traceId", "id", default=f"trace-{run_id}-{index + 1}"))
            events = [
                _normalize_runtime_event(event, run_id=run_id, runtime_session_id=runtime_session_id, trace_id=trace_id, index=event_index)
                for event_index, event in enumerate(list(trace_body.get("events") or []))
            ]
            traces.append(
                RunTrace(
                    trace_id=trace_id,
                    run_id=str(_first(trace_body, "run_id", "runId", default=run_id)),
                    runtime_session_id=str(_first(trace_body, "runtime_session_id", "runtimeSessionId", default=runtime_session_id)),
                    summary=str(_first(trace_body, "summary", default=events[-1].summary if events and events[-1].summary else "")) or None,
                    events=events,
                    metadata=_merge_metadata(_mapping(trace_body.get("metadata")), {
                        key: value
                        for key, value in trace_body.items()
                        if key
                        not in {
                            "trace_id",
                            "traceId",
                            "id",
                            "run_id",
                            "runId",
                            "events",
                            "metadata",
                        }
                    }),
                )
            )
        return traces

    raw_trace = body.get("trace") or run_body.get("trace")
    if isinstance(raw_trace, Mapping):
        trace_body = _event_payload(raw_trace)
        trace_id = str(_first(trace_body, "trace_id", "traceId", "id", default=f"trace-{run_id}"))
        events = [
            _normalize_runtime_event(event, run_id=run_id, runtime_session_id=runtime_session_id, trace_id=trace_id, index=event_index)
            for event_index, event in enumerate(list(trace_body.get("events") or []))
        ]
        return [
            RunTrace(
                trace_id=trace_id,
                run_id=str(_first(trace_body, "run_id", "runId", default=run_id)),
                runtime_session_id=str(_first(trace_body, "runtime_session_id", "runtimeSessionId", default=runtime_session_id)),
                summary=str(_first(trace_body, "summary", default=events[-1].summary if events and events[-1].summary else "")) or None,
                events=events,
                metadata=_merge_metadata(_mapping(trace_body.get("metadata")), {
                    key: value
                    for key, value in trace_body.items()
                    if key
                    not in {
                        "trace_id",
                        "traceId",
                        "id",
                        "run_id",
                        "runId",
                        "events",
                        "metadata",
                    }
                }),
            )
        ]

    grouped: dict[str, list[RuntimeEvent]] = {}
    for event in runtime_events:
        trace_id = event.trace_id or f"trace-{run_id}"
        grouped.setdefault(trace_id, []).append(event)
    if not grouped:
        grouped[f"trace-{run_id}"] = []
    return [
        RunTrace(
            trace_id=trace_id,
            run_id=run_id,
            runtime_session_id=runtime_session_id,
            summary=events[-1].summary if events and events[-1].summary else None,
            events=events,
            metadata={"event_count": len(events)},
        )
        for trace_id, events in grouped.items()
    ]


def _derive_status(body: Mapping[str, Any], runtime_events: list[RuntimeEvent]) -> str:
    explicit = _first(body, "status", default=None)
    if explicit:
        return str(explicit)
    for event in reversed(runtime_events):
        if event.status:
            return str(event.status)
        if event.event_type in {"run_failed", "step_failed"} or event.event_type.endswith("_failed"):
            return "failed"
        if event.event_type in {"run_succeeded", "step_succeeded"} or event.event_type.endswith("_succeeded"):
            return "succeeded"
    return "succeeded"


def _derive_ok(body: Mapping[str, Any], status: str) -> bool:
    if "ok" in body and body.get("ok") is not None:
        return bool(body.get("ok"))
    return status not in _FAILURE_STATUSES


def _derive_summary(body: Mapping[str, Any], status: str, subject_kind: str, runtime_events: list[RuntimeEvent]) -> str:
    explicit = _first(body, "summary", default=None)
    if explicit:
        return str(explicit)
    for event in reversed(runtime_events):
        if event.summary:
            return str(event.summary)
        payload_summary = _mapping(event.payload).get("summary")
        if payload_summary:
            return str(payload_summary)
    return f"{subject_kind} {status}"


def _derive_subject(body: Mapping[str, Any], *, run_id: str) -> tuple[str, str]:
    metadata = _mapping(body.get("metadata"))
    subject_kind = str(_first(body, "subject_kind", "subjectKind", default=metadata.get("subject_kind") or "run"))
    subject_id = _first(body, "subject_id", "subjectId", default=metadata.get("subject_id") or run_id)
    return subject_kind, str(subject_id)


def _derive_identifiers(body: Mapping[str, Any], runtime_events: list[RuntimeEvent]) -> tuple[str, str, str]:
    subject_kind, subject_id = _derive_subject(body, run_id=str(_first(body, "run_id", "runId", default=_first(body, "request_id", default=uuid4().hex))))
    metadata = _mapping(body.get("metadata"))
    run_id = str(_first(body, "run_id", "runId", "request_id", default=metadata.get("run_id") or metadata.get("request_id") or uuid4().hex))
    agent_id = str(_first(body, "agent_id", "agentId", default=metadata.get("agent_id") or subject_id or f"{subject_kind}-agent"))
    flow_id = str(_first(body, "flow_id", "flowId", "preset_id", "presetId", default=metadata.get("flow_id") or subject_id or run_id))
    return run_id, agent_id, flow_id


def _derive_skill_ids(body: Mapping[str, Any], runtime_events: list[RuntimeEvent], flow_id: str) -> list[str]:
    explicit = body.get("skill_ids")
    if isinstance(explicit, list) and explicit:
        return [str(item) for item in explicit if str(item).strip()]
    values = {event.skill_id for event in runtime_events if event.skill_id}
    if values:
        return sorted({str(item) for item in values if str(item).strip()})
    return [flow_id]


def _derive_provider_ids(body: Mapping[str, Any], runtime_events: list[RuntimeEvent]) -> list[str]:
    explicit = body.get("provider_ids")
    if isinstance(explicit, list) and explicit:
        return [str(item) for item in explicit if str(item).strip()]
    metadata = _mapping(body.get("metadata"))
    values = {str(value) for value in [metadata.get("provider_id"), body.get("provider_id")] if value}
    for event in runtime_events:
        provider_id = _mapping(event.metadata).get("provider_id")
        if provider_id:
            values.add(str(provider_id))
    return sorted(values) if values else ["runtime"]


def _normalize_failure(body: Mapping[str, Any], *, runtime_events: list[RuntimeEvent], status: str) -> ServiceError | None:
    failure = body.get("failure")
    if isinstance(failure, Mapping):
        return ServiceError.model_validate(dict(failure))
    if isinstance(failure, ServiceError):
        return failure
    if status not in _FAILURE_STATUSES:
        return None
    terminal = next(
        (event for event in reversed(runtime_events) if event.status in _FAILURE_STATUSES or event.event_type.endswith("_failed") or event.event_type == "run_failed"),
        None,
    )
    if terminal is None:
        return None
    payload = _mapping(terminal.metadata)
    details = _mapping(terminal.payload)
    message = str(terminal.summary or details.get("message") or details.get("summary") or body.get("summary") or "runtime failure")
    code = str(payload.get("error_code") or details.get("error_code") or "runtime_failure")
    return ServiceError(code=code, message=message, details=_merge_metadata(payload, details))


def _normalize_run(
    body: Mapping[str, Any],
    *,
    runtime_events: list[RuntimeEvent],
    traces: list[RunTrace],
    artifacts: list[ArtifactRecord],
) -> RunRecord:
    body = _payload_run_body(body)
    subject_kind, subject_id = _derive_subject(body, run_id=str(_first(body, "run_id", "runId", "request_id", default=uuid4().hex)))
    run_id, agent_id, flow_id = _derive_identifiers(body, runtime_events)
    base_metadata = _mapping(body.get("metadata"))
    runtime_id = str(_first(body, "runtime_id", "runtimeId", default=base_metadata.get("runtime_id") or "runtime"))
    runtime_session_id = _first(body, "runtime_session_id", "runtimeSessionId", default=base_metadata.get("runtime_session_id"))
    if runtime_session_id is None or str(runtime_session_id).strip() == "":
        raise ValueError("runtime_session_id is required for all runtime runs")
    metadata = _merge_metadata(
        base_metadata,
        {
            key: value
            for key, value in body.items()
            if key
            not in _COMPANION_KEYS
            and key
            not in {
                "run_id",
                "runId",
                "request_id",
                "requestId",
                "subject_kind",
                "subjectKind",
                "subject_id",
                "subjectId",
                "agent_id",
                "agentId",
                "flow_id",
                "flowId",
                "preset_id",
                "presetId",
                "status",
                "ok",
                "summary",
                "input_payload",
                "inputPayload",
                "output_payload",
                "outputPayload",
                "skill_ids",
                "provider_ids",
                "started_at",
                "startedAt",
                "finished_at",
                "finishedAt",
                "failure",
                "metadata",
            }
        },
    )
    status = _derive_status(body, runtime_events)
    summary = _derive_summary(body, status, subject_kind, runtime_events)
    ok = _derive_ok(body, status)
    skill_ids = _derive_skill_ids(body, runtime_events, flow_id)
    provider_ids = _derive_provider_ids(body, runtime_events)
    primary_trace = traces[0] if traces else RunTrace(trace_id=f"trace-{run_id}", run_id=run_id, runtime_session_id=str(runtime_session_id), events=list(runtime_events))
    runtime_event_ids = [event.event_id for event in runtime_events]
    metadata = _merge_metadata(
        metadata,
        {
            "subject_kind": subject_kind,
            "subject_id": subject_id,
            "runtime_event_count": len(runtime_events),
            "trace_count": len(traces),
            "artifact_count": len(artifacts),
            "runtime_event_ids": runtime_event_ids,
            "runtime_trace_ids": [trace.trace_id for trace in traces],
            "runtime_artifact_ids": [artifact.artifact_id for artifact in artifacts],
            "runtime_event_types": [event.event_type for event in runtime_events],
        },
    )
    failure = _normalize_failure(body, runtime_events=runtime_events, status=status)
    started_at = _first(body, "started_at", "startedAt", default=runtime_events[0].timestamp if runtime_events else utc_now())
    finished_at = _first(body, "finished_at", "finishedAt", default=runtime_events[-1].timestamp if runtime_events else utc_now())
    input_payload = _mapping(_first(body, "input_payload", "inputPayload", "inputs", "input", default={}))
    output_payload = _mapping(_first(body, "output_payload", "outputPayload", "output", "result", default={}))
    return RunRecord(
        run_id=run_id,
        runtime_id=runtime_id,
        runtime_session_id=str(runtime_session_id),
        subject_kind=subject_kind,
        subject_id=subject_id,
        agent_id=agent_id,
        flow_id=flow_id,
        status=status,
        ok=ok,
        summary=summary,
        input_payload=input_payload,
        output_payload=output_payload,
        skill_ids=skill_ids,
        provider_ids=provider_ids,
        trace_ids=[trace.trace_id for trace in traces],
        artifact_ids=[artifact.artifact_id for artifact in artifacts],
        event_count=len(runtime_events),
        trace_count=len(traces),
        artifact_count=len(artifacts),
        runtime_events=runtime_events,
        artifacts=artifacts,
        traces=traces if traces else [primary_trace],
        trace=primary_trace,
        failure=failure,
        started_at=started_at,
        finished_at=finished_at,
        metadata=metadata,
    )


def _derive_findings(runtime_events: list[RuntimeEvent], summary: str) -> list[str]:
    findings: list[str] = []
    for event in runtime_events:
        if event.status in _FAILURE_STATUSES or event.event_type.endswith("_failed") or event.event_type == "run_failed":
            text = (event.summary or _mapping(event.payload).get("summary") or summary).strip()
            if text and text not in findings:
                findings.append(text)
    return findings


def _normalize_evaluation(
    body: Mapping[str, Any],
    *,
    run: RunRecord,
    runtime_events: list[RuntimeEvent],
    artifacts: list[ArtifactRecord],
    qa: QAEvaluator,
) -> EvaluationRecord:
    explicit = body.get("evaluation") or _nested_value(body, "evaluation")
    if isinstance(explicit, Mapping):
        explicit_body = dict(explicit)
        trace_ids = [trace.trace_id for trace in run.traces] or [run.trace.trace_id]
        event_ids = [event.event_id for event in runtime_events]
        event_types = [event.event_type for event in runtime_events]
        artifact_ids = [artifact.artifact_id for artifact in artifacts]
        evaluation_id = str(
            _first(
                explicit_body,
                "evaluation_id",
                "id",
                "evaluationId",
                default=_hashed_id(
                    "eval",
                    {
                        "run_id": run.run_id,
                        "summary": _first(explicit_body, "summary", default=run.summary),
                        "event_ids": event_ids,
                        "artifact_ids": artifact_ids,
                    },
                ),
            )
        )
        grade = str(_first(explicit_body, "grade", "status", default="pass" if run.ok else "fail"))
        findings = explicit_body.get("findings")
        if not isinstance(findings, list):
            findings = _derive_findings(runtime_events, str(_first(explicit_body, "summary", default=run.summary)))
        checks = explicit_body.get("checks")
        if not isinstance(checks, list):
            checks = [str(_first(explicit_body, "recommendation", default=""))] if _first(explicit_body, "recommendation", default="") else []
        metadata = _merge_metadata(
            _mapping(explicit_body.get("metadata")),
            {
                "trace_ids": trace_ids,
                "event_ids": event_ids,
                "event_types": event_types,
                "artifact_ids": artifact_ids,
            },
        )
        return EvaluationRecord(
            evaluation_id=evaluation_id,
            run_id=run.run_id,
            runtime_session_id=run.runtime_session_id,
            subject_type=str(_first(explicit_body, "subject_type", "subjectType", default=run.subject_kind)),
            subject_id=str(_first(explicit_body, "subject_id", "subjectId", default=run.subject_id or run.flow_id or run.run_id)),
            grade=grade,
            summary=str(_first(explicit_body, "summary", default=run.summary)),
            trace_ids=trace_ids,
            event_ids=event_ids,
            event_types=event_types,
            artifact_ids=artifact_ids,
            checks=[str(item) for item in checks if str(item).strip()],
            findings=[str(item) for item in findings if str(item).strip()],
            metadata=metadata,
        )

    findings = _derive_findings(runtime_events, run.summary)
    summary = run.summary
    return qa.evaluate_run(
        run,
        subject_kind=run.subject_kind,
        subject_id=run.subject_id or run.flow_id or run.run_id,
        status="pass" if run.ok else "fail",
        summary=summary,
        findings=findings,
        recommendation="Keep runtime events flowing through the shared run/trace/evaluation model.",
        metadata={"source_kind": "runtime", "source_id": run.run_id},
    )


def _normalize_agent(body: Mapping[str, Any], *, run: RunRecord) -> AgentAdapter:
    agent = body.get("agent")
    if agent is None:
        agent = _payload_run_body(body).get("agent")
    if isinstance(agent, Mapping):
        return AgentAdapter.model_validate(dict(agent))
    agent_id = str(_first(body, "agent_id", "agentId", default=run.agent_id))
    display_name = str(_first(body, "agent_name", "agentName", "display_name", "displayName", default=agent_id))
    return AgentAdapter(agent_id=agent_id, display_name=display_name)


class ExternalRunIntakeResult(RunPipelineResult):
    def as_dict(self) -> dict[str, Any]:
        payload = super().as_dict()
        payload["evolution"] = _json_safe(self.evolution) if self.evolution is not None else None
        return payload


def record_external_run(
    payload: Mapping[str, Any],
    *,
    registry_root: Path | str,
) -> ExternalRunIntakeResult:
    runtime_session_id = _first(
        payload,
        "runtime_session_id",
        "runtimeSessionId",
        default=_nested_value(payload, "runtime_session_id") or _mapping(payload.get("metadata")).get("runtime_session_id"),
    )
    if runtime_session_id is None or str(runtime_session_id).strip() == "":
        raise ValueError("runtime_session_id is required when recording an external run")
    result = RunIngestionPipeline(Path(registry_root)).record(payload)
    return ExternalRunIntakeResult(
        run=result.run,
        runtime_events=result.runtime_events,
        traces=result.traces,
        artifacts=result.artifacts,
        evaluation=result.evaluation,
        evolution=result.evolution,
    )
