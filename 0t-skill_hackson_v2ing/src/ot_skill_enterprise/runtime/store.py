from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol

from ot_skill_enterprise.storage import build_postgres_support, build_storage_settings

from .models import RuntimeSession


def _json_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, RuntimeSession):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return dict(value)
    dumper = getattr(value, "model_dump", None)
    if dumper is not None:
        payload = dumper(mode="json")
        if isinstance(payload, Mapping):
            return dict(payload)
    raise TypeError("runtime session payload must be a mapping or RuntimeSession")


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


class RuntimeSessionRepository(Protocol):
    def record_session(self, session: RuntimeSession | Mapping[str, Any]) -> RuntimeSession: ...

    def get_session(self, session_id: str) -> RuntimeSession | None: ...

    def list_sessions(self) -> list[RuntimeSession]: ...

    def load_from_disk(self) -> list[RuntimeSession]: ...


@dataclass
class LocalFileRuntimeSessionRepository:
    root: Path | None = None
    sessions: dict[str, dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.root is not None:
            self.root = Path(self.root).expanduser().resolve()
            self.root.mkdir(parents=True, exist_ok=True)

    def _category_dir(self) -> Path | None:
        if self.root is None:
            return None
        path = self.root / "runtime-sessions"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _persist(self, session_id: str, payload: dict[str, Any]) -> None:
        directory = self._category_dir()
        if directory is None:
            return
        (directory / f"{session_id}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    def record_session(self, session: RuntimeSession | Mapping[str, Any]) -> RuntimeSession:
        normalized = session if isinstance(session, RuntimeSession) else RuntimeSession.model_validate(dict(session))
        payload = normalized.model_dump(mode="json")
        self.sessions[normalized.session_id] = payload
        self._persist(normalized.session_id, payload)
        return normalized

    def get_session(self, session_id: str) -> RuntimeSession | None:
        payload = self.sessions.get(session_id)
        if payload is None:
            return None
        return RuntimeSession.model_validate(payload)

    def list_sessions(self) -> list[RuntimeSession]:
        return [RuntimeSession.model_validate(payload) for payload in self.sessions.values()]

    def load_from_disk(self) -> list[RuntimeSession]:
        directory = self._category_dir()
        if directory is None:
            return []
        loaded: list[RuntimeSession] = []
        for path in sorted(directory.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                record = RuntimeSession.model_validate(payload)
            except Exception:
                continue
            self.sessions[record.session_id] = record.model_dump(mode="json")
            loaded.append(record)
        return loaded


@dataclass
class PostgresRuntimeSessionRepository:
    root: Path | None = None

    def __post_init__(self) -> None:
        self.root = Path(self.root).expanduser().resolve() if self.root is not None else None
        self._settings = build_storage_settings(workspace_root=self.root)
        self._postgres = build_postgres_support(settings=self._settings)
        self._postgres.ensure_schema()

    def record_session(self, session: RuntimeSession | Mapping[str, Any]) -> RuntimeSession:
        normalized = session if isinstance(session, RuntimeSession) else RuntimeSession.model_validate(dict(session))
        payload = normalized.model_dump(mode="json")
        agent_id = payload["metadata"].get("agent_id") or payload["metadata"].get("runtime_id")
        flow_id = payload["metadata"].get("flow_id")
        self._postgres.execute(
            """
            INSERT INTO runtime_sessions (
                session_id, runtime_id, agent_id, flow_id, status, cwd, started_at, updated_at, finished_at, metadata_json
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (session_id) DO UPDATE SET
                runtime_id = EXCLUDED.runtime_id,
                agent_id = EXCLUDED.agent_id,
                flow_id = EXCLUDED.flow_id,
                status = EXCLUDED.status,
                cwd = EXCLUDED.cwd,
                started_at = EXCLUDED.started_at,
                updated_at = EXCLUDED.updated_at,
                finished_at = EXCLUDED.finished_at,
                metadata_json = EXCLUDED.metadata_json
            """,
            (
                normalized.session_id,
                normalized.runtime_id,
                agent_id,
                flow_id,
                normalized.status,
                normalized.cwd,
                normalized.started_at,
                normalized.updated_at,
                normalized.finished_at,
                self._postgres.dumps_json(payload.get("metadata") or {}),
            ),
        )
        return normalized

    def get_session(self, session_id: str) -> RuntimeSession | None:
        payload = self._postgres.fetch_one(
            """
            SELECT session_id, runtime_id, status, cwd, started_at, updated_at, finished_at, metadata_json
            FROM runtime_sessions
            WHERE session_id = %s
            """,
            (session_id,),
        )
        if payload is None:
            return None
        return RuntimeSession.model_validate(
            {
                "session_id": payload["session_id"],
                "runtime_id": payload["runtime_id"],
                "status": payload["status"],
                "cwd": payload["cwd"],
                "started_at": payload["started_at"],
                "updated_at": payload["updated_at"],
                "finished_at": payload["finished_at"],
                "metadata": _json_value(payload.get("metadata_json"), {}),
            }
        )

    def list_sessions(self) -> list[RuntimeSession]:
        rows = self._postgres.fetch_all(
            """
            SELECT session_id, runtime_id, status, cwd, started_at, updated_at, finished_at, metadata_json
            FROM runtime_sessions
            ORDER BY updated_at DESC NULLS LAST, started_at DESC NULLS LAST
            """
        )
        return [
            RuntimeSession.model_validate(
                {
                    "session_id": row["session_id"],
                    "runtime_id": row["runtime_id"],
                    "status": row["status"],
                    "cwd": row["cwd"],
                    "started_at": row["started_at"],
                    "updated_at": row["updated_at"],
                    "finished_at": row["finished_at"],
                    "metadata": _json_value(row.get("metadata_json"), {}),
                }
            )
            for row in rows
        ]

    def load_from_disk(self) -> list[RuntimeSession]:
        return self.list_sessions()


@dataclass
class RuntimeSessionStore:
    repository: RuntimeSessionRepository

    def record_session(self, session: RuntimeSession | Mapping[str, Any]) -> RuntimeSession:
        return self.repository.record_session(session)

    def get_session(self, session_id: str) -> RuntimeSession | None:
        return self.repository.get_session(session_id)

    def list_sessions(self) -> list[RuntimeSession]:
        return self.repository.list_sessions()

    def load_from_disk(self) -> list[RuntimeSession]:
        return self.repository.load_from_disk()


def build_runtime_session_store(root: Path | str | None = None) -> RuntimeSessionStore:
    resolved_root = Path(root).expanduser().resolve() if root is not None else None
    settings = build_storage_settings(workspace_root=resolved_root)
    if settings.postgres_enabled:
        return RuntimeSessionStore(repository=PostgresRuntimeSessionRepository(root=resolved_root))
    return RuntimeSessionStore(repository=LocalFileRuntimeSessionRepository(root=resolved_root))
