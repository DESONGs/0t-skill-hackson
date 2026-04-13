from __future__ import annotations

from collections import Counter
from datetime import datetime
import hashlib
from typing import Any, Iterable

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

        dominant_actions = _unique_strings(stats.get("dominant_actions") or ())
        if not dominant_actions:
            dominant_actions = ("watch",)
        preferred_tokens = _unique_strings(stats.get("preferred_tokens") or ())
        if not preferred_tokens:
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
        summary = (
            f"{wallet} on {chain} trades with {execution_tempo}, leans {risk_appetite}, "
            f"shows {conviction_profile}, and most often acts through {action_headline} around {token_headline}."
        )

        sizing_note = (
            f"Typical recent ticket is about ${avg_ticket:,.0f}, against an observed balance near ${balance_usd:,.0f}."
            if avg_ticket > 0
            else "Observed activity is sparse, so sizing should stay conservative until more trades are seen."
        )

        execution_rules = _unique_strings(
            (
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

        confidence = 0.32
        confidence += min(activity_count, 6) * 0.07
        confidence += min(len(preferred_tokens), 3) * 0.05
        confidence += 0.08 if top_allocation > 0 else 0.0
        confidence += 0.08 if avg_ticket > 0 else 0.0
        confidence = max(0.25, min(confidence, 0.94))

        review_status = "generate"
        if confidence < 0.55:
            review_status = "generate_with_low_confidence"
        if activity_count == 0 and top_allocation == 0:
            review_status = "needs_manual_review"

        review = StyleReviewDecision(
            status=review_status,
            should_generate_candidate=True,
            reasoning=(
                f"Detected {activity_count} recent actions, {len(preferred_tokens)} focus tokens, "
                f"and confidence {confidence:.2f}; enough for MVP skill generation."
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
            },
        )
        profile = WalletStyleProfile(
            wallet=wallet,
            chain=chain,
            style_label=f"{risk_appetite}-{execution_tempo.replace(' ', '-')}",
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
            },
        )
        return profile, review
