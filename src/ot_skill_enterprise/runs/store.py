from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol

from ot_skill_enterprise.storage import build_blob_store, build_postgres_support, build_storage_settings

from .models import ArtifactRecord, RunRecord, RunTrace, TraceRecord


def _dump_model(value: Any) -> Any:
    dumper = getattr(value, "model_dump", None)
    if dumper is not None:
        return dumper(mode="json")
    return value


def _json_payload(value: Any) -> dict[str, Any]:
    payload = _dump_model(value)
    if isinstance(payload, Mapping):
        return dict(payload)
    raise TypeError("run records must be mappings or pydantic models")


def _json_value(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return default
    return value


def _run_summary_payload(run: RunRecord) -> dict[str, Any]:
    trace_ids = [trace.trace_id for trace in run.traces] or list(run.trace_ids)
    artifact_ids = [artifact.artifact_id for artifact in run.artifacts] or list(run.artifact_ids)
    event_count = len(run.runtime_events) or sum(len(trace.events) for trace in run.traces) or run.event_count
    trace_count = len(run.traces) or len(trace_ids) or run.trace_count
    artifact_count = len(run.artifacts) or len(artifact_ids) or run.artifact_count
    return {
        "run_id": run.run_id,
        "runtime_id": run.runtime_id,
        "runtime_session_id": run.runtime_session_id,
        "subject_kind": run.subject_kind,
        "subject_id": run.subject_id,
        "agent_id": run.agent_id,
        "flow_id": run.flow_id,
        "status": run.status,
        "ok": run.ok,
        "summary": run.summary,
        "input_payload": dict(run.input_payload),
        "output_payload": dict(run.output_payload),
        "skill_ids": list(run.skill_ids),
        "provider_ids": list(run.provider_ids),
        "trace_ids": trace_ids,
        "artifact_ids": artifact_ids,
        "event_count": event_count,
        "trace_count": trace_count,
        "artifact_count": artifact_count,
        "evaluation_id": run.evaluation_id,
        "failure": run.failure.model_dump(mode="json") if run.failure is not None else None,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "metadata": dict(run.metadata),
    }


class RunRepository(Protocol):
    def record_run(self, run: RunRecord | Mapping[str, Any]) -> RunRecord: ...

    def record_trace(self, trace: TraceRecord | Mapping[str, Any]) -> TraceRecord: ...

    def record_artifact(self, artifact: ArtifactRecord | Mapping[str, Any]) -> ArtifactRecord: ...

    def get_run(self, run_id: str) -> RunRecord | None: ...

    def list_runs(self) -> list[RunRecord]: ...

    def load_runs_from_disk(self) -> list[RunRecord]: ...

    def load_traces_from_disk(self) -> list[TraceRecord]: ...

    def load_artifacts_from_disk(self) -> list[ArtifactRecord]: ...

    def run_path(self, run_id: str) -> Path | None: ...

    def trace_path(self, trace_id: str) -> Path | None: ...

    def artifact_path(self, artifact_id: str) -> Path | None: ...


@dataclass
class LocalFileRunRepository:
    root: Path | None = None
    runs: dict[str, dict[str, Any]] = field(default_factory=dict)
    traces: dict[str, dict[str, Any]] = field(default_factory=dict)
    artifacts: dict[str, dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.root is not None:
            self.root = Path(self.root).expanduser().resolve()
            self.root.mkdir(parents=True, exist_ok=True)

    def _category_dir(self, category: str) -> Path | None:
        if self.root is None:
            return None
        path = self.root / category
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _persist(self, category: str, record_id: str, payload: dict[str, Any]) -> None:
        directory = self._category_dir(category)
        if directory is None:
            return
        (directory / f"{record_id}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    def record_run(self, run: RunRecord | Mapping[str, Any]) -> RunRecord:
        normalized = run if isinstance(run, RunRecord) else RunRecord.model_validate(dict(run))
        traces = list(normalized.traces)
        if normalized.trace.run_id != "pending":
            traces = [normalized.trace, *[trace for trace in traces if trace.trace_id != normalized.trace.trace_id]]
        elif traces:
            normalized.trace = traces[0]
        else:
            traces = [RunTrace(trace_id=f"trace-{normalized.run_id}", run_id=normalized.run_id, runtime_session_id=normalized.runtime_session_id)]
        if normalized.runtime_events and not any(trace.events for trace in traces):
            traces[0].events = list(normalized.runtime_events)
        if not normalized.runtime_events:
            normalized.runtime_events = [event for trace in traces for event in trace.events]
        normalized.traces = traces
        normalized.trace_ids = [trace.trace_id for trace in traces]
        normalized.artifact_ids = [artifact.artifact_id for artifact in normalized.artifacts]
        normalized.event_count = len(normalized.runtime_events)
        normalized.trace_count = len(traces)
        normalized.artifact_count = len(normalized.artifacts)
        payload = _run_summary_payload(normalized)
        self.runs[normalized.run_id] = _json_payload(payload)
        self._persist("runs", normalized.run_id, payload)
        return normalized

    def record_trace(self, trace: TraceRecord | Mapping[str, Any]) -> TraceRecord:
        normalized = trace if isinstance(trace, TraceRecord) else TraceRecord.model_validate(dict(trace))
        payload = normalized.model_dump(mode="json")
        self.traces[normalized.trace_id] = payload
        self._persist("traces", normalized.trace_id, payload)
        return normalized

    def record_artifact(self, artifact: ArtifactRecord | Mapping[str, Any]) -> ArtifactRecord:
        normalized = artifact if isinstance(artifact, ArtifactRecord) else ArtifactRecord.model_validate(dict(artifact))
        payload = normalized.model_dump(mode="json")
        self.artifacts[normalized.artifact_id] = payload
        self._persist("artifacts", normalized.artifact_id, payload)
        return normalized

    def get_run(self, run_id: str) -> RunRecord | None:
        payload = self.runs.get(run_id)
        if payload is None:
            return None
        return RunRecord.model_validate(payload)

    def list_runs(self) -> list[RunRecord]:
        return [RunRecord.model_validate(payload) for payload in self.runs.values()]

    def load_runs_from_disk(self) -> list[RunRecord]:
        directory = self._category_dir("runs")
        if directory is None:
            return []
        loaded: list[RunRecord] = []
        for path in sorted(directory.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                record = RunRecord.model_validate(payload)
            except Exception:
                continue
            self.runs[record.run_id] = record.model_dump(mode="json")
            loaded.append(record)
        return loaded

    def load_traces_from_disk(self) -> list[TraceRecord]:
        directory = self._category_dir("traces")
        if directory is None:
            return []
        loaded: list[TraceRecord] = []
        for path in sorted(directory.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                record = TraceRecord.model_validate(payload)
            except Exception:
                continue
            self.traces[record.trace_id] = record.model_dump(mode="json")
            loaded.append(record)
        return loaded

    def load_artifacts_from_disk(self) -> list[ArtifactRecord]:
        directory = self._category_dir("artifacts")
        if directory is None:
            return []
        loaded: list[ArtifactRecord] = []
        for path in sorted(directory.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                record = ArtifactRecord.model_validate(payload)
            except Exception:
                continue
            self.artifacts[record.artifact_id] = record.model_dump(mode="json")
            loaded.append(record)
        return loaded

    def run_path(self, run_id: str) -> Path | None:
        directory = self._category_dir("runs")
        return None if directory is None else directory / f"{run_id}.json"

    def trace_path(self, trace_id: str) -> Path | None:
        directory = self._category_dir("traces")
        return None if directory is None else directory / f"{trace_id}.json"

    def artifact_path(self, artifact_id: str) -> Path | None:
        directory = self._category_dir("artifacts")
        return None if directory is None else directory / f"{artifact_id}.json"


@dataclass
class PostgresRunRepository:
    root: Path | None = None

    def __post_init__(self) -> None:
        self.root = Path(self.root).expanduser().resolve() if self.root is not None else None
        self._settings = build_storage_settings(workspace_root=self.root)
        self._postgres = build_postgres_support(settings=self._settings)
        self._blob = build_blob_store(settings=self._settings)
        self._postgres.ensure_schema()

    def record_run(self, run: RunRecord | Mapping[str, Any]) -> RunRecord:
        normalized = run if isinstance(run, RunRecord) else RunRecord.model_validate(dict(run))
        payload = _run_summary_payload(normalized)
        self._postgres.execute(
            """
            INSERT INTO runs (
                run_id, runtime_session_id, runtime_id, agent_id, flow_id, status, ok, summary,
                input_payload_json, output_payload_json, skill_ids_json, provider_ids_json,
                trace_ids_json, artifact_ids_json, event_count, trace_count, artifact_count,
                evaluation_id, failure_json, started_at, finished_at, metadata_json
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s,
                %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb,
                %s::jsonb, %s::jsonb, %s, %s, %s,
                %s, %s::jsonb, %s, %s, %s::jsonb
            )
            ON CONFLICT (run_id) DO UPDATE SET
                runtime_session_id = EXCLUDED.runtime_session_id,
                runtime_id = EXCLUDED.runtime_id,
                agent_id = EXCLUDED.agent_id,
                flow_id = EXCLUDED.flow_id,
                status = EXCLUDED.status,
                ok = EXCLUDED.ok,
                summary = EXCLUDED.summary,
                input_payload_json = EXCLUDED.input_payload_json,
                output_payload_json = EXCLUDED.output_payload_json,
                skill_ids_json = EXCLUDED.skill_ids_json,
                provider_ids_json = EXCLUDED.provider_ids_json,
                trace_ids_json = EXCLUDED.trace_ids_json,
                artifact_ids_json = EXCLUDED.artifact_ids_json,
                event_count = EXCLUDED.event_count,
                trace_count = EXCLUDED.trace_count,
                artifact_count = EXCLUDED.artifact_count,
                evaluation_id = EXCLUDED.evaluation_id,
                failure_json = EXCLUDED.failure_json,
                started_at = EXCLUDED.started_at,
                finished_at = EXCLUDED.finished_at,
                metadata_json = EXCLUDED.metadata_json
            """,
            (
                payload["run_id"],
                payload["runtime_session_id"],
                payload["runtime_id"],
                payload["agent_id"],
                payload["flow_id"],
                payload["status"],
                payload["ok"],
                payload["summary"],
                self._postgres.dumps_json(payload["input_payload"]),
                self._postgres.dumps_json(payload["output_payload"]),
                self._postgres.dumps_json(payload["skill_ids"]),
                self._postgres.dumps_json(payload["provider_ids"]),
                self._postgres.dumps_json(payload["trace_ids"]),
                self._postgres.dumps_json(payload["artifact_ids"]),
                payload["event_count"],
                payload["trace_count"],
                payload["artifact_count"],
                payload["evaluation_id"],
                self._postgres.dumps_json(payload["failure"]),
                payload["started_at"],
                payload["finished_at"],
                self._postgres.dumps_json(payload["metadata"]),
            ),
        )
        return normalized

    def record_trace(self, trace: TraceRecord | Mapping[str, Any]) -> TraceRecord:
        normalized = trace if isinstance(trace, TraceRecord) else TraceRecord.model_validate(dict(trace))
        trace_payload = normalized.model_dump(mode="json")
        blob = self._blob.put_json(f"traces/{normalized.trace_id}.json", trace_payload)
        summary = normalized.summary or (normalized.events[-1].summary if normalized.events else None)
        metadata = dict(normalized.metadata)
        metadata.update({"blob_uri": blob.uri, "checksum": blob.checksum, "size_bytes": blob.size_bytes})
        self._postgres.execute(
            """
            INSERT INTO run_traces (trace_id, run_id, runtime_session_id, summary, blob_uri, metadata_json)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (trace_id) DO UPDATE SET
                run_id = EXCLUDED.run_id,
                runtime_session_id = EXCLUDED.runtime_session_id,
                summary = EXCLUDED.summary,
                blob_uri = EXCLUDED.blob_uri,
                metadata_json = EXCLUDED.metadata_json
            """,
            (
                normalized.trace_id,
                normalized.run_id,
                normalized.runtime_session_id,
                summary,
                blob.uri,
                self._postgres.dumps_json(metadata),
            ),
        )
        normalized.blob_uri = blob.uri
        normalized.summary = summary
        normalized.metadata = metadata
        return normalized

    def record_artifact(self, artifact: ArtifactRecord | Mapping[str, Any]) -> ArtifactRecord:
        normalized = artifact if isinstance(artifact, ArtifactRecord) else ArtifactRecord.model_validate(dict(artifact))
        metadata = dict(normalized.metadata)
        content_type = metadata.get("content_type")
        size_bytes = metadata.get("size_bytes")
        checksum = metadata.get("checksum")
        self._postgres.execute(
            """
            INSERT INTO artifacts (
                artifact_id, run_id, runtime_session_id, kind, label, uri, content_type, size_bytes, checksum, source_step_id, metadata_json
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (artifact_id) DO UPDATE SET
                run_id = EXCLUDED.run_id,
                runtime_session_id = EXCLUDED.runtime_session_id,
                kind = EXCLUDED.kind,
                label = EXCLUDED.label,
                uri = EXCLUDED.uri,
                content_type = EXCLUDED.content_type,
                size_bytes = EXCLUDED.size_bytes,
                checksum = EXCLUDED.checksum,
                source_step_id = EXCLUDED.source_step_id,
                metadata_json = EXCLUDED.metadata_json
            """,
            (
                normalized.artifact_id,
                metadata.get("run_id"),
                normalized.runtime_session_id,
                normalized.kind,
                normalized.label,
                normalized.uri,
                content_type,
                size_bytes,
                checksum,
                normalized.source_step_id,
                self._postgres.dumps_json(metadata),
            ),
        )
        return normalized

    def _load_trace(self, row: dict[str, Any]) -> TraceRecord:
        blob_uri = row.get("blob_uri")
        if blob_uri:
            payload = json.loads(self._blob.read_bytes(blob_uri).decode("utf-8"))
            trace = TraceRecord.model_validate(payload)
            trace.blob_uri = blob_uri
            trace.summary = row.get("summary")
            trace.metadata = dict(row.get("metadata_json") or {})
            return trace
        return TraceRecord(
            trace_id=row["trace_id"],
            run_id=row["run_id"],
            runtime_session_id=row["runtime_session_id"],
            summary=row.get("summary"),
            blob_uri=blob_uri,
            metadata=row.get("metadata_json") or {},
        )

    def _load_artifact(self, row: dict[str, Any]) -> ArtifactRecord:
        metadata = dict(row.get("metadata_json") or {})
        if row.get("run_id"):
            metadata.setdefault("run_id", row["run_id"])
        if row.get("content_type"):
            metadata.setdefault("content_type", row["content_type"])
        if row.get("size_bytes") is not None:
            metadata.setdefault("size_bytes", row["size_bytes"])
        if row.get("checksum"):
            metadata.setdefault("checksum", row["checksum"])
        return ArtifactRecord(
            artifact_id=row["artifact_id"],
            runtime_session_id=row["runtime_session_id"],
            kind=row["kind"],
            uri=row.get("uri"),
            label=row.get("label"),
            source_step_id=row.get("source_step_id"),
            metadata=metadata,
        )

    def get_run(self, run_id: str) -> RunRecord | None:
        row = self._postgres.fetch_one("SELECT * FROM runs WHERE run_id = %s", (run_id,))
        if row is None:
            return None
        trace_rows = self._postgres.fetch_all("SELECT * FROM run_traces WHERE run_id = %s ORDER BY trace_id", (run_id,))
        artifact_rows = self._postgres.fetch_all("SELECT * FROM artifacts WHERE run_id = %s ORDER BY artifact_id", (run_id,))
        traces = [self._load_trace(item) for item in trace_rows]
        artifacts = [self._load_artifact(item) for item in artifact_rows]
        return RunRecord.model_validate(
            {
                "run_id": row["run_id"],
                "runtime_id": row["runtime_id"],
                "runtime_session_id": row["runtime_session_id"],
                "subject_kind": row["subject_kind"],
                "subject_id": row["subject_id"],
                "agent_id": row["agent_id"],
                "flow_id": row["flow_id"],
                "status": row["status"],
                "ok": row["ok"],
                "summary": row["summary"],
                "input_payload": _json_value(row.get("input_payload_json"), {}),
                "output_payload": _json_value(row.get("output_payload_json"), {}),
                "skill_ids": _json_value(row.get("skill_ids_json"), []),
                "provider_ids": _json_value(row.get("provider_ids_json"), []),
                "trace_ids": _json_value(row.get("trace_ids_json"), []),
                "artifact_ids": _json_value(row.get("artifact_ids_json"), []),
                "event_count": row.get("event_count") or 0,
                "trace_count": row.get("trace_count") or 0,
                "artifact_count": row.get("artifact_count") or 0,
                "evaluation_id": row.get("evaluation_id"),
                "failure": row.get("failure_json"),
                "started_at": row.get("started_at"),
                "finished_at": row.get("finished_at"),
                "metadata": _json_value(row.get("metadata_json"), {}),
                "traces": [trace.model_dump(mode="json") for trace in traces],
                "trace": traces[0].model_dump(mode="json") if traces else {"trace_id": f"trace-{row['run_id']}", "run_id": row["run_id"], "runtime_session_id": row["runtime_session_id"]},
                "artifacts": [artifact.model_dump(mode="json") for artifact in artifacts],
                "runtime_events": [event.model_dump(mode="json") for trace in traces for event in trace.events],
            }
        )

    def list_runs(self) -> list[RunRecord]:
        rows = self._postgres.fetch_all("SELECT * FROM runs ORDER BY finished_at DESC NULLS LAST, started_at DESC NULLS LAST")
        return [RunRecord.model_validate(
            {
                "run_id": row["run_id"],
                "runtime_id": row["runtime_id"],
                "runtime_session_id": row["runtime_session_id"],
                "subject_kind": row["subject_kind"],
                "subject_id": row["subject_id"],
                "agent_id": row["agent_id"],
                "flow_id": row["flow_id"],
                "status": row["status"],
                "ok": row["ok"],
                "summary": row["summary"],
                "input_payload": _json_value(row.get("input_payload_json"), {}),
                "output_payload": _json_value(row.get("output_payload_json"), {}),
                "skill_ids": _json_value(row.get("skill_ids_json"), []),
                "provider_ids": _json_value(row.get("provider_ids_json"), []),
                "trace_ids": _json_value(row.get("trace_ids_json"), []),
                "artifact_ids": _json_value(row.get("artifact_ids_json"), []),
                "event_count": row.get("event_count") or 0,
                "trace_count": row.get("trace_count") or 0,
                "artifact_count": row.get("artifact_count") or 0,
                "evaluation_id": row.get("evaluation_id"),
                "failure": row.get("failure_json"),
                "started_at": row.get("started_at"),
                "finished_at": row.get("finished_at"),
                "metadata": _json_value(row.get("metadata_json"), {}),
                "trace": {"trace_id": (_json_value(row.get("trace_ids_json"), [f"trace-{row['run_id']}"]) or [f"trace-{row['run_id']}"])[0], "run_id": row["run_id"], "runtime_session_id": row["runtime_session_id"]},
            }
        ) for row in rows]

    def load_runs_from_disk(self) -> list[RunRecord]:
        return self.list_runs()

    def load_traces_from_disk(self) -> list[TraceRecord]:
        rows = self._postgres.fetch_all("SELECT * FROM run_traces ORDER BY trace_id")
        return [self._load_trace(row) for row in rows]

    def load_artifacts_from_disk(self) -> list[ArtifactRecord]:
        rows = self._postgres.fetch_all("SELECT * FROM artifacts ORDER BY artifact_id")
        return [self._load_artifact(row) for row in rows]

    def run_path(self, run_id: str) -> Path | None:
        return None

    def trace_path(self, trace_id: str) -> Path | None:
        return None

    def artifact_path(self, artifact_id: str) -> Path | None:
        return None


@dataclass
class RunStore:
    repository: RunRepository

    def record_run(self, run: RunRecord | Mapping[str, Any]) -> RunRecord:
        return self.repository.record_run(run)

    def record_trace(self, trace: TraceRecord | Mapping[str, Any]) -> TraceRecord:
        return self.repository.record_trace(trace)

    def record_artifact(self, artifact: ArtifactRecord | Mapping[str, Any]) -> ArtifactRecord:
        return self.repository.record_artifact(artifact)

    def get_run(self, run_id: str) -> RunRecord | None:
        return self.repository.get_run(run_id)

    def list_runs(self) -> list[RunRecord]:
        return self.repository.list_runs()

    def load_runs_from_disk(self) -> list[RunRecord]:
        return self.repository.load_runs_from_disk()

    def load_traces_from_disk(self) -> list[TraceRecord]:
        return self.repository.load_traces_from_disk()

    def load_artifacts_from_disk(self) -> list[ArtifactRecord]:
        return self.repository.load_artifacts_from_disk()

    def run_path(self, run_id: str) -> Path | None:
        return self.repository.run_path(run_id)

    def trace_path(self, trace_id: str) -> Path | None:
        return self.repository.trace_path(trace_id)

    def artifact_path(self, artifact_id: str) -> Path | None:
        return self.repository.artifact_path(artifact_id)


@dataclass
class RunRecorder:
    store: RunStore = field(default_factory=lambda: build_run_store(None))

    def record_run(self, run: RunRecord | Mapping[str, Any]) -> RunRecord:
        return self.store.record_run(run)

    def record_trace(self, trace: TraceRecord | Mapping[str, Any]) -> TraceRecord:
        return self.store.record_trace(trace)

    def record_artifact(self, artifact: ArtifactRecord | Mapping[str, Any]) -> ArtifactRecord:
        return self.store.record_artifact(artifact)

    def get_run(self, run_id: str) -> RunRecord | None:
        return self.store.get_run(run_id)

    def list_runs(self) -> list[RunRecord]:
        return self.store.list_runs()


def build_run_store(root: Path | str | None = None) -> RunStore:
    resolved_root = Path(root).expanduser().resolve() if root is not None else None
    settings = build_storage_settings(workspace_root=resolved_root)
    if settings.postgres_enabled:
        return RunStore(repository=PostgresRunRepository(root=resolved_root))
    return RunStore(repository=LocalFileRunRepository(root=resolved_root))
