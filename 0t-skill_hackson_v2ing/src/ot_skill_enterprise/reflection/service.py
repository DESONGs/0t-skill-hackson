from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping

from ot_skill_enterprise.runtime.service import RuntimeService, build_runtime_service
from ot_skill_enterprise.style_distillation.models import (
    ExecutionIntent,
    StrategyCondition,
    StrategySpec,
    StyleReviewDecision,
    WalletStyleProfile,
)

from .models import ReflectionJobResult, ReflectionJobSpec, WalletStyleReviewReport


def _json_safe(value: Any) -> Any:
    return json.loads(
        json.dumps(
            value,
            ensure_ascii=False,
            default=lambda item: item.model_dump(mode="json") if hasattr(item, "model_dump") else str(item),
        )
    )


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _strings(values: Iterable[Any]) -> tuple[str, ...]:
    items: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text:
            items.append(text)
    return tuple(items)


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
        if isinstance(dumped, dict):
            return dict(dumped)
    return {}


def _required_text(payload: Mapping[str, Any], key: str) -> str:
    text = str(payload.get(key) or "").strip()
    if not text:
        raise ValueError(f"reflection output missing required field: {key}")
    return text


def _optional_float(value: Any, *, default: Any = 0.0) -> Any:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def build_wallet_style_output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["profile", "strategy", "execution_intent", "review"],
        "properties": {
            "profile": {
                "type": "object",
                "required": [
                    "wallet",
                    "chain",
                    "style_label",
                    "summary",
                    "confidence",
                    "execution_tempo",
                    "risk_appetite",
                    "conviction_profile",
                    "stablecoin_bias",
                ],
                "properties": {
                    "wallet": {"type": "string"},
                    "chain": {"type": "string"},
                    "style_label": {"type": "string"},
                    "summary": {"type": "string"},
                    "confidence": {"type": "number"},
                    "execution_tempo": {"type": "string"},
                    "risk_appetite": {"type": "string"},
                    "conviction_profile": {"type": "string"},
                    "stablecoin_bias": {"type": "string"},
                    "dominant_actions": {"type": "array", "items": {"type": "string"}},
                    "preferred_tokens": {"type": "array", "items": {"type": "string"}},
                    "active_windows": {"type": "array", "items": {"type": "string"}},
                    "sizing_note": {"type": "string"},
                    "execution_rules": {"type": "array", "items": {"type": "string"}},
                    "anti_patterns": {"type": "array", "items": {"type": "string"}},
                    "prompt_focus": {"type": "array", "items": {"type": "string"}},
                    "metadata": {"type": "object"},
                },
            },
            "strategy": {
                "type": "object",
                "required": ["setup_label", "summary", "entry_conditions", "exit_conditions", "position_sizing"],
                "properties": {
                    "setup_label": {"type": "string"},
                    "summary": {"type": "string"},
                    "entry_conditions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["condition", "data_source"],
                            "properties": {
                                "condition": {"type": "string"},
                                "data_source": {"type": "string"},
                                "weight": {"type": "number"},
                                "rationale": {"type": "string"},
                                "metadata": {"type": "object"},
                            },
                        },
                    },
                    "exit_conditions": {"type": "object"},
                    "position_sizing": {"type": "object"},
                    "risk_controls": {"type": "array", "items": {"type": "string"}},
                    "preferred_setups": {"type": "array", "items": {"type": "string"}},
                    "invalidation_rules": {"type": "array", "items": {"type": "string"}},
                    "metadata": {"type": "object"},
                },
            },
            "execution_intent": {
                "type": "object",
                "required": ["adapter", "mode", "preferred_workflow", "preflight_checks"],
                "properties": {
                    "adapter": {"type": "string"},
                    "mode": {"type": "string"},
                    "preferred_workflow": {"type": "string"},
                    "preflight_checks": {"type": "array", "items": {"type": "string"}},
                    "route_preferences": {"type": "array", "items": {"type": "string"}},
                    "split_legs": {"type": "boolean"},
                    "leg_count": {"type": "integer"},
                    "max_position_pct": {"type": "number"},
                    "requires_explicit_approval": {"type": "boolean"},
                    "metadata": {"type": "object"},
                },
            },
            "review": {
                "type": "object",
                "required": ["status", "should_generate_candidate", "reasoning", "nudge_prompt"],
                "properties": {
                    "status": {"type": "string"},
                    "should_generate_candidate": {"type": "boolean"},
                    "reasoning": {"type": "string"},
                    "nudge_prompt": {"type": "string"},
                    "metadata": {"type": "object"},
                },
            },
        },
    }


def parse_wallet_style_review_report(
    normalized_output: Mapping[str, Any],
    *,
    wallet: str,
    chain: str,
) -> WalletStyleReviewReport:
    payload = _mapping(normalized_output)
    profile_payload = _mapping(payload.get("profile"))
    strategy_payload = _mapping(payload.get("strategy"))
    execution_intent_payload = _mapping(payload.get("execution_intent"))
    review_payload = _mapping(payload.get("review"))
    if not profile_payload or not strategy_payload or not execution_intent_payload or not review_payload:
        raise ValueError("reflection output must include profile, strategy, execution_intent, and review objects")

    profile = WalletStyleProfile(
        wallet=str(profile_payload.get("wallet") or wallet).strip() or wallet,
        chain=str(profile_payload.get("chain") or chain).strip() or chain,
        style_label=_required_text(profile_payload, "style_label"),
        summary=_required_text(profile_payload, "summary"),
        confidence=max(0.0, min(_optional_float(profile_payload.get("confidence"), default=0.0), 1.0)),
        execution_tempo=_required_text(profile_payload, "execution_tempo"),
        risk_appetite=_required_text(profile_payload, "risk_appetite"),
        conviction_profile=_required_text(profile_payload, "conviction_profile"),
        stablecoin_bias=_required_text(profile_payload, "stablecoin_bias"),
        dominant_actions=_strings(profile_payload.get("dominant_actions") or ()),
        preferred_tokens=_strings(profile_payload.get("preferred_tokens") or ()),
        active_windows=_strings(profile_payload.get("active_windows") or ()),
        sizing_note=str(profile_payload.get("sizing_note") or "").strip(),
        execution_rules=_strings(profile_payload.get("execution_rules") or ()),
        anti_patterns=_strings(profile_payload.get("anti_patterns") or ()),
        prompt_focus=_strings(profile_payload.get("prompt_focus") or ()),
        metadata=dict(profile_payload.get("metadata") or {}),
    )
    entry_conditions = tuple(
        StrategyCondition(
            condition=_required_text(_mapping(condition), "condition"),
            data_source=_required_text(_mapping(condition), "data_source"),
            weight=_optional_float(_mapping(condition).get("weight"), default=1.0),
            rationale=str(_mapping(condition).get("rationale") or "").strip(),
            metadata=dict(_mapping(condition).get("metadata") or {}),
        )
        for condition in (strategy_payload.get("entry_conditions") or ())
        if _mapping(condition)
    )
    if not entry_conditions:
        raise ValueError("reflection output strategy.entry_conditions must not be empty")
    strategy = StrategySpec(
        setup_label=_required_text(strategy_payload, "setup_label"),
        summary=_required_text(strategy_payload, "summary"),
        entry_conditions=entry_conditions,
        exit_conditions=dict(strategy_payload.get("exit_conditions") or {}),
        position_sizing=dict(strategy_payload.get("position_sizing") or {}),
        risk_controls=_strings(strategy_payload.get("risk_controls") or ()),
        preferred_setups=_strings(strategy_payload.get("preferred_setups") or ()),
        invalidation_rules=_strings(strategy_payload.get("invalidation_rules") or ()),
        metadata=dict(strategy_payload.get("metadata") or {}),
    )
    execution_intent = ExecutionIntent(
        adapter=_required_text(execution_intent_payload, "adapter"),
        mode=_required_text(execution_intent_payload, "mode"),
        preferred_workflow=_required_text(execution_intent_payload, "preferred_workflow"),
        preflight_checks=_strings(execution_intent_payload.get("preflight_checks") or ()),
        route_preferences=_strings(execution_intent_payload.get("route_preferences") or ()),
        split_legs=bool(execution_intent_payload.get("split_legs")),
        leg_count=max(1, int(execution_intent_payload.get("leg_count") or 1)),
        max_position_pct=_optional_float(execution_intent_payload.get("max_position_pct"), default=None),  # type: ignore[arg-type]
        requires_explicit_approval=bool(
            execution_intent_payload.get("requires_explicit_approval")
            if execution_intent_payload.get("requires_explicit_approval") is not None
            else True
        ),
        metadata=dict(execution_intent_payload.get("metadata") or {}),
    )
    review = StyleReviewDecision(
        status=_required_text(review_payload, "status"),
        should_generate_candidate=bool(review_payload.get("should_generate_candidate")),
        reasoning=_required_text(review_payload, "reasoning"),
        nudge_prompt=_required_text(review_payload, "nudge_prompt"),
        metadata=dict(review_payload.get("metadata") or {}),
    )
    return WalletStyleReviewReport(
        profile=profile,
        strategy=strategy,
        execution_intent=execution_intent,
        review=review,
        normalized_output=payload,
    )


class PiReflectionService:
    def __init__(
        self,
        *,
        project_root: Path,
        workspace_root: Path,
        runtime_service: RuntimeService | None = None,
    ) -> None:
        self.project_root = Path(project_root).expanduser().resolve()
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.runtime_service = runtime_service or build_runtime_service(
            root=self.project_root,
            workspace_dir=self.workspace_root,
        )

    def run(self, spec: ReflectionJobSpec) -> ReflectionJobResult:
        artifact_root = spec.artifact_root_path
        artifact_root.mkdir(parents=True, exist_ok=True)
        request_artifact = artifact_root / "reflection_job.request.json"
        result_artifact = artifact_root / "reflection_job.result.json"
        failure_artifact = artifact_root / "reflection_job.failure.json"
        _write_json(request_artifact, spec.to_dict())
        try:
            runtime_timeout_seconds = float(str(os.environ.get("OT_PI_REFLECTION_TIMEOUT_SECONDS") or "240").strip())
        except ValueError:
            runtime_timeout_seconds = 240.0

        runtime_metadata = {
            "source": "pi-reflection-service",
            "pi_mode": "reflection",
            "flow_id": spec.flow_id,
            "subject_kind": spec.subject_kind,
            "subject_id": spec.subject_id or str(spec.compact_input.get("wallet") or spec.subject_kind),
            "agent_id": "pi-reflection-agent",
            "agent_display_name": "Pi Reflection Agent",
            "agent_execution_mode": "background-reflection",
            "reflection_context": spec.injected_context_envelope().to_dict(),
            "reflection_context_source_count": len(spec.context_sources()),
            "reflection_context_sources": [_json_safe(source) for source in spec.context_sources()],
            "reflection_context_has_content": spec.injected_context_envelope().has_context,
            "runtime_pass": True,
            "runtime_status": "succeeded",
            "runtime_summary": "Pi reflection run completed.",
            "contract_pass": True,
            "contract_summary": "Reflection run produced structured review artifacts.",
            "task_match_score": 1.0,
            "task_match_threshold": 0.5,
            "task_match_summary": "Reflection review completed.",
            "disable_candidate_generation": True,
            "runtime_timeout_seconds": runtime_timeout_seconds,
            **dict(spec.metadata or {}),
        }
        runtime_input = {
            "reflection_job": spec.runtime_payload(),
            "user_payload": spec.user_payload(),
            "injected_context": spec.injected_context_envelope().to_dict(),
        }

        try:
            run_result = self.runtime_service.run(
                runtime_id="pi",
                prompt=spec.prompt or f"Run structured reflection for {spec.subject_kind}",
                cwd=self.project_root,
                input_payload=runtime_input,
                metadata=runtime_metadata,
            )
        except Exception as exc:  # noqa: BLE001
            failure_payload = {
                "status": "failed",
                "error": str(exc),
                "request_artifact": str(request_artifact.resolve()),
            }
            _write_json(failure_artifact, failure_payload)
            return ReflectionJobResult(
                review_backend="pi-reflection-runtime",
                reflection_run_id=None,
                reflection_session_id=None,
                status="failed",
                raw_output={"error": str(exc)},
                normalized_output={},
                fallback_used=False,
                artifacts={
                    "request": str(request_artifact.resolve()),
                    "failure": str(failure_artifact.resolve()),
                },
                metadata={"error": str(exc)},
            )

        transcript_output = dict(run_result.transcript.output_payload or {})
        result_payload = {
            "status": run_result.transcript.status,
            "summary": run_result.transcript.summary,
            "review_backend": transcript_output.get("review_backend") or "pi-reflection-runtime",
            "raw_output": transcript_output.get("raw_output") or transcript_output,
            "normalized_output": transcript_output.get("normalized_output") or {},
            "runtime": run_result.as_dict(full=False),
        }
        _write_json(result_artifact, result_payload)
        return ReflectionJobResult(
            review_backend=str(result_payload["review_backend"]),
            reflection_run_id=run_result.pipeline.run.run_id,
            reflection_session_id=run_result.session.session_id,
            status=run_result.transcript.status,
            raw_output=_mapping(result_payload.get("raw_output")) or {"value": result_payload.get("raw_output")},
            normalized_output=_mapping(result_payload.get("normalized_output")),
            fallback_used=False,
            artifacts={
                "request": str(request_artifact.resolve()),
                "result": str(result_artifact.resolve()),
            },
            metadata={
                "transcript_status": run_result.transcript.status,
                "transcript_summary": run_result.transcript.summary,
                "runtime": run_result.as_dict(full=False),
            },
        )
