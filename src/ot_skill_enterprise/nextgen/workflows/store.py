from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, TypeVar

from ot_skill_enterprise.shared.contracts.common import ArtifactRef

from .models import (
    ApprovalConvergenceResult,
    BenchmarkScorecard,
    RecommendationBundle,
    ResearchIterationRecord,
    ResearchSessionState,
    ReviewDecision,
    WorkflowArtifact,
    WorkflowVariant,
)

ModelT = TypeVar("ModelT")


def _json_safe(value: Any) -> Any:
    dumper = getattr(value, "model_dump", None)
    if callable(dumper):
        return dumper(mode="json")
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_json_safe(dict(payload)), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists() or not path.is_file():
        return None
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return body if isinstance(body, dict) else None


def _load_model(path: Path, model_type: type[ModelT]) -> ModelT | None:
    payload = _read_json(path)
    if payload is None:
        return None
    validator = getattr(model_type, "model_validate")
    return validator(payload)


class ResearchLoopStore:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.root = self.workspace_root / "runtime-sessions"
        self.root.mkdir(parents=True, exist_ok=True)

    def resolve_session_id(self, request_payload: Mapping[str, Any]) -> str:
        explicit = str(
            request_payload.get("session_id")
            or dict(request_payload.get("metadata") or {}).get("session_id")
            or ""
        ).strip()
        if explicit:
            return explicit
        digest = hashlib.sha256(
            json.dumps(
                {
                    "workflow_id": request_payload.get("workflow_id"),
                    "workspace_id": request_payload.get("workspace_id"),
                    "wallet": request_payload.get("wallet"),
                    "chain": request_payload.get("chain"),
                    "skill_name": request_payload.get("skill_name"),
                    "objective": request_payload.get("objective"),
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return f"research-{digest[:12]}"

    def session_dir(self, session_id: str) -> Path:
        path = self.root / session_id / "workflow-kernel"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _dir(self, session_id: str, name: str) -> Path:
        path = self.session_dir(session_id) / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    def session_path(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "session.json"

    def leaderboard_path(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "leaderboard.json"

    def recommendation_path(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "recommendation.json"

    def approval_path(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "approval-convergence.json"

    def save_session(self, session: ResearchSessionState) -> ResearchSessionState:
        _write_json(self.session_path(session.session_id), session.model_dump(mode="json"))
        return session

    def load_session(self, session_id: str) -> ResearchSessionState | None:
        return _load_model(self.session_path(session_id), ResearchSessionState)

    def save_variant(self, session_id: str, variant: WorkflowVariant) -> WorkflowVariant:
        _write_json(self._dir(session_id, "variants") / f"{variant.variant_id}.json", variant.model_dump(mode="json"))
        return variant

    def load_variant(self, session_id: str, variant_id: str) -> WorkflowVariant | None:
        return _load_model(self._dir(session_id, "variants") / f"{variant_id}.json", WorkflowVariant)

    def list_variants(self, session_id: str) -> list[WorkflowVariant]:
        return self._load_many(self._dir(session_id, "variants"), WorkflowVariant)

    def save_scorecard(self, session_id: str, scorecard: BenchmarkScorecard) -> BenchmarkScorecard:
        _write_json(self._dir(session_id, "benchmarks") / f"{scorecard.variant_id}.json", scorecard.model_dump(mode="json"))
        return scorecard

    def list_scorecards(self, session_id: str) -> list[BenchmarkScorecard]:
        return self._load_many(self._dir(session_id, "benchmarks"), BenchmarkScorecard)

    def save_review(self, session_id: str, review: ReviewDecision) -> ReviewDecision:
        _write_json(self._dir(session_id, "reviews") / f"{review.variant_id}.json", review.model_dump(mode="json"))
        return review

    def list_reviews(self, session_id: str) -> list[ReviewDecision]:
        return self._load_many(self._dir(session_id, "reviews"), ReviewDecision)

    def save_iteration(self, record: ResearchIterationRecord) -> ResearchIterationRecord:
        _write_json(self._dir(record.session_id, "iterations") / f"{record.iteration_index:03d}.json", record.model_dump(mode="json"))
        return record

    def list_iterations(self, session_id: str) -> list[ResearchIterationRecord]:
        return self._load_many(self._dir(session_id, "iterations"), ResearchIterationRecord)

    def save_leaderboard(self, session_id: str, leaderboard: Iterable[dict[str, Any]]) -> None:
        _write_json(self.leaderboard_path(session_id), {"leaderboard": list(leaderboard)})

    def load_leaderboard(self, session_id: str) -> list[dict[str, Any]]:
        payload = _read_json(self.leaderboard_path(session_id)) or {}
        items = payload.get("leaderboard")
        return list(items) if isinstance(items, list) else []

    def save_recommendation(self, recommendation: RecommendationBundle) -> RecommendationBundle:
        if not recommendation.session_id:
            raise ValueError("recommendation.session_id is required")
        _write_json(self.recommendation_path(recommendation.session_id), recommendation.model_dump(mode="json"))
        return recommendation

    def load_recommendation(self, session_id: str) -> RecommendationBundle | None:
        return _load_model(self.recommendation_path(session_id), RecommendationBundle)

    def save_approval(self, result: ApprovalConvergenceResult) -> ApprovalConvergenceResult:
        _write_json(self.approval_path(result.session_id), result.model_dump(mode="json"))
        return result

    def load_approval(self, session_id: str) -> ApprovalConvergenceResult | None:
        return _load_model(self.approval_path(session_id), ApprovalConvergenceResult)

    def write_artifact(
        self,
        session_id: str,
        *,
        kind: str,
        label: str,
        filename: str,
        payload: Mapping[str, Any],
        metadata: Mapping[str, Any] | None = None,
    ) -> WorkflowArtifact:
        path = self._dir(session_id, "artifacts") / filename
        _write_json(path, payload)
        return WorkflowArtifact(
            ref=ArtifactRef(
                artifact_id=f"{kind}:{label}",
                kind=kind,
                uri=str(path.resolve()),
                label=label,
                metadata=dict(metadata or {}),
            ),
            payload=dict(payload),
        )

    def _load_many(self, path: Path, model_type: type[ModelT]) -> list[ModelT]:
        if not path.exists() or not path.is_dir():
            return []
        items: list[ModelT] = []
        for item in sorted(path.glob("*.json")):
            normalized = _load_model(item, model_type)
            if normalized is not None:
                items.append(normalized)
        return items
