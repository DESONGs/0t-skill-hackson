from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol

from ot_skill_enterprise.storage import build_postgres_support, build_storage_settings

from .models import (
    ContractEvaluationResult,
    EvaluationRecord,
    RuntimeEvaluationResult,
    TaskMatchEvaluationResult,
)


def _json_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, EvaluationRecord):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return dict(value)
    dumper = getattr(value, "model_dump", None)
    if dumper is not None:
        payload = dumper(mode="json")
        if isinstance(payload, Mapping):
            return dict(payload)
    raise TypeError("evaluation records must be mappings or EvaluationRecord")


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


def _evaluation_payload(evaluation: EvaluationRecord) -> dict[str, Any]:
    payload = evaluation.model_dump(mode="json")
    payload["metadata"] = dict(payload.get("metadata") or {})
    payload["metadata"].update(
        {
            "runtime_result": evaluation.runtime_result.model_dump(mode="json"),
            "contract_result": evaluation.contract_result.model_dump(mode="json"),
            "task_match_result": evaluation.task_match_result.model_dump(mode="json"),
            "runtime_pass": evaluation.runtime_pass,
            "contract_pass": evaluation.contract_pass,
            "task_match_score": evaluation.task_match_score,
            "overall_grade": evaluation.overall_grade,
            "failure_reason": evaluation.failure_reason,
            "suggested_action": evaluation.suggested_action,
            "evidence_refs": evaluation.evidence_refs,
        }
    )
    return payload


def _evaluation_from_payload(payload: Mapping[str, Any]) -> EvaluationRecord:
    metadata = dict(payload.get("metadata") or {})
    runtime_result = metadata.get("runtime_result") or {}
    contract_result = metadata.get("contract_result") or {}
    task_match_result = metadata.get("task_match_result") or {}
    return EvaluationRecord.model_validate(
        {
            **dict(payload),
            "runtime_result": runtime_result,
            "contract_result": contract_result,
            "task_match_result": task_match_result,
            "runtime_pass": metadata.get("runtime_pass", payload.get("runtime_pass", False)),
            "contract_pass": metadata.get("contract_pass", payload.get("contract_pass", False)),
            "task_match_score": metadata.get("task_match_score", payload.get("task_match_score", 0.0)),
            "overall_grade": metadata.get("overall_grade", payload.get("overall_grade", payload.get("grade", "pending"))),
            "failure_reason": metadata.get("failure_reason", payload.get("failure_reason")),
            "suggested_action": metadata.get("suggested_action", payload.get("suggested_action")),
            "evidence_refs": metadata.get("evidence_refs", payload.get("evidence_refs", [])),
        }
    )


class EvaluationRepository(Protocol):
    def record_evaluation(self, evaluation: EvaluationRecord | Mapping[str, Any]) -> EvaluationRecord: ...

    def get_evaluation(self, evaluation_id: str) -> EvaluationRecord | None: ...

    def list_evaluations(self) -> list[EvaluationRecord]: ...

    def load_from_disk(self) -> list[EvaluationRecord]: ...

    def evaluation_path(self, evaluation_id: str) -> Path | None: ...


@dataclass
class LocalFileEvaluationRepository:
    root: Path | None = None
    evaluations: dict[str, dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.root is not None:
            self.root = Path(self.root).expanduser().resolve()
            self.root.mkdir(parents=True, exist_ok=True)

    def _category_dir(self) -> Path | None:
        if self.root is None:
            return None
        path = self.root / "evaluations"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _persist(self, evaluation_id: str, payload: dict[str, Any]) -> None:
        directory = self._category_dir()
        if directory is None:
            return
        (directory / f"{evaluation_id}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    def record_evaluation(self, evaluation: EvaluationRecord | Mapping[str, Any]) -> EvaluationRecord:
        normalized = evaluation if isinstance(evaluation, EvaluationRecord) else EvaluationRecord.model_validate(dict(evaluation))
        payload = _evaluation_payload(normalized)
        self.evaluations[normalized.evaluation_id] = payload
        self._persist(normalized.evaluation_id, payload)
        return normalized

    def get_evaluation(self, evaluation_id: str) -> EvaluationRecord | None:
        payload = self.evaluations.get(evaluation_id)
        if payload is None:
            return None
        return _evaluation_from_payload(payload)

    def list_evaluations(self) -> list[EvaluationRecord]:
        return [_evaluation_from_payload(payload) for payload in self.evaluations.values()]

    def load_from_disk(self) -> list[EvaluationRecord]:
        directory = self._category_dir()
        if directory is None:
            return []
        loaded: list[EvaluationRecord] = []
        for path in sorted(directory.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                record = _evaluation_from_payload(payload)
            except Exception:
                continue
            self.evaluations[record.evaluation_id] = _evaluation_payload(record)
            loaded.append(record)
        return loaded

    def evaluation_path(self, evaluation_id: str) -> Path | None:
        directory = self._category_dir()
        return None if directory is None else directory / f"{evaluation_id}.json"


@dataclass
class PostgresEvaluationRepository:
    root: Path | None = None

    def __post_init__(self) -> None:
        self.root = Path(self.root).expanduser().resolve() if self.root is not None else None
        self._settings = build_storage_settings(workspace_root=self.root)
        self._postgres = build_postgres_support(settings=self._settings)
        self._postgres.ensure_schema()

    def record_evaluation(self, evaluation: EvaluationRecord | Mapping[str, Any]) -> EvaluationRecord:
        normalized = evaluation if isinstance(evaluation, EvaluationRecord) else EvaluationRecord.model_validate(dict(evaluation))
        self._postgres.execute(
            """
            INSERT INTO evaluations (
                evaluation_id, run_id, runtime_session_id, subject_type, subject_id, grade, summary,
                finding_count, trace_ids_json, event_ids_json, event_types_json, artifact_ids_json,
                checks_json, findings_json, metadata_json, created_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s,
                %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb,
                %s::jsonb, %s::jsonb, %s::jsonb, %s
            )
            ON CONFLICT (evaluation_id) DO UPDATE SET
                run_id = EXCLUDED.run_id,
                runtime_session_id = EXCLUDED.runtime_session_id,
                subject_type = EXCLUDED.subject_type,
                subject_id = EXCLUDED.subject_id,
                grade = EXCLUDED.grade,
                summary = EXCLUDED.summary,
                finding_count = EXCLUDED.finding_count,
                trace_ids_json = EXCLUDED.trace_ids_json,
                event_ids_json = EXCLUDED.event_ids_json,
                event_types_json = EXCLUDED.event_types_json,
                artifact_ids_json = EXCLUDED.artifact_ids_json,
                checks_json = EXCLUDED.checks_json,
                findings_json = EXCLUDED.findings_json,
                metadata_json = EXCLUDED.metadata_json,
                created_at = EXCLUDED.created_at
            """,
            (
                normalized.evaluation_id,
                normalized.run_id,
                normalized.runtime_session_id,
                normalized.subject_type,
                normalized.subject_id,
                normalized.grade,
                normalized.summary,
                len(normalized.findings),
                self._postgres.dumps_json(normalized.trace_ids),
                self._postgres.dumps_json(normalized.event_ids),
                self._postgres.dumps_json(normalized.event_types),
                self._postgres.dumps_json(normalized.artifact_ids),
                self._postgres.dumps_json(normalized.checks),
                self._postgres.dumps_json(normalized.findings),
                self._postgres.dumps_json(
                    {
                        **dict(normalized.metadata),
                        "runtime_result": normalized.runtime_result.model_dump(mode="json"),
                        "contract_result": normalized.contract_result.model_dump(mode="json"),
                        "task_match_result": normalized.task_match_result.model_dump(mode="json"),
                        "runtime_pass": normalized.runtime_pass,
                        "contract_pass": normalized.contract_pass,
                        "task_match_score": normalized.task_match_score,
                        "overall_grade": normalized.overall_grade,
                        "failure_reason": normalized.failure_reason,
                        "suggested_action": normalized.suggested_action,
                        "evidence_refs": normalized.evidence_refs,
                    }
                ),
                normalized.created_at,
            ),
        )
        return normalized

    def get_evaluation(self, evaluation_id: str) -> EvaluationRecord | None:
        row = self._postgres.fetch_one("SELECT * FROM evaluations WHERE evaluation_id = %s", (evaluation_id,))
        if row is None:
            return None
        metadata = _json_value(row.get("metadata_json"), {})
        return _evaluation_from_payload(
            {
                "evaluation_id": row["evaluation_id"],
                "run_id": row["run_id"],
                "runtime_session_id": row["runtime_session_id"],
                "subject_type": row["subject_type"],
                "subject_id": row["subject_id"],
                "grade": row["grade"],
                "summary": row["summary"],
                "trace_ids": _json_value(row.get("trace_ids_json"), []),
                "event_ids": _json_value(row.get("event_ids_json"), []),
                "event_types": _json_value(row.get("event_types_json"), []),
                "artifact_ids": _json_value(row.get("artifact_ids_json"), []),
                "checks": _json_value(row.get("checks_json"), []),
                "findings": _json_value(row.get("findings_json"), []),
                "metadata": metadata,
                "created_at": row.get("created_at"),
            }
        )

    def list_evaluations(self) -> list[EvaluationRecord]:
        rows = self._postgres.fetch_all("SELECT * FROM evaluations ORDER BY created_at DESC NULLS LAST")
        return [
            _evaluation_from_payload(
                {
                    "evaluation_id": row["evaluation_id"],
                    "run_id": row["run_id"],
                    "runtime_session_id": row["runtime_session_id"],
                    "subject_type": row["subject_type"],
                    "subject_id": row["subject_id"],
                    "grade": row["grade"],
                    "summary": row["summary"],
                    "trace_ids": _json_value(row.get("trace_ids_json"), []),
                    "event_ids": _json_value(row.get("event_ids_json"), []),
                    "event_types": _json_value(row.get("event_types_json"), []),
                    "artifact_ids": _json_value(row.get("artifact_ids_json"), []),
                    "checks": _json_value(row.get("checks_json"), []),
                    "findings": _json_value(row.get("findings_json"), []),
                    "metadata": _json_value(row.get("metadata_json"), {}),
                    "created_at": row.get("created_at"),
                }
            )
            for row in rows
        ]

    def load_from_disk(self) -> list[EvaluationRecord]:
        return self.list_evaluations()

    def evaluation_path(self, evaluation_id: str) -> Path | None:
        return None


@dataclass
class EvaluationStore:
    repository: EvaluationRepository

    def record_evaluation(self, evaluation: EvaluationRecord | Mapping[str, Any]) -> EvaluationRecord:
        return self.repository.record_evaluation(evaluation)

    def get_evaluation(self, evaluation_id: str) -> EvaluationRecord | None:
        return self.repository.get_evaluation(evaluation_id)

    def list_evaluations(self) -> list[EvaluationRecord]:
        return self.repository.list_evaluations()

    def load_from_disk(self) -> list[EvaluationRecord]:
        return self.repository.load_from_disk()

    def evaluation_path(self, evaluation_id: str) -> Path | None:
        return self.repository.evaluation_path(evaluation_id)


def build_evaluation_store(root: Path | str | None = None) -> EvaluationStore:
    resolved_root = Path(root).expanduser().resolve() if root is not None else None
    settings = build_storage_settings(workspace_root=resolved_root)
    if settings.postgres_enabled:
        return EvaluationStore(repository=PostgresEvaluationRepository(root=resolved_root))
    return EvaluationStore(repository=LocalFileEvaluationRepository(root=resolved_root))
