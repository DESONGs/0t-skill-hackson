from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ot_skill_enterprise.style_distillation.models import (
    ExecutionIntent,
    StrategySpec,
    StyleReviewDecision,
    WalletStyleProfile,
)


def _json_safe(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


@dataclass(slots=True)
class ReflectionJobSpec:
    subject_kind: str
    flow_id: str
    system_prompt: str
    compact_input: dict[str, Any]
    expected_output_schema: dict[str, Any]
    artifact_root: Path | str
    subject_id: str | None = None
    prompt: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def artifact_root_path(self) -> Path:
        return Path(self.artifact_root).expanduser().resolve()

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject_kind": self.subject_kind,
            "subject_id": self.subject_id,
            "flow_id": self.flow_id,
            "system_prompt": self.system_prompt,
            "compact_input": _json_safe(self.compact_input),
            "expected_output_schema": _json_safe(self.expected_output_schema),
            "artifact_root": str(self.artifact_root_path),
            "prompt": self.prompt,
            "metadata": _json_safe(self.metadata),
        }

    def runtime_payload(self) -> dict[str, Any]:
        return self.to_dict()


@dataclass(slots=True)
class ReflectionJobResult:
    review_backend: str
    reflection_run_id: str | None
    reflection_session_id: str | None
    status: str
    raw_output: dict[str, Any]
    normalized_output: dict[str, Any]
    fallback_used: bool
    artifacts: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "review_backend": self.review_backend,
            "reflection_run_id": self.reflection_run_id,
            "reflection_session_id": self.reflection_session_id,
            "status": self.status,
            "raw_output": _json_safe(self.raw_output),
            "normalized_output": _json_safe(self.normalized_output),
            "fallback_used": self.fallback_used,
            "artifacts": dict(self.artifacts),
            "metadata": _json_safe(self.metadata),
        }


@dataclass(slots=True)
class WalletStyleReviewReport:
    profile: WalletStyleProfile
    strategy: StrategySpec
    execution_intent: ExecutionIntent
    review: StyleReviewDecision
    normalized_output: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile.to_dict(),
            "strategy": self.strategy.to_dict(),
            "execution_intent": self.execution_intent.to_dict(),
            "review": self.review.to_dict(),
            "normalized_output": _json_safe(self.normalized_output),
        }
