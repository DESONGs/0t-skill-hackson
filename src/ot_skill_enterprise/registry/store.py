from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


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


@dataclass
class EvolutionRegistry:
    root: Path | None = None
    feedback_records: dict[str, dict[str, Any]] = field(default_factory=dict)
    cases: dict[str, dict[str, Any]] = field(default_factory=dict)
    proposals: dict[str, dict[str, Any]] = field(default_factory=dict)
    submissions: dict[str, dict[str, Any]] = field(default_factory=dict)

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
        path = directory / f"{record_id}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

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
        self.feedback_records[record_id] = payload
        self._persist("feedback", record_id, payload)
        return payload

    def record_case(self, case: Any) -> dict[str, Any]:
        payload = _json_payload(case)
        record_id = str(payload["case_id"])
        self.cases[record_id] = payload
        self._persist("cases", record_id, payload)
        return payload

    def record_proposal(self, proposal: Any) -> dict[str, Any]:
        payload = _json_payload(proposal)
        record_id = str(payload["proposal_id"])
        self.proposals[record_id] = payload
        self._persist("proposals", record_id, payload)
        return payload

    def record_submission(self, submission: Any) -> dict[str, Any]:
        payload = _json_payload(submission)
        record_id = str(payload["submission_id"])
        self.submissions[record_id] = payload
        self._persist("submissions", record_id, payload)
        return payload

    def get_case(self, case_id: str) -> dict[str, Any] | None:
        return self.cases.get(case_id)

    def get_proposal(self, proposal_id: str) -> dict[str, Any] | None:
        return self.proposals.get(proposal_id)

    def get_submission(self, submission_id: str) -> dict[str, Any] | None:
        return self.submissions.get(submission_id)


def build_evolution_registry(root: Path | str | None = None) -> EvolutionRegistry:
    return EvolutionRegistry(root=Path(root) if root is not None else None)
