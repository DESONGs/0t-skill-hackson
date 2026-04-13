from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from .config import StorageSettings, build_storage_settings


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS runtime_sessions (
    session_id TEXT PRIMARY KEY,
    runtime_id TEXT NOT NULL,
    agent_id TEXT,
    flow_id TEXT,
    status TEXT NOT NULL,
    cwd TEXT,
    started_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    runtime_session_id TEXT NOT NULL REFERENCES runtime_sessions(session_id) ON DELETE CASCADE,
    runtime_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    flow_id TEXT NOT NULL,
    status TEXT NOT NULL,
    ok BOOLEAN NOT NULL,
    summary TEXT NOT NULL,
    input_payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    output_payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    skill_ids_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    provider_ids_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    trace_ids_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    artifact_ids_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    event_count INTEGER NOT NULL DEFAULT 0,
    trace_count INTEGER NOT NULL DEFAULT 0,
    artifact_count INTEGER NOT NULL DEFAULT 0,
    evaluation_id TEXT,
    failure_json JSONB,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS run_traces (
    trace_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    runtime_session_id TEXT NOT NULL REFERENCES runtime_sessions(session_id) ON DELETE CASCADE,
    summary TEXT,
    blob_uri TEXT,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS runtime_events (
    event_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    runtime_session_id TEXT NOT NULL REFERENCES runtime_sessions(session_id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    status TEXT,
    summary TEXT,
    timestamp TIMESTAMPTZ,
    payload_json JSONB,
    blob_uri TEXT,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    runtime_session_id TEXT NOT NULL REFERENCES runtime_sessions(session_id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    label TEXT,
    uri TEXT,
    content_type TEXT,
    size_bytes BIGINT,
    checksum TEXT,
    source_step_id TEXT,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS evaluations (
    evaluation_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    runtime_session_id TEXT NOT NULL REFERENCES runtime_sessions(session_id) ON DELETE CASCADE,
    subject_type TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    grade TEXT NOT NULL,
    summary TEXT NOT NULL,
    finding_count INTEGER NOT NULL DEFAULT 0,
    trace_ids_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    event_ids_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    event_types_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    artifact_ids_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    checks_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    findings_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS feedback (
    feedback_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    runtime_session_id TEXT NOT NULL,
    subject_id TEXT,
    status TEXT,
    summary TEXT,
    payload_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS cases (
    case_id TEXT PRIMARY KEY,
    run_id TEXT,
    runtime_session_id TEXT,
    subject_id TEXT,
    status TEXT,
    summary TEXT,
    payload_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS proposals (
    proposal_id TEXT PRIMARY KEY,
    run_id TEXT,
    runtime_session_id TEXT,
    subject_id TEXT,
    status TEXT,
    summary TEXT,
    payload_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS submissions (
    submission_id TEXT PRIMARY KEY,
    run_id TEXT,
    runtime_session_id TEXT,
    subject_id TEXT,
    status TEXT,
    summary TEXT,
    payload_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_runs_runtime_session_id ON runs(runtime_session_id);
CREATE INDEX IF NOT EXISTS idx_runtime_events_run_id ON runtime_events(run_id);
CREATE INDEX IF NOT EXISTS idx_runtime_events_runtime_session_id ON runtime_events(runtime_session_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_run_id ON artifacts(run_id);
CREATE INDEX IF NOT EXISTS idx_evaluations_run_id ON evaluations(run_id);
"""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


@dataclass(slots=True)
class PostgresSupport:
    settings: StorageSettings
    _schema_ready: bool = False

    @property
    def enabled(self) -> bool:
        return self.settings.postgres_enabled

    def _connect(self) -> Any:
        if not self.settings.db_dsn:
            raise RuntimeError("OT_DB_DSN is not configured")
        try:
            import psycopg
        except Exception as exc:  # pragma: no cover - optional dependency path
            raise RuntimeError("psycopg is required when OT_DB_DSN is set") from exc
        return psycopg.connect(self.settings.db_dsn)

    def ensure_schema(self) -> None:
        if not self.enabled or self._schema_ready:
            return
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(SCHEMA_SQL)
            conn.commit()
        self._schema_ready = True

    @contextmanager
    def connection(self) -> Iterator[Any]:
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        self.ensure_schema()
        with self.connection() as conn:
            with conn.cursor(row_factory=self._dict_row_factory()) as cur:
                cur.execute(sql, params)
                return list(cur.fetchall())

    def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        rows = self.fetch_all(sql, params)
        return rows[0] if rows else None

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        self.ensure_schema()
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
            conn.commit()

    def execute_many(self, sql: str, params_seq: list[tuple[Any, ...]]) -> None:
        if not params_seq:
            return
        self.ensure_schema()
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.executemany(sql, params_seq)
            conn.commit()

    @staticmethod
    def dumps_json(value: Any) -> str:
        return _json(value)

    @staticmethod
    def _dict_row_factory() -> Any:
        try:
            from psycopg.rows import dict_row
        except Exception as exc:  # pragma: no cover - optional dependency path
            raise RuntimeError("psycopg rows support is required when OT_DB_DSN is set") from exc
        return dict_row


def build_postgres_support(
    *,
    settings: StorageSettings | None = None,
    project_root: Path | None = None,
    workspace_root: Path | None = None,
) -> PostgresSupport:
    resolved = settings or build_storage_settings(project_root=project_root, workspace_root=workspace_root)
    return PostgresSupport(settings=resolved)
