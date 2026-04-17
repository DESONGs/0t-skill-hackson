from __future__ import annotations

from collections import Counter
from typing import Any, Mapping, Sequence

from .models import BehavioralPattern, TradingArchetype
from .trade_pairing import CompletedTrade, OpenPosition, TradeStatistics


ARCHETYPE_TAXONOMY: tuple[str, ...] = (
    "scalper",
    "high_freq_rotator",
    "swing_trader",
    "meme_hunter",
    "diamond_hands",
    "degen_sniper",
    "compounding_builder",
    "asymmetric_bettor",
    "no_stable_archetype",
)

NO_STABLE_ARCHETYPE = "no_stable_archetype"


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _stats_map(trade_statistics: TradeStatistics | Mapping[str, Any] | None) -> dict[str, Any]:
    if trade_statistics is None:
        return {}
    if hasattr(trade_statistics, "to_dict"):
        return dict(trade_statistics.to_dict())  # type: ignore[arg-type]
    return dict(trade_statistics)


def _record_map(item: CompletedTrade | OpenPosition | Mapping[str, Any]) -> dict[str, Any]:
    if hasattr(item, "to_dict"):
        return dict(item.to_dict())  # type: ignore[arg-type]
    return dict(item)


def _linear_score(value: float, low: float, high: float) -> float:
    if high <= low:
        return 0.0
    if value <= low:
        return 0.0
    if value >= high:
        return 1.0
    return (value - low) / (high - low)


def _inverse_score(value: float, low: float, high: float) -> float:
    return 1.0 - _linear_score(value, low, high)


def _bell_score(value: float, center: float, width: float) -> float:
    if width <= 0:
        return 0.0
    distance = abs(value - center)
    if distance >= width:
        return 0.0
    return 1.0 - (distance / width)


def _small_cap_score(first_buy_avg_mcap_usd: float, small_cap_trade_ratio: float) -> float:
    score = _linear_score(small_cap_trade_ratio, 0.0, 0.6)
    if first_buy_avg_mcap_usd <= 0:
        return score
    if first_buy_avg_mcap_usd <= 1_000_000:
        return max(score, 1.0)
    if first_buy_avg_mcap_usd <= 5_000_000:
        return max(score, 0.85)
    if first_buy_avg_mcap_usd <= 20_000_000:
        return max(score, 0.45)
    if first_buy_avg_mcap_usd <= 50_000_000:
        return max(score, 0.2)
    return score


def _token_preferences(
    completed_trades: Sequence[CompletedTrade | Mapping[str, Any]],
    open_positions: Sequence[OpenPosition | Mapping[str, Any]],
) -> tuple[str, ...]:
    counts: Counter[str] = Counter()
    for item in list(completed_trades) + list(open_positions):
        record = _record_map(item)
        symbol = _safe_text(record.get("token_symbol")) or _safe_text(record.get("token_identifier")) or _safe_text(record.get("token_address"))
        if symbol:
            counts[symbol] += 1
    return tuple(symbol for symbol, _ in counts.most_common(3))


def _evidence_summary(stats: dict[str, Any], patterns: Sequence[BehavioralPattern]) -> list[str]:
    values = [
        f"trades_per_day={_safe_float(stats.get('trades_per_day')):.2f}",
        f"avg_holding_seconds={int(_safe_float(stats.get('avg_holding_seconds')))}",
        f"open_position_ratio={_safe_float(stats.get('open_position_ratio')):.2f}",
        f"small_cap_trade_ratio={_safe_float(stats.get('small_cap_trade_ratio')):.2f}",
        f"profit_add_ratio={_safe_float(stats.get('profit_add_ratio')):.2f}",
        f"profit_reinvestment_rate={_safe_float(stats.get('profit_reinvestment_rate')):.2f}",
        f"pnl_multiplier_max={_safe_float(stats.get('pnl_multiplier_max')):.2f}",
    ]
    for pattern in patterns[:3]:
        values.append(f"{pattern.pattern_label}:{pattern.strength:.2f}")
    return values


def derive_behavioral_patterns(
    trade_statistics: TradeStatistics | Mapping[str, Any] | None,
    completed_trades: Sequence[CompletedTrade | Mapping[str, Any]] = (),
    open_positions: Sequence[OpenPosition | Mapping[str, Any]] = (),
) -> list[BehavioralPattern]:
    stats = _stats_map(trade_statistics)
    avg_holding_seconds = _safe_float(stats.get("avg_holding_seconds"))
    trades_per_day = _safe_float(stats.get("trades_per_day"))
    open_position_ratio = _safe_float(stats.get("open_position_ratio"))
    avg_position_splits = _safe_float(stats.get("avg_position_splits"))
    small_cap_trade_ratio = _safe_float(stats.get("small_cap_trade_ratio"))
    first_buy_avg_mcap_usd = _safe_float(stats.get("first_buy_avg_mcap_usd"))
    profit_add_ratio = _safe_float(stats.get("profit_add_ratio"))
    profit_reinvestment_rate = _safe_float(stats.get("profit_reinvestment_rate"))
    pnl_multiplier_max = _safe_float(stats.get("pnl_multiplier_max"))
    pnl_multiplier_median = _safe_float(stats.get("pnl_multiplier_median"))
    win_rate = _safe_float(stats.get("win_rate"))
    averaging_pattern = _safe_text(stats.get("averaging_pattern")).lower()
    loss_tolerance_label = _safe_text(stats.get("loss_tolerance_label")).lower()

    short_hold = _inverse_score(avg_holding_seconds / 3600.0, 0.25, 72.0)
    long_hold = _linear_score(avg_holding_seconds / 3600.0, 24.0, 168.0)
    mid_hold = _bell_score(avg_holding_seconds / 3600.0, 24.0, 96.0)
    fast_activity = _linear_score(trades_per_day, 0.5, 8.0)
    very_fast_activity = _linear_score(trades_per_day, 2.0, 12.0)
    open_hold = _linear_score(open_position_ratio, 0.15, 0.8)
    moderate_open = _bell_score(open_position_ratio, 0.35, 0.25)
    split_bias = _linear_score(avg_position_splits, 1.0, 3.0)
    profit_add = _linear_score(profit_add_ratio, 0.0, 0.5)
    profit_recycle = _linear_score(profit_reinvestment_rate, 0.0, 0.6)
    upside = _linear_score(pnl_multiplier_max, 1.0, 3.0)
    median_upside = _linear_score(pnl_multiplier_median, 0.9, 1.8)
    small_cap_bias = _small_cap_score(first_buy_avg_mcap_usd, small_cap_trade_ratio)
    win_quality = _linear_score(win_rate, 0.35, 0.75)
    loss_hardness = 1.0 if loss_tolerance_label == "diamond_hands" else 0.0
    averaging_bias = 1.0 if averaging_pattern != "none" else 0.0

    patterns: list[BehavioralPattern] = []

    fast_rotation_strength = max(fast_activity, short_hold, split_bias)
    if fast_rotation_strength >= 0.2:
        patterns.append(
            BehavioralPattern(
                pattern_label="fast_rotation",
                strength=round(fast_rotation_strength, 4),
                evidence=(
                    f"trades_per_day={trades_per_day:.2f}",
                    f"avg_holding_hours={avg_holding_seconds / 3600.0:.2f}",
                    f"avg_position_splits={avg_position_splits:.2f}",
                ),
                metadata={"fast_activity": round(fast_activity, 4), "short_hold": round(short_hold, 4)},
            )
        )

    if small_cap_bias >= 0.2:
        patterns.append(
            BehavioralPattern(
                pattern_label="small_cap_bias",
                strength=round(small_cap_bias, 4),
                evidence=(
                    f"first_buy_avg_mcap_usd={first_buy_avg_mcap_usd:.2f}",
                    f"small_cap_trade_ratio={small_cap_trade_ratio:.2f}",
                ),
                metadata={"small_cap_trade_ratio": round(small_cap_trade_ratio, 4)},
            )
        )

    profit_cycle_strength = max(profit_add, profit_recycle)
    if profit_cycle_strength >= 0.2:
        patterns.append(
            BehavioralPattern(
                pattern_label="profit_recycling",
                strength=round(profit_cycle_strength, 4),
                evidence=(
                    f"profit_add_ratio={profit_add_ratio:.2f}",
                    f"profit_reinvestment_rate={profit_reinvestment_rate:.2f}",
                ),
                metadata={"profit_add_ratio": round(profit_add_ratio, 4), "profit_reinvestment_rate": round(profit_reinvestment_rate, 4)},
            )
        )

    conviction_strength = max(long_hold, open_hold, loss_hardness)
    if conviction_strength >= 0.2:
        patterns.append(
            BehavioralPattern(
                pattern_label="conviction_holding",
                strength=round(conviction_strength, 4),
                evidence=(
                    f"avg_holding_hours={avg_holding_seconds / 3600.0:.2f}",
                    f"open_position_ratio={open_position_ratio:.2f}",
                    f"loss_tolerance_label={loss_tolerance_label or 'unknown'}",
                ),
                metadata={"open_position_ratio": round(open_position_ratio, 4), "long_hold": round(long_hold, 4)},
            )
        )

    if averaging_bias >= 0.2:
        patterns.append(
            BehavioralPattern(
                pattern_label="averaging_behavior",
                strength=round(max(averaging_bias, split_bias), 4),
                evidence=(
                    f"averaging_pattern={averaging_pattern or 'none'}",
                    f"avg_position_splits={avg_position_splits:.2f}",
                ),
                metadata={"averaging_pattern": averaging_pattern or "none"},
            )
        )

    upside_strength = max(upside, median_upside, _linear_score(1.0 - win_rate, 0.0, 0.6))
    if upside_strength >= 0.2:
        patterns.append(
            BehavioralPattern(
                pattern_label="asymmetric_upside",
                strength=round(upside_strength, 4),
                evidence=(
                    f"pnl_multiplier_max={pnl_multiplier_max:.2f}",
                    f"pnl_multiplier_median={pnl_multiplier_median:.2f}",
                    f"win_rate={win_rate:.2f}",
                ),
                metadata={"pnl_multiplier_max": round(pnl_multiplier_max, 4), "pnl_multiplier_median": round(pnl_multiplier_median, 4)},
            )
        )

    patterns.sort(key=lambda item: (item.strength, item.pattern_label), reverse=True)
    return patterns


def score_archetypes(
    trade_statistics: TradeStatistics | Mapping[str, Any] | None,
    behavioral_patterns: Sequence[BehavioralPattern] = (),
) -> list[dict[str, Any]]:
    stats = _stats_map(trade_statistics)
    patterns_by_label = {pattern.pattern_label: pattern for pattern in behavioral_patterns}
    avg_holding_seconds = _safe_float(stats.get("avg_holding_seconds"))
    trades_per_day = _safe_float(stats.get("trades_per_day"))
    open_position_ratio = _safe_float(stats.get("open_position_ratio"))
    avg_position_splits = _safe_float(stats.get("avg_position_splits"))
    small_cap_trade_ratio = _safe_float(stats.get("small_cap_trade_ratio"))
    first_buy_avg_mcap_usd = _safe_float(stats.get("first_buy_avg_mcap_usd"))
    profit_add_ratio = _safe_float(stats.get("profit_add_ratio"))
    profit_reinvestment_rate = _safe_float(stats.get("profit_reinvestment_rate"))
    pnl_multiplier_max = _safe_float(stats.get("pnl_multiplier_max"))
    pnl_multiplier_median = _safe_float(stats.get("pnl_multiplier_median"))
    win_rate = _safe_float(stats.get("win_rate"))
    profit_factor = _safe_float(stats.get("profit_factor"))
    averaging_pattern = _safe_text(stats.get("averaging_pattern")).lower()
    loss_tolerance_label = _safe_text(stats.get("loss_tolerance_label")).lower()

    short_hold = _inverse_score(avg_holding_seconds / 3600.0, 0.25, 72.0)
    long_hold = _linear_score(avg_holding_seconds / 3600.0, 24.0, 168.0)
    mid_hold = _bell_score(avg_holding_seconds / 3600.0, 24.0, 96.0)
    fast_activity = _linear_score(trades_per_day, 0.5, 8.0)
    very_fast_activity = _linear_score(trades_per_day, 2.0, 12.0)
    open_hold = _linear_score(open_position_ratio, 0.15, 0.8)
    moderate_open = _bell_score(open_position_ratio, 0.35, 0.25)
    split_bias = _linear_score(avg_position_splits, 1.0, 3.0)
    profit_add = _linear_score(profit_add_ratio, 0.0, 0.5)
    profit_recycle = _linear_score(profit_reinvestment_rate, 0.0, 0.6)
    upside = _linear_score(pnl_multiplier_max, 1.0, 3.0)
    median_upside = _linear_score(pnl_multiplier_median, 0.9, 1.8)
    small_cap_bias = _small_cap_score(first_buy_avg_mcap_usd, small_cap_trade_ratio)
    win_quality = _linear_score(win_rate, 0.35, 0.75)
    profit_factor_quality = _linear_score(profit_factor, 1.0, 3.0)
    loss_hardness = 1.0 if loss_tolerance_label == "diamond_hands" else 0.0
    averaging_bias = 1.0 if averaging_pattern != "none" else 0.0

    fast_rotation = patterns_by_label.get("fast_rotation", BehavioralPattern("fast_rotation", 0.0))
    small_cap_pattern = patterns_by_label.get("small_cap_bias", BehavioralPattern("small_cap_bias", 0.0))
    profit_cycle = patterns_by_label.get("profit_recycling", BehavioralPattern("profit_recycling", 0.0))
    conviction = patterns_by_label.get("conviction_holding", BehavioralPattern("conviction_holding", 0.0))
    averaging = patterns_by_label.get("averaging_behavior", BehavioralPattern("averaging_behavior", 0.0))
    asymmetric_upside = patterns_by_label.get("asymmetric_upside", BehavioralPattern("asymmetric_upside", 0.0))

    scores = [
        {
            "label": "scalper",
            "score": round(
                min(
                    1.0,
                    0.34 * fast_activity
                    + 0.28 * short_hold
                    + 0.16 * (1.0 - open_position_ratio if open_position_ratio <= 1.0 else 0.0)
                    + 0.12 * win_quality
                    + 0.10 * fast_rotation.strength,
                ),
                4,
            ),
            "evidence": ["short_holding", "high_turnover", "tight_position_cycle"],
        },
        {
            "label": "high_freq_rotator",
            "score": round(
                min(
                    1.0,
                    0.34 * very_fast_activity
                    + 0.22 * split_bias
                    + 0.18 * short_hold
                    + 0.12 * moderate_open
                    + 0.14 * fast_rotation.strength,
                ),
                4,
            ),
            "evidence": ["split_leg_rotation", "fast_turnover", "middle_open_positions"],
        },
        {
            "label": "swing_trader",
            "score": round(
                min(
                    1.0,
                    0.34 * mid_hold
                    + 0.18 * fast_activity
                    + 0.18 * win_quality
                    + 0.16 * profit_factor_quality
                    + 0.14 * (1.0 - small_cap_bias),
                ),
                4,
            ),
            "evidence": ["mid_horizon_hold", "positive_expectancy", "moderate_rotation"],
        },
        {
            "label": "meme_hunter",
            "score": round(
                min(
                    1.0,
                    0.32 * small_cap_bias
                    + 0.18 * upside
                    + 0.15 * fast_activity
                    + 0.12 * split_bias
                    + 0.12 * small_cap_pattern.strength
                    + 0.11 * asymmetric_upside.strength,
                ),
                4,
            ),
            "evidence": ["small_cap_bias", "upside_seeking", "rotation_into_virality"],
        },
        {
            "label": "diamond_hands",
            "score": round(
                min(
                    1.0,
                    0.38 * long_hold
                    + 0.18 * open_hold
                    + 0.14 * loss_hardness
                    + 0.12 * (1.0 - fast_activity)
                    + 0.10 * conviction.strength
                    + 0.08 * (1.0 - win_quality),
                ),
                4,
            ),
            "evidence": ["long_horizon", "high_open_positions", "low_rotation"],
        },
        {
            "label": "degen_sniper",
            "score": round(
                min(
                    1.0,
                    0.30 * very_fast_activity
                    + 0.22 * short_hold
                    + 0.18 * small_cap_bias
                    + 0.15 * upside
                    + 0.10 * (1.0 - win_quality)
                    + 0.05 * fast_rotation.strength,
                ),
                4,
            ),
            "evidence": ["tiny_holding_windows", "small_cap_frenzy", "high_velocity_entries"],
        },
        {
            "label": "compounding_builder",
            "score": round(
                min(
                    1.0,
                    0.30 * profit_cycle.strength
                    + 0.18 * profit_add
                    + 0.18 * profit_recycle
                    + 0.14 * split_bias
                    + 0.10 * profit_factor_quality
                    + 0.10 * win_quality,
                ),
                4,
            ),
            "evidence": ["profit_recycling", "split_adding", "compound_redeployment"],
        },
        {
            "label": "asymmetric_bettor",
            "score": round(
                min(
                    1.0,
                    0.32 * asymmetric_upside.strength
                    + 0.20 * upside
                    + 0.14 * median_upside
                    + 0.14 * small_cap_bias
                    + 0.10 * (1.0 - win_quality)
                    + 0.10 * (1.0 - fast_activity),
                ),
                4,
            ),
            "evidence": ["high_upside_outliers", "uneven_win_loss_distribution", "optionality"],
        },
        {
            "label": NO_STABLE_ARCHETYPE,
            "score": round(
                min(
                    1.0,
                    max(
                        0.0,
                        1.0
                        - max(
                            0.0,
                            0.38 * max(fast_activity, very_fast_activity)
                            + 0.24 * max(short_hold, mid_hold, long_hold)
                            + 0.14 * small_cap_bias
                            + 0.12 * profit_cycle.strength
                            + 0.12 * win_quality,
                        ),
                    ),
                ),
                4,
            ),
            "evidence": ["low_signal_or_low_confidence"],
        },
    ]
    scores.sort(key=lambda item: (item["score"], item["label"]), reverse=True)
    return scores


def select_primary_and_secondary(
    scored_archetypes: Sequence[Mapping[str, Any]],
    behavioral_patterns: Sequence[BehavioralPattern],
    trade_statistics: TradeStatistics | Mapping[str, Any] | None,
    *,
    completed_trades: Sequence[CompletedTrade | Mapping[str, Any]] = (),
    open_positions: Sequence[OpenPosition | Mapping[str, Any]] = (),
) -> TradingArchetype:
    stats = _stats_map(trade_statistics)
    total_trades = int(_safe_float(stats.get("completed_trade_count")) or 0)
    if total_trades <= 1:
        primary_label = NO_STABLE_ARCHETYPE
        primary_score = 0.0
        secondary_labels: tuple[str, ...] = ()
    else:
        filtered_scores = [dict(item) for item in scored_archetypes if _safe_text(item.get("label"))]
        filtered_scores.sort(key=lambda item: (float(item.get("score") or 0.0), _safe_text(item.get("label"))), reverse=True)
        best = filtered_scores[0] if filtered_scores else {"label": NO_STABLE_ARCHETYPE, "score": 0.0}
        primary_label = _safe_text(best.get("label")) or NO_STABLE_ARCHETYPE
        primary_score = _safe_float(best.get("score"))
        if primary_label == NO_STABLE_ARCHETYPE or primary_score < 0.45:
            primary_label = NO_STABLE_ARCHETYPE
            secondary_labels = ()
        else:
            secondary_labels = tuple(
                _safe_text(item.get("label"))
                for item in filtered_scores[1:4]
                if _safe_text(item.get("label"))
                and _safe_text(item.get("label")) != primary_label
                and _safe_float(item.get("score")) >= max(0.35, primary_score - 0.12)
            )[:2]

    if primary_label == NO_STABLE_ARCHETYPE:
        confidence = min(0.45, 0.20 + 0.05 * len(behavioral_patterns))
    else:
        confidence = min(0.95, max(0.30, primary_score + 0.05 * min(len(behavioral_patterns), 3)))

    token_preference = _token_preferences(completed_trades, open_positions)
    evidence = _evidence_summary(stats, behavioral_patterns)
    metadata = {
        "score_map": {str(item.get("label")): _safe_float(item.get("score")) for item in scored_archetypes},
        "pattern_count": len(behavioral_patterns),
        "primary_score": round(float(primary_score), 4),
    }
    return TradingArchetype(
        trading_archetype=primary_label,
        primary_label=primary_label,
        secondary_archetypes=secondary_labels,
        behavioral_patterns=tuple(behavioral_patterns),
        confidence=confidence,
        evidence=tuple(evidence),
        token_preference=token_preference,
        trades_per_day=_safe_float(stats.get("trades_per_day")),
        open_position_ratio=_safe_float(stats.get("open_position_ratio")),
        pnl_multiplier_max=_safe_float(stats.get("pnl_multiplier_max")),
        pnl_multiplier_median=_safe_float(stats.get("pnl_multiplier_median")),
        trade_statistics=stats,
        metadata=metadata,
    )


def classify_archetype(
    trade_statistics: TradeStatistics | Mapping[str, Any] | None,
    completed_trades: Sequence[CompletedTrade | Mapping[str, Any]] = (),
    open_positions: Sequence[OpenPosition | Mapping[str, Any]] = (),
) -> TradingArchetype:
    patterns = derive_behavioral_patterns(trade_statistics, completed_trades, open_positions)
    scores = score_archetypes(trade_statistics, patterns)
    return select_primary_and_secondary(
        scores,
        patterns,
        trade_statistics,
        completed_trades=completed_trades,
        open_positions=open_positions,
    )
