from __future__ import annotations

from collections import Counter
from datetime import datetime
import hashlib
from typing import Any, Iterable

from .archetype import NO_STABLE_ARCHETYPE
from .models import StyleReviewDecision, WalletStyleProfile


DEFAULT_EXTRACTION_PROMPT = (
    "You are the wallet-style extractor for the 0T hackathon MVP.\n"
    "Given compact wallet JSON, identify the address's trading style, including tempo, "
    "risk appetite, conviction profile, token preference, sizing pattern, and execution "
    "guardrails. Prefer concise reusable rules that can be compiled into a local skill. "
    "If the evidence is sparse, still produce a best-effort profile and label confidence."
)


_PROMPT_DIMENSIONS = {
    "风险": "risk",
    "risk": "risk",
    "liquidity": "liquidity",
    "流动性": "liquidity",
    "timing": "timing",
    "时间": "timing",
    "concentration": "concentration",
    "集中度": "concentration",
    "stable": "stablecoin",
    "稳定币": "stablecoin",
    "momentum": "momentum",
    "趋势": "momentum",
    "size": "sizing",
    "仓位": "sizing",
}

_STABLE_SYMBOLS = {"USDT", "USDC", "DAI", "FDUSD", "TUSD"}

_WINDOWS = (
    (0, 6, "asia-late"),
    (6, 12, "asia-open"),
    (12, 18, "europe-overlap"),
    (18, 24, "us-session"),
)


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _unique_strings(values: Iterable[Any]) -> tuple[str, ...]:
    seen: set[str] = set()
    items: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        items.append(text)
    return tuple(items)


def _prompt_focus(prompt: str) -> tuple[str, ...]:
    lowered = prompt.lower()
    matched = [label for keyword, label in _PROMPT_DIMENSIONS.items() if keyword in lowered]
    return _unique_strings(matched)


def _fallback_bucket(wallet: str, values: tuple[str, ...]) -> str:
    if not values:
        return "balanced"
    digest = hashlib.sha256(wallet.encode("utf-8")).hexdigest()
    index = int(digest[:8], 16) % len(values)
    return values[index]


def _activity_windows(hours: Iterable[int]) -> tuple[str, ...]:
    labels: list[str] = []
    for hour in hours:
        for start, end, label in _WINDOWS:
            if start <= hour < end:
                labels.append(label)
                break
    return _unique_strings(labels)


def _hour_from_timestamp(value: Any) -> int | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.hour


def _extract_archetype_payload(preprocessed: dict[str, Any]) -> dict[str, Any]:
    combined: dict[str, Any] = {}
    derived_stats = dict(preprocessed.get("derived_stats") or {})
    signal_context = dict(preprocessed.get("signal_context") or {})

    for source in (
        derived_stats.get("archetype"),
        signal_context.get("archetype"),
        preprocessed.get("archetype"),
    ):
        if isinstance(source, dict):
            combined.update(source)

    for key in (
        "primary_archetype",
        "secondary_archetypes",
        "behavioral_patterns",
        "archetype_confidence",
        "evidence",
        "token_preference",
    ):
        if key in derived_stats and key not in combined:
            combined[key] = derived_stats.get(key)

    if "confidence" in derived_stats and "archetype_confidence" not in combined:
        combined["archetype_confidence"] = derived_stats.get("confidence")
    if "archetype_evidence_summary" in derived_stats and "evidence" not in combined:
        combined["evidence"] = derived_stats.get("archetype_evidence_summary")
    if "archetype_token_preference" in derived_stats and "token_preference" not in combined:
        combined["token_preference"] = derived_stats.get("archetype_token_preference")

    return combined


def _pattern_summaries(patterns: Any) -> tuple[str, ...]:
    summaries: list[str] = []
    for pattern in patterns or ():
        if isinstance(pattern, dict):
            label = str(pattern.get("pattern_label") or pattern.get("label") or pattern.get("name") or "").strip()
            if not label:
                continue
            strength = _safe_float(pattern.get("strength"))
            evidence = _unique_strings(pattern.get("evidence") or ())
            summary = label
            if strength is not None:
                summary = f"{summary} ({strength:.2f})"
            if evidence:
                summary = f"{summary}: {'; '.join(evidence[:2])}"
            summaries.append(summary)
        else:
            text = str(pattern or "").strip()
            if text:
                summaries.append(text)
    return _unique_strings(summaries)


def _archetype_metadata(archetype_payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "primary_archetype": str(
            archetype_payload.get("primary_archetype")
            or archetype_payload.get("primary_label")
            or archetype_payload.get("trading_archetype")
            or ""
        ).strip(),
        "secondary_archetypes": list(_unique_strings(archetype_payload.get("secondary_archetypes") or ())),
        "behavioral_patterns": list(_pattern_summaries(archetype_payload.get("behavioral_patterns") or ())),
        "archetype_confidence": _safe_float(
            archetype_payload.get("archetype_confidence")
            if archetype_payload.get("archetype_confidence") is not None
            else archetype_payload.get("confidence")
        ),
        "evidence": list(_unique_strings(archetype_payload.get("evidence") or ())),
        "token_preference": list(_unique_strings(archetype_payload.get("token_preference") or ())),
    }


class WalletStyleExtractor:
    def __init__(self, system_prompt: str = DEFAULT_EXTRACTION_PROMPT) -> None:
        self.system_prompt = system_prompt

    def extract(
        self,
        preprocessed: dict[str, Any],
        *,
        system_prompt: str | None = None,
    ) -> tuple[WalletStyleProfile, StyleReviewDecision]:
        prompt = str(system_prompt or self.system_prompt).strip() or DEFAULT_EXTRACTION_PROMPT
        stats = dict(preprocessed.get("derived_stats") or {})
        wallet = str(preprocessed.get("wallet") or "")
        chain = str(preprocessed.get("chain") or "unknown")
        balance_usd = _safe_float(preprocessed.get("wallet_summary", {}).get("balance_usd")) or 0.0
        activity_count = int(stats.get("activity_count") or 0)
        avg_ticket = _safe_float(stats.get("avg_activity_usd")) or 0.0
        top_allocation = _safe_float(stats.get("top_holding_allocation_pct")) or 0.0
        stablecoin_allocation = _safe_float(stats.get("stablecoin_allocation_pct")) or 0.0
        risky_token_count = int(stats.get("risky_token_count") or 0)
        prompt_focus = _prompt_focus(prompt)
        archetype_payload = _extract_archetype_payload(preprocessed)
        archetype_metadata = _archetype_metadata(archetype_payload) if archetype_payload else {}
        primary_archetype = str(archetype_metadata.get("primary_archetype") or "").strip()
        secondary_archetypes = tuple(str(item) for item in archetype_metadata.get("secondary_archetypes") or () if str(item).strip())
        behavioral_patterns = tuple(str(item) for item in archetype_metadata.get("behavioral_patterns") or () if str(item).strip())
        archetype_evidence = tuple(str(item) for item in archetype_metadata.get("evidence") or () if str(item).strip())
        archetype_token_preference = tuple(str(item) for item in archetype_metadata.get("token_preference") or () if str(item).strip())
        archetype_confidence = archetype_metadata.get("archetype_confidence")

        dominant_actions = _unique_strings(stats.get("dominant_actions") or ())
        if not dominant_actions:
            dominant_actions = ("watch",)
        preferred_tokens = _unique_strings(stats.get("preferred_tokens") or ())
        if archetype_token_preference:
            preferred_tokens = archetype_token_preference
        elif not preferred_tokens:
            preferred_tokens = (_fallback_bucket(wallet, ("ALPHA", "BETA", "USDT")),)

        hours = [
            hour
            for hour in (_hour_from_timestamp(item.get("timestamp")) for item in preprocessed.get("recent_activity", []))
            if hour is not None
        ]
        active_windows = _activity_windows(hours)
        if not active_windows:
            active_windows = (_fallback_bucket(wallet, ("asia-open", "europe-overlap", "us-session")),)

        if activity_count >= 6:
            execution_tempo = "high-frequency rotation"
        elif activity_count >= 3:
            execution_tempo = "active swing"
        elif balance_usd > 0:
            execution_tempo = "position holding"
        else:
            execution_tempo = "sparse sample"

        risk_score = 0.35
        risk_score += min(avg_ticket / max(balance_usd, 1.0), 1.0) * 0.3
        risk_score += min(top_allocation / 100.0, 1.0) * 0.25
        risk_score += min(risky_token_count, 3) * 0.08
        risk_score -= min(stablecoin_allocation / 100.0, 1.0) * 0.15
        if "buy" in dominant_actions[:1]:
            risk_score += 0.05
        if risk_score >= 0.62:
            risk_appetite = "aggressive"
        elif risk_score >= 0.45:
            risk_appetite = "balanced"
        else:
            risk_appetite = "conservative"

        if top_allocation >= 55:
            conviction_profile = "single-name conviction"
        elif top_allocation >= 35:
            conviction_profile = "barbell concentration"
        elif stablecoin_allocation >= 50:
            conviction_profile = "cash-heavy optionality"
        else:
            conviction_profile = "distributed basket"

        if stablecoin_allocation >= 60:
            stablecoin_bias = "reserve-heavy"
        elif stablecoin_allocation >= 30:
            stablecoin_bias = "buffered"
        else:
            stablecoin_bias = "fully deployed"

        action_headline = " / ".join(dominant_actions[:2])
        token_headline = ", ".join(preferred_tokens[:3])
        archetype_headline = primary_archetype or f"{risk_appetite}-{execution_tempo.replace(' ', '-')}"
        summary = (
            f"{wallet} on {chain} maps to {archetype_headline}, trades with {execution_tempo}, "
            f"leans {risk_appetite}, shows {conviction_profile}, and most often acts through {action_headline} "
            f"around {token_headline}."
        )
        if primary_archetype == NO_STABLE_ARCHETYPE:
            summary = (
                f"{wallet} on {chain} currently shows no stable archetype, with insufficient pattern stability "
                f"to project a reusable wallet skill yet."
            )

        sizing_note = (
            f"Typical recent ticket is about ${avg_ticket:,.0f}, against an observed balance near ${balance_usd:,.0f}."
            if avg_ticket > 0
            else "Observed activity is sparse, so sizing should stay conservative until more trades are seen."
        )

        archetype_rules: list[str] = []
        if primary_archetype:
            archetype_rules.append(f"Honor archetype signal: {primary_archetype}.")
        if secondary_archetypes:
            archetype_rules.append(f"Secondary archetypes observed: {', '.join(secondary_archetypes[:3])}.")
        if behavioral_patterns:
            archetype_rules.append(f"Behavioral patterns observed: {', '.join(behavioral_patterns[:3])}.")
        if archetype_evidence:
            archetype_rules.append(f"Evidence signals: {', '.join(archetype_evidence[:3])}.")

        execution_rules = _unique_strings(
            (
                *archetype_rules,
                f"Bias decisions toward {action_headline} setups instead of all-market participation.",
                f"Keep focus on {token_headline} when selecting tokens to imitate this wallet.",
                f"Respect a {stablecoin_bias} capital posture before increasing exposure.",
                f"Operate mainly during {', '.join(active_windows)} windows unless stronger context overrides it.",
                sizing_note,
            )
        )
        anti_patterns = _unique_strings(
            (
                "Avoid chasing illiquid names outside the observed token set." if "liquidity" in prompt_focus or risky_token_count else "",
                "Avoid forcing high turnover when the wallet sample is sparse." if activity_count < 3 else "",
                "Avoid over-diversifying if the source wallet is concentrated." if top_allocation >= 35 else "",
                "Avoid all-in deployment if the wallet kept a stablecoin buffer." if stablecoin_allocation >= 30 else "",
            )
        )

        heuristic_confidence = 0.32
        heuristic_confidence += min(activity_count, 6) * 0.07
        heuristic_confidence += min(len(preferred_tokens), 3) * 0.05
        heuristic_confidence += 0.08 if top_allocation > 0 else 0.0
        heuristic_confidence += 0.08 if avg_ticket > 0 else 0.0
        heuristic_confidence = max(0.25, min(heuristic_confidence, 0.94))

        confidence = archetype_confidence if archetype_confidence is not None else heuristic_confidence
        review_status = "generate"
        should_generate_candidate = True
        if primary_archetype == NO_STABLE_ARCHETYPE:
            should_generate_candidate = False
            if activity_count <= 1:
                review_status = "insufficient_signal"
            else:
                review_status = "no_pattern_detected"
        elif confidence < 0.55:
            review_status = "generate_with_low_confidence"
        elif activity_count == 0 and top_allocation == 0:
            review_status = "needs_manual_review"

        review = StyleReviewDecision(
            status=review_status,
            should_generate_candidate=should_generate_candidate,
            reasoning=(
                f"Detected {activity_count} recent actions, {len(preferred_tokens)} focus tokens, "
                f"and confidence {confidence:.2f}; "
                + (
                    "no stable archetype was detected, so the wallet needs more signal before generating a candidate."
                    if primary_archetype == NO_STABLE_ARCHETYPE and activity_count <= 1
                    else "no stable archetype was detected, but the observed behavior still lacks a reusable pattern."
                    if primary_archetype == NO_STABLE_ARCHETYPE
                    else "enough for MVP skill generation."
                    if should_generate_candidate
                    else "insufficient evidence to generate a reusable candidate."
                )
            ),
            nudge_prompt=(
                "Review whether this wallet-style profile should become a reusable skill. "
                "If the strategy summary is coherent and reusable, generate or patch the skill package immediately."
            ),
            metadata={
                "prompt_focus": list(prompt_focus),
                "activity_count": activity_count,
                "top_holding_allocation_pct": top_allocation,
                "stablecoin_allocation_pct": stablecoin_allocation,
                "archetype": archetype_metadata,
            },
        )
        profile = WalletStyleProfile(
            wallet=wallet,
            chain=chain,
            style_label=primary_archetype or f"{risk_appetite}-{execution_tempo.replace(' ', '-')}",
            summary=summary,
            confidence=confidence,
            execution_tempo=execution_tempo,
            risk_appetite=risk_appetite,
            conviction_profile=conviction_profile,
            stablecoin_bias=stablecoin_bias,
            dominant_actions=dominant_actions,
            preferred_tokens=preferred_tokens,
            active_windows=active_windows,
            sizing_note=sizing_note,
            execution_rules=execution_rules,
            anti_patterns=anti_patterns,
            prompt_focus=prompt_focus,
            metadata={
                "source_activity_count": activity_count,
                "top_holding_allocation_pct": top_allocation,
                "stablecoin_allocation_pct": stablecoin_allocation,
                "avg_activity_usd": avg_ticket,
                "archetype": archetype_metadata,
            },
        )
        return profile, review
