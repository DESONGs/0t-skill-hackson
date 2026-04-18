from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, TypeVar

from pydantic import BaseModel

from .models import (
    OptimizationActivation,
    OptimizationDecision,
    OptimizationRecommendation,
    OptimizationRun,
    OptimizationSession,
    OptimizationVariant,
    TeamAdapterSession,
    WorkItem,
)


ModelT = TypeVar("ModelT", bound=BaseModel)


def _dump_model(value: Any) -> Any:
    dumper = getattr(value, "model_dump", None)
    if dumper is not None:
        return dumper(mode="json")
    return value


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _load_model(path: Path, model_type: type[ModelT]) -> ModelT | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return model_type.model_validate(payload)


def _load_models(directory: Path, model_type: type[ModelT]) -> list[ModelT]:
    if not directory.exists():
        return []
    records: list[ModelT] = []
    for path in sorted(directory.glob("*.json")):
        if path.name.endswith(".result.json"):
            continue
        loaded = _load_model(path, model_type)
        if loaded is not None:
            records.append(loaded)
    return records


@dataclass(slots=True)
class TeamStateStore:
    workspace_root: Path

    def __post_init__(self) -> None:
        self.workspace_root = Path(self.workspace_root).expanduser().resolve()
        self.workspace_root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        path = self.workspace_root / "team"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def sessions_root(self) -> Path:
        path = self.root / "sessions"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def session_dir(self, session_id: str) -> Path:
        path = self.sessions_root / session_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def session_path(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "session.json"

    def brief_path(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "brief.md"

    def journal_path(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "journal.jsonl"

    def leaderboard_path(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "leaderboard.json"

    def recommendation_path(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "recommendation.json"

    def activation_path(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "activation.json"

    def variants_dir(self, session_id: str) -> Path:
        path = self.session_dir(session_id) / "variants"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def runs_dir(self, session_id: str) -> Path:
        path = self.session_dir(session_id) / "runs"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def decisions_dir(self, session_id: str) -> Path:
        path = self.session_dir(session_id) / "decisions"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def work_items_dir(self, session_id: str) -> Path:
        path = self.session_dir(session_id) / "work-items"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def agent_sessions_dir(self, session_id: str) -> Path:
        path = self.session_dir(session_id) / "agent-sessions"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def handoffs_dir(self, session_id: str) -> Path:
        path = self.session_dir(session_id) / "handoffs"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def artifacts_dir(self, session_id: str) -> Path:
        path = self.session_dir(session_id) / "artifacts"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def save_session(self, session: OptimizationSession) -> OptimizationSession:
        _write_json(self.session_path(session.session_id), session.model_dump(mode="json"))
        return session

    def get_session(self, session_id: str) -> OptimizationSession | None:
        return _load_model(self.session_path(session_id), OptimizationSession)

    def list_sessions(self) -> list[OptimizationSession]:
        records: list[OptimizationSession] = []
        for path in sorted(self.sessions_root.glob("*/session.json")):
            loaded = _load_model(path, OptimizationSession)
            if loaded is not None:
                records.append(loaded)
        records.sort(key=lambda item: item.updated_at, reverse=True)
        return records

    def save_brief(self, session_id: str, content: str) -> str:
        path = self.brief_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return str(path)

    def load_brief(self, session_id: str) -> str:
        path = self.brief_path(session_id)
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def append_journal(self, session_id: str, event: dict[str, Any]) -> None:
        path = self.journal_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, default=str))
            handle.write("\n")

    def save_work_item(self, item: WorkItem) -> WorkItem:
        _write_json(self.work_items_dir(item.session_id) / f"{item.work_item_id}.json", item.model_dump(mode="json"))
        return item

    def get_work_item(self, session_id: str, work_item_id: str) -> WorkItem | None:
        return _load_model(self.work_items_dir(session_id) / f"{work_item_id}.json", WorkItem)

    def list_work_items(self, session_id: str) -> list[WorkItem]:
        records = _load_models(self.work_items_dir(session_id), WorkItem)
        records.sort(key=lambda item: item.created_at)
        return records

    def save_work_item_result(self, session_id: str, work_item_id: str, payload: dict[str, Any]) -> str:
        path = self.work_items_dir(session_id) / f"{work_item_id}.result.json"
        _write_json(path, payload)
        return str(path)

    def load_work_item_result(self, session_id: str, work_item_id: str) -> dict[str, Any] | None:
        path = self.work_items_dir(session_id) / f"{work_item_id}.result.json"
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None

    def save_variant(self, variant: OptimizationVariant) -> OptimizationVariant:
        _write_json(self.variants_dir(variant.session_id) / f"{variant.variant_id}.json", variant.model_dump(mode="json"))
        return variant

    def get_variant(self, session_id: str, variant_id: str) -> OptimizationVariant | None:
        return _load_model(self.variants_dir(session_id) / f"{variant_id}.json", OptimizationVariant)

    def list_variants(self, session_id: str) -> list[OptimizationVariant]:
        records = _load_models(self.variants_dir(session_id), OptimizationVariant)
        records.sort(key=lambda item: item.updated_at)
        return records

    def save_run(self, run: OptimizationRun) -> OptimizationRun:
        _write_json(self.runs_dir(run.session_id) / f"{run.run_id}.json", run.model_dump(mode="json"))
        return run

    def list_runs(self, session_id: str) -> list[OptimizationRun]:
        records = _load_models(self.runs_dir(session_id), OptimizationRun)
        records.sort(key=lambda item: item.updated_at)
        return records

    def save_decision(self, decision: OptimizationDecision) -> OptimizationDecision:
        _write_json(self.decisions_dir(decision.session_id) / f"{decision.decision_id}.json", decision.model_dump(mode="json"))
        return decision

    def list_decisions(self, session_id: str) -> list[OptimizationDecision]:
        records = _load_models(self.decisions_dir(session_id), OptimizationDecision)
        records.sort(key=lambda item: item.created_at)
        return records

    def save_recommendation(self, recommendation: OptimizationRecommendation) -> OptimizationRecommendation:
        _write_json(self.recommendation_path(recommendation.session_id), recommendation.model_dump(mode="json"))
        return recommendation

    def get_recommendation(self, session_id: str) -> OptimizationRecommendation | None:
        return _load_model(self.recommendation_path(session_id), OptimizationRecommendation)

    def clear_recommendation(self, session_id: str) -> None:
        path = self.recommendation_path(session_id)
        if path.exists():
            path.unlink()

    def save_activation(self, activation: OptimizationActivation) -> OptimizationActivation:
        _write_json(self.activation_path(activation.session_id), activation.model_dump(mode="json"))
        return activation

    def get_activation(self, session_id: str) -> OptimizationActivation | None:
        return _load_model(self.activation_path(session_id), OptimizationActivation)

    def save_agent_session(self, payload: TeamAdapterSession) -> TeamAdapterSession:
        _write_json(self.agent_sessions_dir(payload.session_id) / f"{payload.agent_session_id}.json", payload.model_dump(mode="json"))
        return payload

    def list_agent_sessions(self, session_id: str) -> list[TeamAdapterSession]:
        records = _load_models(self.agent_sessions_dir(session_id), TeamAdapterSession)
        records.sort(key=lambda item: item.created_at)
        return records

    def write_handoff(self, session_id: str, name: str, content: str) -> str:
        path = self.handoffs_dir(session_id) / name
        path.write_text(content, encoding="utf-8")
        return str(path)

    def save_leaderboard(self, session_id: str, payload: Iterable[dict[str, Any]]) -> None:
        _write_json(self.leaderboard_path(session_id), list(payload))

    def load_leaderboard(self, session_id: str) -> list[dict[str, Any]]:
        path = self.leaderboard_path(session_id)
        if not path.exists():
            return []
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, list) else []
