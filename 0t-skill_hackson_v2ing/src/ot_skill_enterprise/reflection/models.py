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


def _strings(values: Any) -> tuple[str, ...]:
    items: list[str] = []
    if values is None:
        return ()
    if isinstance(values, (str, bytes)):
        values = (values,)
    for value in values or ():
        text = str(value or "").strip()
        if text:
            items.append(text)
    return tuple(items)


def _context_sources(value: Any) -> tuple[dict[str, Any], ...]:
    sources: list[dict[str, Any]] = []
    if value is None:
        return ()
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        value = (value,)
    elif isinstance(value, (str, bytes)):
        value = ({"source_id": str(value)},)
    for item in value or ():
        if hasattr(item, "model_dump"):
            item = item.model_dump(mode="json")
        if isinstance(item, dict):
            sources.append({str(key): _json_safe(item_value) for key, item_value in item.items()})
    return tuple(sources)


def _fenced_block(kind: str, lines: tuple[str, ...]) -> str:
    body = "\n".join(lines).strip()
    if not body:
        return ""
    return f"```{kind}\n{body}\n```"


@dataclass(slots=True)
class ReflectionContextEnvelope:
    memory: tuple[str, ...] = field(default_factory=tuple)
    hints: tuple[str, ...] = field(default_factory=tuple)
    context_sources: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    context: str = ""
    hard_constraints: tuple[str, ...] = field(default_factory=tuple)
    memory_items: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    review_hints: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_value(cls, value: Any) -> "ReflectionContextEnvelope":
        if isinstance(value, cls):
            return value
        if hasattr(value, "model_dump"):
            value = value.model_dump(mode="json")
        payload = dict(value or {}) if isinstance(value, dict) else {}
        return cls(
            memory=_strings(payload.get("memory") or payload.get("memories")),
            hints=_strings(payload.get("hints") or payload.get("hint_blocks")),
            context_sources=_context_sources(payload.get("context_sources") or payload.get("sources")),
            context=str(payload.get("context") or ""),
            hard_constraints=_strings(payload.get("hard_constraints")),
            memory_items=tuple(dict(item) for item in (payload.get("memory_items") or []) if isinstance(item, dict)),
            review_hints=tuple(dict(item) for item in (payload.get("review_hints") or []) if isinstance(item, dict)),
            metadata=dict(payload.get("metadata") or {}),
        )

    @property
    def has_context(self) -> bool:
        return bool(
            self.memory
            or self.hints
            or self.context_sources
            or self.metadata
            or self.context
            or self.hard_constraints
            or self.memory_items
            or self.review_hints
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory": list(self.memory),
            "hints": list(self.hints),
            "context_sources": [_json_safe(source) for source in self.context_sources],
            "metadata": _json_safe(self.metadata),
            "context": self.context,
            "hard_constraints": list(self.hard_constraints),
            "memory_items": [_json_safe(item) for item in self.memory_items],
            "review_hints": [_json_safe(item) for item in self.review_hints],
            "fenced_blocks": {
                "memory": _fenced_block("memory", self.memory),
                "hints": _fenced_block("hint", self.hints),
            },
            "has_context": self.has_context,
        }

    def user_payload(self) -> dict[str, Any]:
        payload = self.to_dict()
        return {
            "memory": payload["memory"],
            "hints": payload["hints"],
            "context_sources": payload["context_sources"],
            "metadata": payload["metadata"],
            "context": payload["context"],
            "hard_constraints": payload["hard_constraints"],
            "memory_items": payload["memory_items"],
            "review_hints": payload["review_hints"],
            "fenced_blocks": payload["fenced_blocks"],
            "has_context": payload["has_context"],
        }


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
    injected_context: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def artifact_root_path(self) -> Path:
        return Path(self.artifact_root).expanduser().resolve()

    def injected_context_envelope(self) -> ReflectionContextEnvelope:
        return ReflectionContextEnvelope.from_value(self.injected_context)

    def context_sources(self) -> tuple[dict[str, Any], ...]:
        return self.injected_context_envelope().context_sources

    def user_payload(self) -> dict[str, Any]:
        envelope = self.injected_context_envelope()
        payload = {
            "prompt": self.prompt,
            "injected_context": envelope.user_payload(),
        }
        if envelope.has_context:
            payload["context_sources"] = [_json_safe(source) for source in envelope.context_sources]
        return payload

    def to_dict(self) -> dict[str, Any]:
        envelope = self.injected_context_envelope()
        return {
            "subject_kind": self.subject_kind,
            "subject_id": self.subject_id,
            "flow_id": self.flow_id,
            "system_prompt": self.system_prompt,
            "compact_input": _json_safe(self.compact_input),
            "expected_output_schema": _json_safe(self.expected_output_schema),
            "artifact_root": str(self.artifact_root_path),
            "prompt": self.prompt,
            "injected_context": envelope.to_dict(),
            "context_sources": [_json_safe(source) for source in envelope.context_sources],
            "user_payload": self.user_payload(),
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
