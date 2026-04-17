from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from ot_skill_enterprise.agents import AgentStore, build_agent_store
from ot_skill_enterprise.lab.models import PromotionRecord, SkillCandidate
from ot_skill_enterprise.qa import EvaluationStore, build_evaluation_store
from ot_skill_enterprise.runs import RunStore, build_run_store
from ot_skill_enterprise.storage import build_blob_store, build_postgres_support, build_storage_settings


def _dump_model(value: Any) -> Any:
    dumper = getattr(value, "model_dump", None)
    if dumper is not None:
        return dumper(mode="json")
    return value


def _json_payload(value: Any) -> dict[str, Any]:
    payload = _dump_model(value)
    if isinstance(payload, Mapping):
        return dict(payload)
    raise TypeError("registry records must be mappings or pydantic models")


def _stable_payload(value: Any) -> str:
    return json.dumps(_dump_model(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


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


@dataclass
class EvolutionRegistry:
    root: Path | None = None
    agents: AgentStore = field(init=False)
    runs: RunStore = field(init=False)
    evaluations: EvaluationStore = field(init=False)
    runtime_events: dict[str, dict[str, Any]] = field(default_factory=dict)
    candidate_records: dict[str, dict[str, Any]] = field(default_factory=dict)
    promotion_records: dict[str, dict[str, Any]] = field(default_factory=dict)
    feedback_records: dict[str, dict[str, Any]] = field(default_factory=dict)
    cases: dict[str, dict[str, Any]] = field(default_factory=dict)
    proposals: dict[str, dict[str, Any]] = field(default_factory=dict)
    submissions: dict[str, dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.root = Path(self.root).expanduser().resolve() if self.root is not None else None
        if self.root is not None:
            self.root.mkdir(parents=True, exist_ok=True)
        self._settings = build_storage_settings(workspace_root=self.root)
        self._postgres = build_postgres_support(settings=self._settings)
        self._blob = build_blob_store(settings=self._settings)
        if self._settings.postgres_enabled:
            self._postgres.ensure_schema()
        self.agents = build_agent_store(self.root)
        self.runs = build_run_store(self.root)
        self.evaluations = build_evaluation_store(self.root)

    def _category_dir(self, category: str) -> Path | None:
        if self.root is None:
            return None
        path = self.root / category
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _persist(self, category: str, record_id: str, payload: dict[str, Any]) -> None:
        if self._settings.postgres_enabled:
            return
        directory = self._category_dir(category)
        if directory is None:
            return
        (directory / f"{record_id}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    @staticmethod
    def _runtime_event_identity(event: Any) -> str | None:
        payload = _json_payload(event)
        event_id = payload.get("event_id") or payload.get("id")
        return str(event_id) if event_id is not None and str(event_id).strip() else None

    def _record_runtime_events(self, events: list[Any]) -> None:
        for event in events:
            self.record_runtime_event(event)

    def record_agent(self, agent: Any) -> Any:
        return self.agents.record_agent(agent)

    def record_run(self, run: Any) -> Any:
        normalized = self.runs.record_run(run)
        runtime_events = list(getattr(normalized, "runtime_events", []) or [])
        for trace in list(getattr(normalized, "traces", []) or []):
            runtime_events.extend(list(getattr(trace, "events", []) or []))
        if not runtime_events and getattr(normalized, "trace", None) is not None:
            runtime_events.extend(list(getattr(normalized.trace, "events", []) or []))
        self._record_runtime_events(runtime_events)
        return normalized

    def record_trace(self, trace: Any) -> Any:
        normalized = self.runs.record_trace(trace)
        self._record_runtime_events(list(getattr(normalized, "events", []) or []))
        return normalized

    def record_artifact(self, artifact: Any) -> Any:
        return self.runs.record_artifact(artifact)

    def record_evaluation(self, evaluation: Any) -> Any:
        return self.evaluations.record_evaluation(evaluation)

    def record_runtime_event(self, event: Any) -> dict[str, Any]:
        payload = _json_payload(event)
        record_id = self._runtime_event_identity(payload)
        if record_id is None:
            raise TypeError("runtime events must include an event_id")
        self.runtime_events[record_id] = payload
        if self._settings.postgres_enabled:
            session_id = str(payload.get("runtime_session_id") or payload.get("metadata", {}).get("runtime_session_id") or "")
            if not session_id:
                raise ValueError("runtime event is missing runtime_session_id")
            payload_json = payload.get("payload") or {}
            serialized = json.dumps(payload_json, ensure_ascii=False, default=str).encode("utf-8")
            blob_uri = None
            payload_column = payload_json
            if len(serialized) > self._settings.inline_payload_limit_bytes:
                blob = self._blob.put_json(f"runtime-events/{record_id}.json", payload_json)
                blob_uri = blob.uri
                payload_column = {}
            self._postgres.execute(
                """
                INSERT INTO runtime_events (
                    event_id, run_id, runtime_session_id, event_type, status, summary, timestamp, payload_json, blob_uri, metadata_json
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s::jsonb)
                ON CONFLICT (event_id) DO UPDATE SET
                    run_id = EXCLUDED.run_id,
                    runtime_session_id = EXCLUDED.runtime_session_id,
                    event_type = EXCLUDED.event_type,
                    status = EXCLUDED.status,
                    summary = EXCLUDED.summary,
                    timestamp = EXCLUDED.timestamp,
                    payload_json = EXCLUDED.payload_json,
                    blob_uri = EXCLUDED.blob_uri,
                    metadata_json = EXCLUDED.metadata_json
                """,
                (
                    record_id,
                    payload.get("run_id"),
                    session_id,
                    payload.get("event_type"),
                    payload.get("status"),
                    payload.get("summary"),
                    payload.get("timestamp"),
                    self._postgres.dumps_json(payload_column),
                    blob_uri,
                    self._postgres.dumps_json(payload.get("metadata") or {}),
                ),
            )
        else:
            self._persist("runtime-events", record_id, payload)
        return payload

    def _record_registry_payload(self, table: str, identity_key: str, payload: dict[str, Any]) -> dict[str, Any]:
        record_id = str(payload[identity_key])
        summary = payload.get("summary") or payload.get("change_summary")
        status = payload.get("status")
        runtime_session_id = (
            payload.get("runtime_session_id")
            or payload.get("metadata", {}).get("runtime_session_id")
        )
        run_id = payload.get("run_id") or payload.get("metadata", {}).get("source_id")
        subject_id = payload.get("subject_id") or payload.get("target_skill_name")
        if not runtime_session_id:
            raise ValueError(f"{table} payload is missing runtime_session_id")
        if self._settings.postgres_enabled:
            self._postgres.execute(
                f"""
                INSERT INTO {table} ({identity_key}, run_id, runtime_session_id, subject_id, status, summary, payload_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT ({identity_key}) DO UPDATE SET
                    run_id = EXCLUDED.run_id,
                    runtime_session_id = EXCLUDED.runtime_session_id,
                    subject_id = EXCLUDED.subject_id,
                    status = EXCLUDED.status,
                    summary = EXCLUDED.summary,
                    payload_json = EXCLUDED.payload_json
                """,
                (
                    record_id,
                    run_id,
                    runtime_session_id,
                    subject_id,
                    status,
                    summary,
                    self._postgres.dumps_json(payload),
                ),
            )
        else:
            self._persist(table, record_id, payload)
        return payload

    @staticmethod
    def _candidate_snapshot(candidate: SkillCandidate | Mapping[str, Any]) -> dict[str, Any]:
        payload = _json_payload(candidate)
        candidate_id = str(payload.get("candidate_id") or payload.get("case_id") or payload.get("id"))
        payload.setdefault("candidate_id", candidate_id)
        payload.setdefault("case_id", candidate_id)
        payload.setdefault("run_id", str(payload.get("source_run_id") or payload.get("run_id") or payload.get("metadata", {}).get("source_run_id") or ""))
        payload.setdefault("subject_id", payload.get("target_skill_name", candidate_id))
        payload.setdefault("status", payload.get("status") or payload.get("validation_status") or "pending")
        payload.setdefault("summary", payload.get("change_summary") or payload.get("summary") or candidate_id)
        payload.setdefault("runtime_session_id", str(payload.get("runtime_session_id") or payload.get("metadata", {}).get("runtime_session_id") or ""))
        return payload

    @staticmethod
    def _promotion_snapshot(promotion: PromotionRecord | Mapping[str, Any]) -> dict[str, Any]:
        payload = _json_payload(promotion)
        promotion_id = str(payload.get("promotion_id") or payload.get("submission_id") or payload.get("id"))
        payload.setdefault("promotion_id", promotion_id)
        payload.setdefault("submission_id", promotion_id)
        payload.setdefault("run_id", str(payload.get("source_run_id") or payload.get("run_id") or payload.get("metadata", {}).get("source_run_id") or ""))
        payload.setdefault("subject_id", payload.get("target_skill_name", promotion_id))
        payload.setdefault("status", payload.get("validation_status") or payload.get("registry_status") or "pending")
        payload.setdefault("summary", payload.get("candidate_slug") or payload.get("target_skill_name") or promotion_id)
        payload.setdefault("runtime_session_id", str(payload.get("runtime_session_id") or payload.get("metadata", {}).get("runtime_session_id") or ""))
        return payload

    def candidate_path(self, candidate_id: str) -> Path | None:
        directory = self._category_dir("candidates")
        return None if directory is None else directory / f"{candidate_id}.json"

    def promotion_path(self, promotion_id: str) -> Path | None:
        directory = self._category_dir("promotions")
        return None if directory is None else directory / f"{promotion_id}.json"

    def record_candidate(self, candidate: SkillCandidate | Mapping[str, Any]) -> dict[str, Any]:
        payload = self._candidate_snapshot(candidate)
        record_id = str(payload["candidate_id"])
        self.candidate_records[record_id] = payload
        self.cases[record_id] = payload
        if not self._settings.postgres_enabled:
            self._persist("candidates", record_id, payload)
            return payload
        return self._record_registry_payload("cases", "case_id", payload)

    def record_promotion(self, promotion: PromotionRecord | Mapping[str, Any]) -> dict[str, Any]:
        payload = self._promotion_snapshot(promotion)
        record_id = str(payload["promotion_id"])
        self.promotion_records[record_id] = payload
        self.submissions[record_id] = payload
        if not self._settings.postgres_enabled:
            self._persist("promotions", record_id, payload)
            return payload
        return self._record_registry_payload("submissions", "submission_id", payload)

    def get_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        payload = self.candidate_records.get(candidate_id)
        if payload is not None:
            return payload
        if self._settings.postgres_enabled:
            row = self._postgres.fetch_one("SELECT payload_json FROM cases WHERE case_id = %s", (candidate_id,))
            if row is None:
                return None
            payload = _json_value(row["payload_json"], {})
            if isinstance(payload, dict):
                self.candidate_records[candidate_id] = payload
                return payload
            return None
        path = self.candidate_path(candidate_id)
        if path is None or not path.exists():
            return None
        payload = _json_value(path.read_text(encoding="utf-8"), {})
        if isinstance(payload, dict):
            self.candidate_records[candidate_id] = payload
            return payload
        return None

    def get_promotion(self, promotion_id: str) -> dict[str, Any] | None:
        payload = self.promotion_records.get(promotion_id)
        if payload is not None:
            return payload
        if self._settings.postgres_enabled:
            row = self._postgres.fetch_one("SELECT payload_json FROM submissions WHERE submission_id = %s", (promotion_id,))
            if row is None:
                return None
            payload = _json_value(row["payload_json"], {})
            if isinstance(payload, dict):
                self.promotion_records[promotion_id] = payload
                return payload
            return None
        path = self.promotion_path(promotion_id)
        if path is None or not path.exists():
            return None
        payload = _json_value(path.read_text(encoding="utf-8"), {})
        if isinstance(payload, dict):
            self.promotion_records[promotion_id] = payload
            return payload
        return None

    def list_candidates(self) -> list[dict[str, Any]]:
        if self.candidate_records:
            return list(self.candidate_records.values())
        if self._settings.postgres_enabled:
            rows = self._postgres.fetch_all("SELECT payload_json FROM cases ORDER BY created_at DESC")
            return [_json_value(row["payload_json"], {}) for row in rows]
        directory = self._category_dir("candidates")
        if directory is None:
            return []
        return [_json_value(path.read_text(encoding="utf-8"), {}) for path in sorted(directory.glob("*.json"))]

    def list_promotions(self) -> list[dict[str, Any]]:
        if self.promotion_records:
            return list(self.promotion_records.values())
        if self._settings.postgres_enabled:
            rows = self._postgres.fetch_all("SELECT payload_json FROM submissions ORDER BY created_at DESC")
            return [_json_value(row["payload_json"], {}) for row in rows]
        directory = self._category_dir("promotions")
        if directory is None:
            return []
        return [_json_value(path.read_text(encoding="utf-8"), {}) for path in sorted(directory.glob("*.json"))]

    def case_path(self, case_id: str) -> Path | None:
        directory = self._category_dir("cases")
        return None if directory is None else directory / f"{case_id}.json"

    def proposal_path(self, proposal_id: str) -> Path | None:
        directory = self._category_dir("proposals")
        return None if directory is None else directory / f"{proposal_id}.json"

    def submission_path(self, submission_id: str) -> Path | None:
        directory = self._category_dir("submissions")
        return None if directory is None else directory / f"{submission_id}.json"

    def record_feedback(self, feedback: Any) -> dict[str, Any]:
        payload = _json_payload(feedback)
        record_id = hashlib.sha256(_stable_payload(payload).encode("utf-8")).hexdigest()[:12]
        payload.setdefault("feedback_id", record_id)
        self.feedback_records[record_id] = payload
        return self._record_registry_payload("feedback", "feedback_id", payload)

    def record_case(self, case: Any) -> dict[str, Any]:
        payload = _json_payload(case)
        record_id = str(payload["case_id"])
        self.cases[record_id] = payload
        return self._record_registry_payload("cases", "case_id", payload)

    def record_proposal(self, proposal: Any) -> dict[str, Any]:
        payload = _json_payload(proposal)
        record_id = str(payload["proposal_id"])
        self.proposals[record_id] = payload
        return self._record_registry_payload("proposals", "proposal_id", payload)

    def record_submission(self, submission: Any) -> dict[str, Any]:
        payload = _json_payload(submission)
        record_id = str(payload["submission_id"])
        self.submissions[record_id] = payload
        return self._record_registry_payload("submissions", "submission_id", payload)

    def get_case(self, case_id: str) -> dict[str, Any] | None:
        return self.cases.get(case_id)

    def get_proposal(self, proposal_id: str) -> dict[str, Any] | None:
        return self.proposals.get(proposal_id)

    def get_submission(self, submission_id: str) -> dict[str, Any] | None:
        return self.submissions.get(submission_id)

    def list_feedback(self) -> list[dict[str, Any]]:
        if self._settings.postgres_enabled:
            rows = self._postgres.fetch_all("SELECT payload_json FROM feedback ORDER BY created_at DESC")
            return [_json_value(row["payload_json"], {}) for row in rows]
        directory = self._category_dir("feedback")
        if directory is None:
            return []
        return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(directory.glob("*.json"))]

    def list_cases(self) -> list[dict[str, Any]]:
        if self._settings.postgres_enabled:
            rows = self._postgres.fetch_all("SELECT payload_json FROM cases ORDER BY created_at DESC")
            return [_json_value(row["payload_json"], {}) for row in rows]
        directory = self._category_dir("cases")
        if directory is None:
            return []
        return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(directory.glob("*.json"))]

    def list_proposals(self) -> list[dict[str, Any]]:
        if self._settings.postgres_enabled:
            rows = self._postgres.fetch_all("SELECT payload_json FROM proposals ORDER BY created_at DESC")
            return [_json_value(row["payload_json"], {}) for row in rows]
        directory = self._category_dir("proposals")
        if directory is None:
            return []
        return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(directory.glob("*.json"))]

    def list_submissions(self) -> list[dict[str, Any]]:
        if self._settings.postgres_enabled:
            rows = self._postgres.fetch_all("SELECT payload_json FROM submissions ORDER BY created_at DESC")
            return [_json_value(row["payload_json"], {}) for row in rows]
        directory = self._category_dir("submissions")
        if directory is None:
            return []
        return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(directory.glob("*.json"))]


def build_evolution_registry(root: Path | str | None = None) -> EvolutionRegistry:
    return EvolutionRegistry(root=Path(root) if root is not None else None)
