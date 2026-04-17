from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping

from ot_skill_enterprise.runtime.service import RuntimeService, build_runtime_service
from ot_skill_enterprise.style_distillation.extractors import WalletStyleExtractor
from ot_skill_enterprise.style_distillation.models import (
    ExecutionIntent,
    StrategyCondition,
    StrategySpec,
    StyleReviewDecision,
    WalletStyleProfile,
)
from ot_skill_enterprise.style_distillation.reflection_builders import (
    build_fallback_execution_intent,
    build_fallback_strategy_spec,
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


_GENERIC_STYLE_LABELS = {"balanced", "default", "generic", "neutral"}
_GENERIC_SETUP_LABELS = {"balanced", "default", "generic", "neutral"}
_GENERIC_SUMMARY_MARKERS = (
    "balanced risk profile with moderate conviction",
    "standard entry and exit strategy",
)
_GENERIC_ENTRY_CONDITIONS = {"price above support"}
_REVIEW_STATUS_ALIASES = {
    "low_confidence": "generate_with_low_confidence",
    "manual-review": "needs_manual_review",
    "manual_review": "needs_manual_review",
    "no_pattern": "no_pattern_detected",
    "runtime_error": "runtime_failed",
}
_GENERATING_REVIEW_STATUSES = {"generate", "generate_with_low_confidence"}
_NON_GENERATING_REVIEW_STATUSES = {
    "insufficient_signal",
    "needs_manual_review",
    "no_pattern_detected",
    "runtime_failed",
}
_ALL_REVIEW_STATUSES = _GENERATING_REVIEW_STATUSES | _NON_GENERATING_REVIEW_STATUSES
_LOW_SIGNAL_REVIEW_STATUSES = {
    "insufficient_signal",
    "needs_manual_review",
    "no_pattern_detected",
}

_REFLECTION_FAILURE_TYPES = {
    "runtime_abort",
    "runtime_timeout",
    "provider_unavailable",
    "empty_output",
    "json_parse_failed",
    "schema_rejected",
    "generic_rejected",
}


def _lower_text(value: Any) -> str:
    return str(value or "").strip().lower()


def _string_or_none(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _is_generic_summary(text: Any) -> bool:
    lowered = _lower_text(text)
    return any(marker in lowered for marker in _GENERIC_SUMMARY_MARKERS)


def _normalize_review_status(value: Any, *, should_generate_candidate: Any = None) -> str:
    normalized = _lower_text(value)
    normalized = _REVIEW_STATUS_ALIASES.get(normalized, normalized)
    if normalized in _ALL_REVIEW_STATUSES:
        return normalized
    return "generate" if bool(should_generate_candidate) else "needs_manual_review"


def _review_should_generate(status: str) -> bool:
    return status in _GENERATING_REVIEW_STATUSES


def _default_entry_condition_source(*, review_status: str) -> str:
    if review_status in _LOW_SIGNAL_REVIEW_STATUSES:
        return "reflection.review.status"
    if review_status == "runtime_failed":
        return "reflection.runtime.status"
    return "reflection.strategy.entry_conditions"


def _low_signal_entry_condition(*, review_status: str) -> StrategyCondition:
    return StrategyCondition(
        condition=f"preserve evidence only; review.status == '{review_status}'",
        data_source=_default_entry_condition_source(review_status=review_status),
        weight=0.0,
        rationale="Low-signal reflection output should remain a successful analysis without generating a candidate skill.",
        metadata={"synthetic": True, "review_status": review_status},
    )


def _collect_archetype_metadata(payload: Mapping[str, Any]) -> dict[str, Any]:
    nested = _mapping(payload.get("archetype"))
    metadata: dict[str, Any] = {}
    for source in (nested, payload):
        for source_key, target_key in (
            ("trading_archetype", "primary_archetype"),
            ("primary_archetype", "primary_archetype"),
            ("primary_label", "primary_archetype"),
            ("secondary_archetypes", "secondary_archetypes"),
            ("behavioral_patterns", "behavioral_patterns"),
            ("archetype_confidence", "archetype_confidence"),
            ("confidence", "archetype_confidence"),
            ("archetype_evidence_summary", "archetype_evidence_summary"),
            ("evidence", "archetype_evidence_summary"),
        ):
            if source_key not in source or target_key in metadata:
                continue
            value = source.get(source_key)
            if isinstance(value, list):
                cleaned = [str(item).strip() for item in value if str(item).strip()]
                if cleaned:
                    metadata[target_key] = cleaned
            elif isinstance(value, str) and value.strip():
                metadata[target_key] = value.strip()
            elif value not in (None, "", (), {}):
                metadata[target_key] = _json_safe(value)
    return metadata


def _extract_attempts(value: Any) -> tuple[dict[str, Any], ...]:
    if value is None:
        return ()
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        value = (value,)
    attempts: list[dict[str, Any]] = []
    for item in value or ():
        if hasattr(item, "model_dump"):
            item = item.model_dump(mode="json")
        if isinstance(item, dict):
            attempts.append({str(key): _json_safe(item_value) for key, item_value in item.items()})
    return tuple(attempts)


def _extract_model_identity(*payloads: Mapping[str, Any]) -> tuple[str | None, str | None]:
    for payload in payloads:
        model_payload = _mapping(payload.get("model"))
        provider = _string_or_none(model_payload.get("provider") or payload.get("provider"))
        model_id = _string_or_none(
            model_payload.get("model_id")
            or model_payload.get("id")
            or payload.get("model_id")
            or payload.get("model")
        )
        if provider or model_id:
            return provider, model_id
    return None, None


def _classify_failure_type(
    message: str,
    *,
    stage: str | None = None,
    transcript_output: Mapping[str, Any] | None = None,
    raw_output: Mapping[str, Any] | None = None,
) -> str:
    for payload in (transcript_output, raw_output):
        if not payload:
            continue
        failure_type = _string_or_none(payload.get("failure_type"))
        if failure_type in _REFLECTION_FAILURE_TYPES:
            return failure_type

    lowered = _lower_text(message)
    if stage in _REFLECTION_FAILURE_TYPES:
        return stage
    if any(marker in lowered for marker in ("timed out", "timeout", "request timed out")):
        return "runtime_timeout"
    if any(marker in lowered for marker in ("aborted", "cancelled", "canceled", "terminated", "signal")):
        return "runtime_abort"
    if any(marker in lowered for marker in ("api key", "unauthorized", "forbidden", "provider unavailable", "provider auth", "no pi model", "no model")):
        return "provider_unavailable"
    if any(marker in lowered for marker in ("no extractable content", "empty output", "stdout is empty", "no output")):
        return "empty_output"
    if stage == "json_parse_failed" or any(marker in lowered for marker in ("json", "parse failed", "not valid json", "failed to parse")):
        return "json_parse_failed"
    if stage == "schema_rejected" or any(marker in lowered for marker in ("schema", "required field", "must include", "must not be empty", "too generic")):
        return "schema_rejected"
    return "generic_rejected"


class ReflectionQualityError(ValueError):
    """Raised when reflection returns structurally valid but strategically unusable output."""


def build_wallet_style_output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["wallet", "chain", "summary", "review_status", "reasoning"],
        "properties": {
            "wallet": {"type": "string"},
            "chain": {"type": "string"},
            "style_label": {"type": "string"},
            "summary": {"type": "string"},
            "primary_archetype": {"type": "string"},
            "secondary_archetypes": {"type": "array", "items": {"type": "string"}},
            "behavioral_patterns": {
                "type": "array",
                "items": {
                    "anyOf": [
                        {"type": "string"},
                        {
                            "type": "object",
                            "properties": {
                                "pattern_label": {"type": "string"},
                                "strength": {"type": "number"},
                                "evidence": {"type": "array", "items": {"type": "string"}},
                            },
                        },
                    ],
                },
            },
            "archetype_confidence": {"type": "number"},
            "archetype_evidence_summary": {"type": "array", "items": {"type": "string"}},
            "dominant_actions": {"type": "array", "items": {"type": "string"}},
            "preferred_tokens": {"type": "array", "items": {"type": "string"}},
            "active_windows": {"type": "array", "items": {"type": "string"}},
            "risk_flags": {"type": "array", "items": {"type": "string"}},
            "setup_label": {"type": "string"},
            "setup_summary": {"type": "string"},
            "entry_signals": {"type": "array", "items": {"type": "string"}},
            "prompt_focus": {"type": "array", "items": {"type": "string"}},
            "review_status": {"type": "string"},
            "should_generate_candidate": {"type": "boolean"},
            "reasoning": {"type": "string"},
            "nudge_prompt": {"type": "string"},
            "metadata": {"type": "object"},
        },
    }


def _string_list(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)):
        return _strings((value,))
    if isinstance(value, dict):
        return ()
    return _strings(value)


def _specific_text(
    value: Any,
    *,
    generic_labels: set[str] | None = None,
    reject_generic_summary: bool = False,
) -> str | None:
    text = _string_or_none(value)
    if text is None:
        return None
    lowered = _lower_text(text)
    if generic_labels and lowered in generic_labels:
        return None
    if reject_generic_summary and _is_generic_summary(text):
        return None
    return text


def _pattern_labels(value: Any) -> tuple[str, ...]:
    labels: list[str] = []
    for item in value or ():
        if isinstance(item, dict):
            label = _specific_text(item.get("pattern_label"))
        else:
            label = _specific_text(item)
        if label:
            labels.append(label)
    return tuple(labels)


def _has_full_reflection_sections(payload: Mapping[str, Any]) -> bool:
    # Legacy compatibility path for older full-contract reflection payloads.
    return bool(_mapping(payload.get("profile")) and _mapping(payload.get("strategy")) and _mapping(payload.get("review")))


def _legacy_reflection_payload_to_minimal(payload: Mapping[str, Any]) -> dict[str, Any]:
    profile_payload = _mapping(payload.get("profile"))
    strategy_payload = _mapping(payload.get("strategy"))
    review_payload = _mapping(payload.get("review"))
    archetype_metadata = {
        **_collect_archetype_metadata(profile_payload),
        **_collect_archetype_metadata(strategy_payload),
    }
    return {
        "wallet": profile_payload.get("wallet"),
        "chain": profile_payload.get("chain"),
        "style_label": profile_payload.get("style_label"),
        "summary": profile_payload.get("summary"),
        "primary_archetype": archetype_metadata.get("primary_archetype"),
        "secondary_archetypes": archetype_metadata.get("secondary_archetypes"),
        "behavioral_patterns": archetype_metadata.get("behavioral_patterns"),
        "archetype_confidence": archetype_metadata.get("archetype_confidence"),
        "archetype_evidence_summary": archetype_metadata.get("archetype_evidence_summary"),
        "dominant_actions": profile_payload.get("dominant_actions"),
        "preferred_tokens": profile_payload.get("preferred_tokens"),
        "active_windows": profile_payload.get("active_windows"),
        "risk_flags": list(strategy_payload.get("risk_controls") or ()) or list(profile_payload.get("anti_patterns") or ()),
        "setup_label": strategy_payload.get("setup_label"),
        "setup_summary": strategy_payload.get("summary"),
        "entry_signals": [
            dict(item).get("condition")
            for item in strategy_payload.get("entry_conditions") or ()
            if isinstance(item, dict) and str(dict(item).get("condition") or "").strip()
        ],
        "prompt_focus": profile_payload.get("prompt_focus") or _mapping(review_payload.get("metadata")).get("prompt_focus"),
        "review_status": review_payload.get("status"),
        "should_generate_candidate": review_payload.get("should_generate_candidate"),
        "reasoning": review_payload.get("reasoning"),
        "nudge_prompt": review_payload.get("nudge_prompt"),
        "metadata": {
            "source_contract": "legacy_full",
            **dict(payload.get("metadata") or {}),
        },
    }


def _build_minimal_wallet_style_review_report(
    normalized_output: Mapping[str, Any],
    *,
    wallet: str,
    chain: str,
    preprocessed: Mapping[str, Any] | None,
    prompt: str | None,
    execution_intent: ExecutionIntent | None = None,
    source_contract: str = "minimal_distill",
) -> WalletStyleReviewReport:
    if preprocessed is None:
        raise ValueError("minimal reflection output requires preprocessed wallet payload")

    payload = _mapping(normalized_output)
    extractor = WalletStyleExtractor()
    base_profile, base_review = extractor.extract(dict(preprocessed), system_prompt=prompt)
    auto_fixes: list[dict[str, str]] = []

    output_wallet = _string_or_none(payload.get("wallet"))
    output_chain = _string_or_none(payload.get("chain"))
    if output_wallet and output_wallet != wallet:
        auto_fixes.append({"field": "wallet", "from": output_wallet, "to": wallet})
    if output_chain and output_chain != chain:
        auto_fixes.append({"field": "chain", "from": output_chain, "to": chain})

    review_status_input = payload.get("review_status") if payload.get("review_status") is not None else payload.get("status")
    review_status = _normalize_review_status(
        review_status_input if review_status_input is not None else base_review.status,
        should_generate_candidate=payload.get("should_generate_candidate"),
    )
    should_generate_candidate = _review_should_generate(review_status)
    if payload.get("should_generate_candidate") is not None and bool(payload.get("should_generate_candidate")) != should_generate_candidate:
        auto_fixes.append(
            {
                "field": "should_generate_candidate",
                "from": str(bool(payload.get("should_generate_candidate"))).lower(),
                "to": str(should_generate_candidate).lower(),
            }
        )

    pattern_labels = _pattern_labels(payload.get("behavioral_patterns"))
    archetype_metadata = dict(base_profile.metadata.get("archetype") or {})
    archetype_metadata.update(_collect_archetype_metadata(payload))
    if pattern_labels:
        archetype_metadata["behavioral_patterns"] = list(pattern_labels)

    dominant_actions = _string_list(payload.get("dominant_actions")) or tuple(base_profile.dominant_actions)
    preferred_tokens = _string_list(payload.get("preferred_tokens")) or tuple(base_profile.preferred_tokens)
    active_windows = _string_list(payload.get("active_windows")) or tuple(base_profile.active_windows)
    prompt_focus = _string_list(payload.get("prompt_focus")) or tuple(base_profile.prompt_focus)
    risk_flags = _string_list(payload.get("risk_flags"))
    entry_signals = _string_list(payload.get("entry_signals"))
    structured_signal_count = sum(
        1
        for item in (
            _specific_text(payload.get("style_label"), generic_labels=_GENERIC_STYLE_LABELS),
            _specific_text(payload.get("primary_archetype"), generic_labels=_GENERIC_STYLE_LABELS),
            pattern_labels,
            dominant_actions,
            preferred_tokens,
            active_windows,
            risk_flags,
            _specific_text(payload.get("setup_label"), generic_labels=_GENERIC_SETUP_LABELS),
            _specific_text(payload.get("setup_summary"), reject_generic_summary=True),
            entry_signals,
        )
        if item
    )

    style_label = (
        _specific_text(payload.get("style_label"), generic_labels=_GENERIC_STYLE_LABELS)
        or _specific_text(payload.get("primary_archetype"), generic_labels=_GENERIC_STYLE_LABELS)
        or str(base_profile.style_label)
    )
    summary_override = _specific_text(payload.get("summary"), reject_generic_summary=True)
    if summary_override is None and source_contract == "minimal_distill":
        raise ValueError("minimal reflection output missing required field: summary")
    summary = summary_override or str(base_profile.summary)
    confidence = max(
        0.0,
        min(
            _optional_float(
                payload.get("confidence") if payload.get("confidence") is not None else payload.get("archetype_confidence"),
                default=base_profile.confidence,
            ),
            1.0,
        ),
    )

    profile_metadata = {
        **dict(base_profile.metadata or {}),
        "archetype": archetype_metadata,
        "reflection_contract": source_contract,
    }
    if risk_flags:
        profile_metadata["risk_flags"] = list(risk_flags)

    profile = WalletStyleProfile(
        wallet=wallet,
        chain=chain,
        style_label=style_label,
        summary=summary,
        confidence=confidence,
        execution_tempo=base_profile.execution_tempo,
        risk_appetite=base_profile.risk_appetite,
        conviction_profile=base_profile.conviction_profile,
        stablecoin_bias=base_profile.stablecoin_bias,
        dominant_actions=dominant_actions,
        preferred_tokens=preferred_tokens,
        active_windows=active_windows,
        sizing_note=base_profile.sizing_note,
        execution_rules=tuple(base_profile.execution_rules),
        anti_patterns=tuple(base_profile.anti_patterns),
        prompt_focus=prompt_focus,
        metadata=profile_metadata,
    )

    strategy = build_fallback_strategy_spec(dict(preprocessed), profile.to_dict())
    setup_label = _specific_text(payload.get("setup_label"), generic_labels=_GENERIC_SETUP_LABELS)
    setup_summary = _specific_text(payload.get("setup_summary"), reject_generic_summary=True)
    if review_status in _LOW_SIGNAL_REVIEW_STATUSES:
        strategy.setup_label = setup_label or "evidence-only"
        strategy.summary = setup_summary or "Current evidence is insufficient to define a reliable automated setup."
        strategy.entry_conditions = (_low_signal_entry_condition(review_status=review_status),)
    else:
        if setup_label:
            strategy.setup_label = setup_label
        if setup_summary:
            strategy.summary = setup_summary
        if entry_signals:
            signal_conditions = tuple(
                StrategyCondition(
                    condition=signal,
                    data_source="reflection.entry_signals",
                    weight=0.6,
                    rationale="Recovered from minimal reflection distill output.",
                    metadata={"source_contract": source_contract},
                )
                for signal in entry_signals[:2]
            )
            strategy.entry_conditions = signal_conditions + tuple(strategy.entry_conditions)
    if review_status == "runtime_failed":
        strategy.entry_conditions = (_low_signal_entry_condition(review_status=review_status),)

    strategy.metadata = {
        **dict(strategy.metadata or {}),
        "reflection_contract": source_contract,
        "reflection_entry_signals": list(entry_signals),
    }
    if risk_flags:
        strategy.risk_controls = tuple(dict.fromkeys((*strategy.risk_controls, *risk_flags)))
        strategy.metadata["risk_flags"] = list(risk_flags)

    resolved_execution_intent = execution_intent or build_fallback_execution_intent(dict(preprocessed), strategy)
    reasoning_override = _specific_text(payload.get("reasoning"))
    if reasoning_override is None and source_contract == "minimal_distill" and structured_signal_count < 3:
        raise ValueError("minimal reflection output missing required field: reasoning")
    if review_status_input is None and source_contract == "minimal_distill" and structured_signal_count < 3:
        raise ValueError("minimal reflection output missing required field: review_status")
    if review_status_input is None and structured_signal_count >= 3:
        auto_fixes.append({"field": "review_status", "from": "", "to": base_review.status})
    if reasoning_override is None and structured_signal_count >= 3:
        auto_fixes.append({"field": "reasoning", "from": "", "to": base_review.reasoning})
    reasoning = reasoning_override or str(base_review.reasoning)
    nudge_prompt = _specific_text(payload.get("nudge_prompt")) or str(base_review.nudge_prompt)
    review_metadata = {
        **dict(base_review.metadata or {}),
        "reflection_contract": source_contract,
        "distill_output": _json_safe(payload),
    }
    if risk_flags:
        review_metadata["risk_flags"] = list(risk_flags)
    review = StyleReviewDecision(
        status=review_status,
        should_generate_candidate=False if review_status in _NON_GENERATING_REVIEW_STATUSES else should_generate_candidate,
        reasoning=reasoning,
        nudge_prompt=nudge_prompt,
        metadata=review_metadata,
    )

    assembled_payload = {
        "profile": profile.to_dict(),
        "strategy": strategy.to_dict(),
        "execution_intent": resolved_execution_intent.to_dict(),
        "review": review.to_dict(),
        "metadata": {
            "reflection_contract": source_contract,
            "distill_output": _json_safe(payload),
        },
    }
    if auto_fixes:
        assembled_payload["metadata"]["_auto_fixes"] = auto_fixes

    return WalletStyleReviewReport(
        profile=profile,
        strategy=strategy,
        execution_intent=resolved_execution_intent,
        review=review,
        normalized_output=assembled_payload,
    )


def parse_wallet_style_review_report(
    normalized_output: Mapping[str, Any],
    *,
    wallet: str,
    chain: str,
    execution_intent: ExecutionIntent | None = None,
    preprocessed: Mapping[str, Any] | None = None,
    prompt: str | None = None,
) -> WalletStyleReviewReport:
    payload = _mapping(normalized_output)
    if not _has_full_reflection_sections(payload):
        return _build_minimal_wallet_style_review_report(
            payload,
            wallet=wallet,
            chain=chain,
            preprocessed=preprocessed,
            prompt=prompt,
            execution_intent=execution_intent,
        )

    profile_payload = _mapping(payload.get("profile"))
    strategy_payload = _mapping(payload.get("strategy"))
    review_payload = _mapping(payload.get("review"))

    auto_fixes: list[dict[str, str]] = []

    output_wallet = str(profile_payload.get("wallet") or "").strip()
    output_chain = str(profile_payload.get("chain") or "").strip()
    resolved_wallet = wallet
    resolved_chain = chain
    if output_wallet and output_wallet != wallet:
        auto_fixes.append({"field": "profile.wallet", "from": output_wallet, "to": wallet})
    if output_chain and output_chain != chain:
        auto_fixes.append({"field": "profile.chain", "from": output_chain, "to": chain})

    review_status = _normalize_review_status(
        review_payload.get("status"),
        should_generate_candidate=review_payload.get("should_generate_candidate"),
    )
    should_generate_candidate = _review_should_generate(review_status)
    if str(review_payload.get("status") or "").strip() and review_status != _lower_text(review_payload.get("status")):
        auto_fixes.append(
            {
                "field": "review.status",
                "from": str(review_payload.get("status") or "").strip(),
                "to": review_status,
            }
        )
    if bool(review_payload.get("should_generate_candidate")) != should_generate_candidate:
        auto_fixes.append(
            {
                "field": "review.should_generate_candidate",
                "from": str(bool(review_payload.get("should_generate_candidate"))).lower(),
                "to": str(should_generate_candidate).lower(),
            }
        )

    profile_metadata = {
        **dict(profile_payload.get("metadata") or {}),
        **_collect_archetype_metadata(profile_payload),
    }
    strategy_metadata = {
        **dict(strategy_payload.get("metadata") or {}),
        **_collect_archetype_metadata(strategy_payload),
    }
    review_metadata = dict(review_payload.get("metadata") or {})

    profile = WalletStyleProfile(
        wallet=resolved_wallet,
        chain=resolved_chain,
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
        metadata=profile_metadata,
    )
    entry_conditions_items: list[StrategyCondition] = []
    for raw_condition in (strategy_payload.get("entry_conditions") or ()):
        condition_payload = _mapping(raw_condition)
        if not condition_payload:
            continue
        condition_text = _required_text(condition_payload, "condition")
        data_source = str(condition_payload.get("data_source") or "").strip()
        if not data_source:
            data_source = _default_entry_condition_source(review_status=review_status)
            auto_fixes.append(
                {
                    "field": "strategy.entry_conditions[].data_source",
                    "from": "",
                    "to": data_source,
                }
            )
        entry_conditions_items.append(
            StrategyCondition(
                condition=condition_text,
                data_source=data_source,
                weight=_optional_float(condition_payload.get("weight"), default=1.0),
                rationale=str(condition_payload.get("rationale") or "").strip(),
                metadata=dict(condition_payload.get("metadata") or {}),
            )
        )
    if not entry_conditions_items and review_status in _LOW_SIGNAL_REVIEW_STATUSES:
        auto_fixes.append(
            {
                "field": "strategy.entry_conditions",
                "from": "[]",
                "to": "synthetic_low_signal_condition",
            }
        )
        entry_conditions_items.append(_low_signal_entry_condition(review_status=review_status))
    entry_conditions = tuple(entry_conditions_items)
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
        metadata=strategy_metadata,
    )
    resolved_execution_intent = execution_intent
    if resolved_execution_intent is None:
        if preprocessed is None:
            raise ValueError("execution_intent must be provided by caller")
        resolved_execution_intent = build_fallback_execution_intent(dict(preprocessed), strategy)
    review = StyleReviewDecision(
        status=review_status,
        should_generate_candidate=should_generate_candidate,
        reasoning=_required_text(review_payload, "reasoning"),
        nudge_prompt=_required_text(review_payload, "nudge_prompt"),
        metadata=review_metadata,
    )

    profile_is_generic = _lower_text(profile.style_label) in _GENERIC_STYLE_LABELS or _is_generic_summary(profile.summary)
    missing_profile_behaviors = not profile.dominant_actions or not profile.preferred_tokens or not profile.execution_rules
    strategy_is_generic = _lower_text(strategy.setup_label) in _GENERIC_SETUP_LABELS or _is_generic_summary(strategy.summary)
    generic_entry_conditions = [
        condition
        for condition in strategy.entry_conditions
        if _lower_text(condition.condition) in _GENERIC_ENTRY_CONDITIONS
        or _lower_text(condition.data_source) in {"", "onchain"}
    ]
    quality_warnings: list[str] = []
    if profile_is_generic:
        quality_warnings.append("profile_generic")
    if missing_profile_behaviors:
        quality_warnings.append("profile_behaviors_missing")
    if strategy_is_generic:
        quality_warnings.append("strategy_generic")
    if generic_entry_conditions:
        quality_warnings.append("entry_conditions_generic")

    severe_generic_failure = review.status in _GENERATING_REVIEW_STATUSES and (
        (profile_is_generic and strategy_is_generic and len(generic_entry_conditions) == len(strategy.entry_conditions))
        or (missing_profile_behaviors and (profile_is_generic or strategy_is_generic))
    )
    if severe_generic_failure:
        if preprocessed is not None:
            return _build_minimal_wallet_style_review_report(
                _legacy_reflection_payload_to_minimal(payload),
                wallet=wallet,
                chain=chain,
                preprocessed=preprocessed,
                prompt=prompt,
                execution_intent=execution_intent,
                source_contract="legacy_full_normalized",
            )
        raise ReflectionQualityError("reflection output remained too generic after normalization")

    if quality_warnings and review.status == "generate":
        auto_fixes.append(
            {
                "field": "review.status",
                "from": "generate",
                "to": "generate_with_low_confidence",
            }
        )
        review.status = "generate_with_low_confidence"
        review.should_generate_candidate = True

    if review.status in _LOW_SIGNAL_REVIEW_STATUSES and review.should_generate_candidate:
        review.should_generate_candidate = False

    if review.status in _LOW_SIGNAL_REVIEW_STATUSES and not strategy.entry_conditions:
        strategy.entry_conditions = (_low_signal_entry_condition(review_status=review.status),)

    if review.status == "runtime_failed" and review.should_generate_candidate:
        review.should_generate_candidate = False

    if any(
        _lower_text(condition.condition) in _GENERIC_ENTRY_CONDITIONS
        or _lower_text(condition.data_source) in {"", "onchain"}
        for condition in strategy.entry_conditions
    ) and review.status not in _LOW_SIGNAL_REVIEW_STATUSES and severe_generic_failure:
        raise ReflectionQualityError("reflection output strategy.entry_conditions are too generic")

    if quality_warnings:
        profile.metadata["reflection_quality_warnings"] = list(quality_warnings)
        strategy.metadata["reflection_quality_warnings"] = list(quality_warnings)
        review.metadata["reflection_quality_warnings"] = list(quality_warnings)
    if auto_fixes:
        metadata = dict(payload.get("metadata") or {})
        metadata.setdefault("_auto_fixes", auto_fixes)
        payload["metadata"] = metadata
    if quality_warnings:
        metadata = dict(payload.get("metadata") or {})
        metadata.setdefault("_quality_warnings", quality_warnings)
        payload["metadata"] = metadata
    payload["profile"] = profile.to_dict()
    payload["strategy"] = strategy.to_dict()
    payload["review"] = review.to_dict()
    return WalletStyleReviewReport(
        profile=profile,
        strategy=strategy,
        execution_intent=resolved_execution_intent,
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
            runtime_timeout_seconds = float(str(os.environ.get("OT_PI_REFLECTION_TIMEOUT_SECONDS") or "180").strip())
        except ValueError:
            runtime_timeout_seconds = 180.0
        try:
            request_timeout_seconds = float(
                str(os.environ.get("OT_PI_REFLECTION_REQUEST_TIMEOUT_SECONDS") or max(45.0, min(runtime_timeout_seconds - 15.0, 75.0))).strip()
            )
        except ValueError:
            request_timeout_seconds = max(45.0, min(runtime_timeout_seconds - 15.0, 75.0))
        try:
            max_tokens = int(str(os.environ.get("OT_PI_REFLECTION_MAX_TOKENS") or "3500").strip())
        except ValueError:
            max_tokens = 3500

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
            "reflection_request_timeout_seconds": request_timeout_seconds,
            "reflection_max_tokens": max_tokens,
            **dict(spec.metadata or {}),
        }
        runtime_input = {
            "reflection_job": spec.runtime_payload(),
            "user_payload": spec.user_payload(),
            "injected_context": spec.injected_context_envelope().to_dict(),
        }

        def _result_model(payload: Mapping[str, Any]) -> tuple[str | None, str | None]:
            return _extract_model_identity(payload, _mapping(payload.get("raw_output")))

        def _build_job_result(
            *,
            run_result: Any | None,
            payload: Mapping[str, Any],
            failure_type: str | None,
            status: str,
            error: str | None = None,
            failure_artifact_path: str | None = None,
            result_artifact_path: str | None = None,
        ) -> ReflectionJobResult:
            raw_output = _mapping(payload.get("raw_output")) if payload.get("raw_output") is not None else {}
            normalized_output = _mapping(payload.get("normalized_output")) if payload.get("normalized_output") is not None else {}
            attempts = _extract_attempts(payload.get("attempts") or raw_output.get("attempts"))
            provider, model_id = _result_model(payload)
            raw_text = _string_or_none(payload.get("raw_text") or raw_output.get("raw_text") or raw_output.get("text"))
            metadata = {
                "transcript_status": status,
                "transcript_summary": payload.get("summary") or (run_result.transcript.summary if run_result is not None else ""),
                "runtime": run_result.as_dict(full=False) if run_result is not None else {},
                "failure_type": failure_type,
                "provider": provider,
                "model_id": model_id,
                "model": f"{provider}/{model_id}" if provider and model_id else None,
                "raw_text_salvaged": bool(raw_text),
                "attempt_count": len(attempts),
                "attempts": attempts,
            }
            if error is not None:
                metadata["error"] = error
            if failure_artifact_path is not None:
                metadata["failure_artifact"] = failure_artifact_path
            if result_artifact_path is not None:
                metadata["result_artifact"] = result_artifact_path
            return ReflectionJobResult(
                review_backend=str(payload.get("review_backend") or "pi-reflection-runtime"),
                reflection_run_id=run_result.pipeline.run.run_id if run_result is not None else None,
                reflection_session_id=run_result.session.session_id if run_result is not None else None,
                status=status,
                raw_output=raw_output or {"error": error or "", "raw_text": raw_text},
                normalized_output=normalized_output,
                fallback_used=False,
                artifacts={
                    "request": str(request_artifact.resolve()),
                    **({"failure": failure_artifact_path} if failure_artifact_path else {}),
                    **({"result": result_artifact_path} if result_artifact_path else {}),
                },
                metadata=metadata,
                failure_type=failure_type,
                provider=provider,
                model_id=model_id,
                model=f"{provider}/{model_id}" if provider and model_id else None,
                raw_text=raw_text,
                raw_text_salvaged=bool(raw_text),
                runtime_fallback_used=len(attempts) > 1,
                attempts=attempts,
            )

        try:
            run_result = self.runtime_service.run(
                runtime_id="pi",
                prompt=spec.prompt or f"Run structured reflection for {spec.subject_kind}",
                cwd=self.project_root,
                input_payload=runtime_input,
                metadata=runtime_metadata,
            )
        except Exception as exc:  # noqa: BLE001
            error_message = str(exc)
            failure_type = _classify_failure_type(error_message)
            failure_payload = {
                "status": "failed",
                "error": error_message,
                "failure_type": failure_type,
                "request_artifact": str(request_artifact.resolve()),
            }
            _write_json(failure_artifact, failure_payload)
            return _build_job_result(
                run_result=None,
                payload={
                    "review_backend": "pi-reflection-runtime",
                    "raw_output": {"error": error_message, "failure_type": failure_type},
                    "normalized_output": {},
                    "summary": error_message,
                },
                failure_type=failure_type,
                status="failed",
                error=error_message,
                failure_artifact_path=str(failure_artifact.resolve()),
            )

        transcript_output = dict(run_result.transcript.output_payload or {})
        failure_type = _string_or_none(transcript_output.get("failure_type"))
        if failure_type not in _REFLECTION_FAILURE_TYPES:
            failure_type = _classify_failure_type(
                run_result.transcript.summary or str(transcript_output.get("error") or ""),
                transcript_output=transcript_output,
                raw_output=_mapping(transcript_output.get("raw_output")),
            )
        raw_output_payload = _mapping(transcript_output.get("raw_output")) or transcript_output
        normalized_output_payload = _mapping(transcript_output.get("normalized_output"))
        provider, model_id = _extract_model_identity(raw_output_payload, transcript_output)
        attempts = _extract_attempts(
            transcript_output.get("attempts") or raw_output_payload.get("attempts") or runtime_metadata.get("reflection_attempts")
        )
        raw_text = _string_or_none(
            raw_output_payload.get("raw_text")
            or raw_output_payload.get("text")
            or transcript_output.get("raw_text")
            or transcript_output.get("text")
        )

        if not run_result.transcript.ok or run_result.transcript.status in {"failed", "error"} or failure_type in _REFLECTION_FAILURE_TYPES:
            failure_payload = {
                "status": run_result.transcript.status,
                "summary": run_result.transcript.summary,
                "failure_type": failure_type,
                "review_backend": transcript_output.get("review_backend") or "pi-reflection-runtime",
                "provider": provider,
                "model_id": model_id,
                "model": f"{provider}/{model_id}" if provider and model_id else None,
                "raw_text": raw_text,
                "raw_text_salvaged": bool(raw_text),
                "attempts": attempts,
                "raw_output": raw_output_payload,
                "normalized_output": normalized_output_payload,
                "runtime": run_result.as_dict(full=False),
            }
            _write_json(failure_artifact, failure_payload)
            return _build_job_result(
                run_result=run_result,
                payload=failure_payload,
                failure_type=failure_type,
                status=run_result.transcript.status,
                error=run_result.transcript.summary,
                failure_artifact_path=str(failure_artifact.resolve()),
            )

        result_payload = {
            "status": run_result.transcript.status,
            "summary": run_result.transcript.summary,
            "review_backend": transcript_output.get("review_backend") or "pi-reflection-runtime",
            "raw_output": {
                **raw_output_payload,
                "failure_type": None,
                "provider": provider,
                "model_id": model_id,
                "model": f"{provider}/{model_id}" if provider and model_id else None,
                "raw_text": raw_text,
                "raw_text_salvaged": bool(raw_text),
                "attempts": attempts,
            },
            "normalized_output": normalized_output_payload,
            "runtime": run_result.as_dict(full=False),
            "failure_type": None,
            "provider": provider,
            "model_id": model_id,
            "model": f"{provider}/{model_id}" if provider and model_id else None,
            "raw_text": raw_text,
            "raw_text_salvaged": bool(raw_text),
            "attempts": attempts,
        }
        _write_json(result_artifact, result_payload)
        return _build_job_result(
            run_result=run_result,
            payload=result_payload,
            failure_type=None,
            status=run_result.transcript.status,
            result_artifact_path=str(result_artifact.resolve()),
        )
