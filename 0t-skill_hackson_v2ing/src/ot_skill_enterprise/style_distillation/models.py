from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ot_skill_enterprise.shared.contracts.common import utc_now


@dataclass(slots=True)
class StrategyCondition:
    condition: str
    data_source: str
    weight: float = 1.0
    rationale: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition": self.condition,
            "data_source": self.data_source,
            "weight": round(float(self.weight), 4),
            "rationale": self.rationale,
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class StrategySpec:
    setup_label: str
    summary: str
    entry_conditions: tuple[StrategyCondition, ...]
    exit_conditions: dict[str, Any]
    position_sizing: dict[str, Any]
    risk_controls: tuple[str, ...] = ()
    preferred_setups: tuple[str, ...] = ()
    invalidation_rules: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "setup_label": self.setup_label,
            "summary": self.summary,
            "entry_conditions": [condition.to_dict() for condition in self.entry_conditions],
            "exit_conditions": dict(self.exit_conditions),
            "position_sizing": dict(self.position_sizing),
            "risk_controls": list(self.risk_controls),
            "preferred_setups": list(self.preferred_setups),
            "invalidation_rules": list(self.invalidation_rules),
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class ExecutionIntent:
    adapter: str
    mode: str
    preferred_workflow: str
    preflight_checks: tuple[str, ...]
    route_preferences: tuple[str, ...] = ()
    split_legs: bool = False
    leg_count: int = 1
    max_position_pct: float | None = None
    requires_explicit_approval: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "adapter": self.adapter,
            "mode": self.mode,
            "preferred_workflow": self.preferred_workflow,
            "preflight_checks": list(self.preflight_checks),
            "route_preferences": list(self.route_preferences),
            "split_legs": self.split_legs,
            "leg_count": int(self.leg_count),
            "max_position_pct": self.max_position_pct,
            "requires_explicit_approval": self.requires_explicit_approval,
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class WalletStyleProfile:
    wallet: str
    chain: str
    style_label: str
    summary: str
    confidence: float
    execution_tempo: str
    risk_appetite: str
    conviction_profile: str
    stablecoin_bias: str
    dominant_actions: tuple[str, ...] = ()
    preferred_tokens: tuple[str, ...] = ()
    active_windows: tuple[str, ...] = ()
    sizing_note: str = ""
    execution_rules: tuple[str, ...] = ()
    anti_patterns: tuple[str, ...] = ()
    prompt_focus: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "wallet": self.wallet,
            "chain": self.chain,
            "style_label": self.style_label,
            "summary": self.summary,
            "confidence": round(float(self.confidence), 4),
            "execution_tempo": self.execution_tempo,
            "risk_appetite": self.risk_appetite,
            "conviction_profile": self.conviction_profile,
            "stablecoin_bias": self.stablecoin_bias,
            "dominant_actions": list(self.dominant_actions),
            "preferred_tokens": list(self.preferred_tokens),
            "active_windows": list(self.active_windows),
            "sizing_note": self.sizing_note,
            "execution_rules": list(self.execution_rules),
            "anti_patterns": list(self.anti_patterns),
            "prompt_focus": list(self.prompt_focus),
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class StyleReviewDecision:
    status: str
    should_generate_candidate: bool
    reasoning: str
    nudge_prompt: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "should_generate_candidate": self.should_generate_candidate,
            "reasoning": self.reasoning,
            "nudge_prompt": self.nudge_prompt,
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class StyleDistillationSummary:
    job_id: str
    wallet: str
    chain: str
    target_skill_name: str
    candidate_id: str | None
    promotion_id: str | None
    summary: str
    confidence: float
    qa_status: str
    execution_readiness: str
    review_backend: str
    reflection_flow_id: str | None = None
    reflection_run_id: str | None = None
    reflection_session_id: str | None = None
    reflection_status: str | None = None
    fallback_used: bool = False
    created_at: datetime = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "wallet": self.wallet,
            "chain": self.chain,
            "target_skill_name": self.target_skill_name,
            "candidate_id": self.candidate_id,
            "promotion_id": self.promotion_id,
            "summary": self.summary,
            "confidence": round(float(self.confidence), 4),
            "qa_status": self.qa_status,
            "execution_readiness": self.execution_readiness,
            "review_backend": self.review_backend,
            "reflection_flow_id": self.reflection_flow_id,
            "reflection_run_id": self.reflection_run_id,
            "reflection_session_id": self.reflection_session_id,
            "reflection_status": self.reflection_status,
            "fallback_used": self.fallback_used,
            "created_at": self.created_at,
        }
