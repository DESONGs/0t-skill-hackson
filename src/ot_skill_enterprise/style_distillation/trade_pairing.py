from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from statistics import median
from typing import Any


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _parse_timestamp(value: Any) -> datetime | None:
    text = _safe_text(value)
    if text is None:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _token_key(item: dict[str, Any]) -> str | None:
    token_ref = dict(item.get("token_ref") or {})
    identifier = _safe_text(token_ref.get("identifier"))
    token_address = _safe_text(token_ref.get("token_address"))
    symbol = _safe_text(token_ref.get("symbol"))
    return token_address or identifier or symbol


def _token_display_name(item: dict[str, Any]) -> str:
    token_ref = dict(item.get("token_ref") or {})
    return str(token_ref.get("symbol") or token_ref.get("identifier") or token_ref.get("token_address") or "").strip()


def _activity_price_usd(item: dict[str, Any], *, side: str) -> float | None:
    preferred_keys = ("to_price_usd", "from_price_usd") if side == "buy" else ("from_price_usd", "to_price_usd")
    for key in preferred_keys:
        price = _safe_float(item.get(key))
        if price is not None and price > 0:
            return price
    amount_usd = _safe_float(item.get("amount_usd"))
    token_amount = _safe_float(item.get("token_amount"))
    if amount_usd is not None and token_amount and token_amount > 0:
        return amount_usd / token_amount
    return None


def _estimate_market_cap_usd(buy_price_usd: float | None, buy_token_amount: float | None) -> float | None:
    if buy_price_usd is None or buy_price_usd <= 0:
        return None
    if buy_token_amount is not None and buy_token_amount > 0:
        estimated_supply = max(buy_token_amount * 1000.0, 1_000_000.0)
        return buy_price_usd * estimated_supply
    return buy_price_usd * 1_000_000.0


def _unique_token_buy_key(token_key: str, timestamp: str, tx_hash: str | None) -> str:
    return f"{token_key}:{timestamp}:{tx_hash or 'unknown'}"


@dataclass(slots=True)
class CompletedTrade:
    token_symbol: str
    token_address: str | None
    token_identifier: str | None
    buy_timestamp: str
    sell_timestamp: str
    buy_amount_usd: float
    sell_amount_usd: float
    holding_seconds: int
    pnl_usd: float
    pnl_pct: float
    is_profitable: bool
    buy_price_usd: float | None = None
    sell_price_usd: float | None = None
    buy_mcap_usd: float | None = None
    buy_amount_vs_avg_ratio: float | None = None
    is_first_buy_for_token: bool = False
    was_in_profit_when_added: bool | None = None
    buy_tx_hash: str | None = None
    sell_tx_hash: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "token_symbol": self.token_symbol,
            "token_address": self.token_address,
            "token_identifier": self.token_identifier,
            "buy_timestamp": self.buy_timestamp,
            "sell_timestamp": self.sell_timestamp,
            "buy_amount_usd": round(self.buy_amount_usd, 8),
            "sell_amount_usd": round(self.sell_amount_usd, 8),
            "holding_seconds": self.holding_seconds,
            "pnl_usd": round(self.pnl_usd, 8),
            "pnl_pct": round(self.pnl_pct, 8),
            "is_profitable": self.is_profitable,
            "buy_price_usd": round(self.buy_price_usd, 8) if self.buy_price_usd is not None else None,
            "sell_price_usd": round(self.sell_price_usd, 8) if self.sell_price_usd is not None else None,
            "buy_mcap_usd": round(self.buy_mcap_usd, 8) if self.buy_mcap_usd is not None else None,
            "buy_amount_vs_avg_ratio": round(self.buy_amount_vs_avg_ratio, 8) if self.buy_amount_vs_avg_ratio is not None else None,
            "is_first_buy_for_token": self.is_first_buy_for_token,
            "was_in_profit_when_added": self.was_in_profit_when_added,
            "buy_tx_hash": self.buy_tx_hash,
            "sell_tx_hash": self.sell_tx_hash,
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class OpenPosition:
    token_symbol: str
    token_address: str | None
    token_identifier: str | None
    buy_timestamp: str
    buy_amount_usd: float
    age_seconds: int
    classification: str
    buy_price_usd: float | None = None
    buy_mcap_usd: float | None = None
    buy_amount_vs_avg_ratio: float | None = None
    is_first_buy_for_token: bool = False
    was_in_profit_when_added: bool | None = None
    tx_hash: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "token_symbol": self.token_symbol,
            "token_address": self.token_address,
            "token_identifier": self.token_identifier,
            "buy_timestamp": self.buy_timestamp,
            "buy_amount_usd": round(self.buy_amount_usd, 8),
            "age_seconds": self.age_seconds,
            "classification": self.classification,
            "buy_price_usd": round(self.buy_price_usd, 8) if self.buy_price_usd is not None else None,
            "buy_mcap_usd": round(self.buy_mcap_usd, 8) if self.buy_mcap_usd is not None else None,
            "buy_amount_vs_avg_ratio": round(self.buy_amount_vs_avg_ratio, 8) if self.buy_amount_vs_avg_ratio is not None else None,
            "is_first_buy_for_token": self.is_first_buy_for_token,
            "was_in_profit_when_added": self.was_in_profit_when_added,
            "tx_hash": self.tx_hash,
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class TradeStatistics:
    total_trades: int
    completed_trade_count: int
    open_position_count: int
    matching_coverage: float
    win_rate: float
    avg_pnl_pct: float
    profit_factor: float
    expectancy_usd: float
    avg_holding_seconds: int
    median_holding_seconds: int
    holding_classification: str
    max_drawdown_pct: float
    avg_loss_pct: float
    loss_tolerance_label: str
    averaging_pattern: str
    avg_position_splits: float
    trades_per_day: float = 0.0
    open_position_ratio: float = 0.0
    pnl_multiplier_max: float = 0.0
    pnl_multiplier_median: float = 0.0
    profitable_avg_holding_seconds: float = 0.0
    losing_avg_holding_seconds: float = 0.0
    profit_reinvestment_rate: float = 0.0
    first_buy_avg_mcap_usd: float = 0.0
    small_cap_trade_ratio: float = 0.0
    profit_add_ratio: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_trades": self.total_trades,
            "completed_trade_count": self.completed_trade_count,
            "open_position_count": self.open_position_count,
            "matching_coverage": round(self.matching_coverage, 8),
            "win_rate": round(self.win_rate, 8),
            "avg_pnl_pct": round(self.avg_pnl_pct, 8),
            "profit_factor": round(self.profit_factor, 8),
            "expectancy_usd": round(self.expectancy_usd, 8),
            "avg_holding_seconds": self.avg_holding_seconds,
            "median_holding_seconds": self.median_holding_seconds,
            "holding_classification": self.holding_classification,
            "max_drawdown_pct": round(self.max_drawdown_pct, 8),
            "avg_loss_pct": round(self.avg_loss_pct, 8),
            "loss_tolerance_label": self.loss_tolerance_label,
            "averaging_pattern": self.averaging_pattern,
            "avg_position_splits": round(self.avg_position_splits, 8),
            "trades_per_day": round(self.trades_per_day, 8),
            "open_position_ratio": round(self.open_position_ratio, 8),
            "pnl_multiplier_max": round(self.pnl_multiplier_max, 8),
            "pnl_multiplier_median": round(self.pnl_multiplier_median, 8),
            "profitable_avg_holding_seconds": round(self.profitable_avg_holding_seconds, 8),
            "losing_avg_holding_seconds": round(self.losing_avg_holding_seconds, 8),
            "profit_reinvestment_rate": round(self.profit_reinvestment_rate, 8),
            "first_buy_avg_mcap_usd": round(self.first_buy_avg_mcap_usd, 8),
            "small_cap_trade_ratio": round(self.small_cap_trade_ratio, 8),
            "profit_add_ratio": round(self.profit_add_ratio, 8),
        }


def pair_trades(
    activities: list[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> tuple[list[CompletedTrade], list[OpenPosition], dict[str, list[float]]]:
    current_time = now or datetime.now(timezone.utc)
    buy_queues: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
    buy_splits: dict[str, list[float]] = defaultdict(list)
    completed: list[CompletedTrade] = []
    token_buy_counts: dict[str, int] = defaultdict(int)
    token_profit_totals: dict[str, float] = defaultdict(float)
    token_buy_amounts: dict[str, list[float]] = defaultdict(list)

    sorted_activities = sorted(
        [item for item in activities if isinstance(item, dict)],
        key=lambda item: _parse_timestamp(item.get("timestamp")) or current_time,
    )

    for item in sorted_activities:
        key = _token_key(item)
        if key is None:
            continue
        action = str(item.get("action") or "").strip().lower()
        amount_usd = _safe_float(item.get("amount_usd")) or 0.0
        token_ref = dict(item.get("token_ref") or {})
        if action == "buy":
            buy_lot = dict(item)
            token_amount = _safe_float(item.get("token_amount"))
            buy_lot["_original_amount_usd"] = amount_usd
            buy_lot["_remaining_token_amount"] = token_amount if token_amount and token_amount > 0 else None
            buy_lot["_remaining_amount_usd"] = amount_usd
            buy_lot["_buy_sequence_index"] = token_buy_counts[key] + 1
            buy_lot["_token_buy_average_before"] = (sum(token_buy_amounts[key]) / len(token_buy_amounts[key])) if token_buy_amounts[key] else amount_usd
            buy_lot["_was_in_profit_when_added"] = token_profit_totals[key] > 0
            buy_lot["_is_first_buy_for_token"] = token_buy_counts[key] == 0
            buy_lot["_buy_price_usd"] = _activity_price_usd(item, side="buy")
            buy_lot["_buy_mcap_usd"] = _estimate_market_cap_usd(
                buy_lot["_buy_price_usd"],
                token_amount if token_amount and token_amount > 0 else None,
            )
            buy_queues[key].append(buy_lot)
            buy_splits[key].append(amount_usd)
            token_buy_counts[key] += 1
            token_buy_amounts[key].append(amount_usd)
            continue
        if action != "sell" or not buy_queues[key]:
            continue

        sell_remaining_token = _safe_float(item.get("token_amount"))
        sell_remaining_usd = amount_usd
        if sell_remaining_token is not None and sell_remaining_token > 0:
            while sell_remaining_token > 0 and buy_queues[key]:
                buy_leg = buy_queues[key][0]
                buy_remaining_token = _safe_float(buy_leg.get("_remaining_token_amount"))
                buy_remaining_usd = _safe_float(buy_leg.get("_remaining_amount_usd")) or 0.0
                if not buy_remaining_token or buy_remaining_token <= 0 or buy_remaining_usd <= 0:
                    buy_queues[key].popleft()
                    continue
                matched_token = min(buy_remaining_token, sell_remaining_token)
                sell_fraction = matched_token / sell_remaining_token if sell_remaining_token > 0 else 0.0
                buy_fraction = matched_token / buy_remaining_token if buy_remaining_token > 0 else 0.0
                buy_amount = buy_remaining_usd * buy_fraction
                sell_amount = sell_remaining_usd * sell_fraction
                buy_ts = _parse_timestamp(buy_leg.get("timestamp")) or current_time
                sell_ts = _parse_timestamp(item.get("timestamp")) or current_time
                pnl_usd = sell_amount - buy_amount
                pnl_pct = (pnl_usd / buy_amount) * 100.0 if buy_amount > 0 else 0.0
                buy_price_usd = _safe_float(buy_leg.get("_buy_price_usd")) or _activity_price_usd(buy_leg, side="buy")
                sell_price_usd = _activity_price_usd(item, side="sell")
                buy_mcap_usd = _safe_float(buy_leg.get("_buy_mcap_usd"))
                buy_amount_vs_avg_ratio = (
                    (_safe_float(buy_leg.get("_original_amount_usd")) or buy_amount)
                    / max(_safe_float(buy_leg.get("_token_buy_average_before")) or buy_amount, 1e-9)
                    if buy_amount > 0
                    else 1.0
                )
                completed.append(
                    CompletedTrade(
                        token_symbol=_token_display_name(buy_leg) or _token_display_name(item),
                        token_address=_safe_text(token_ref.get("token_address") or buy_leg.get("token_ref", {}).get("token_address")),
                        token_identifier=_safe_text(token_ref.get("identifier") or buy_leg.get("token_ref", {}).get("identifier")),
                        buy_timestamp=buy_ts.isoformat(),
                        sell_timestamp=sell_ts.isoformat(),
                        buy_amount_usd=buy_amount,
                        sell_amount_usd=sell_amount,
                        holding_seconds=max(0, int((sell_ts - buy_ts).total_seconds())),
                        pnl_usd=pnl_usd,
                        pnl_pct=pnl_pct,
                        is_profitable=pnl_usd > 0,
                        buy_price_usd=buy_price_usd,
                        sell_price_usd=sell_price_usd,
                        buy_mcap_usd=buy_mcap_usd,
                        buy_amount_vs_avg_ratio=buy_amount_vs_avg_ratio,
                        is_first_buy_for_token=bool(buy_leg.get("_is_first_buy_for_token")),
                        was_in_profit_when_added=bool(buy_leg.get("_was_in_profit_when_added")),
                        buy_tx_hash=_safe_text(buy_leg.get("tx_hash")),
                        sell_tx_hash=_safe_text(item.get("tx_hash")),
                        metadata={
                            "buy_note": buy_leg.get("note"),
                            "sell_note": item.get("note"),
                            "quote_symbol": item.get("quote_symbol") or buy_leg.get("quote_symbol"),
                            "matched_token_amount": round(matched_token, 8),
                            "buy_price_usd": round(buy_price_usd, 8) if buy_price_usd is not None else None,
                            "sell_price_usd": round(sell_price_usd, 8) if sell_price_usd is not None else None,
                            "buy_mcap_usd": round(buy_mcap_usd, 8) if buy_mcap_usd is not None else None,
                            "buy_amount_vs_avg_ratio": round(buy_amount_vs_avg_ratio, 8),
                            "is_first_buy_for_token": bool(buy_leg.get("_is_first_buy_for_token")),
                            "was_in_profit_when_added": bool(buy_leg.get("_was_in_profit_when_added")),
                            "buy_sequence_index": int(buy_leg.get("_buy_sequence_index") or 0),
                        },
                    )
                )
                token_profit_totals[key] += pnl_usd
                buy_leg["_remaining_token_amount"] = max(0.0, buy_remaining_token - matched_token)
                buy_leg["_remaining_amount_usd"] = max(0.0, buy_remaining_usd - buy_amount)
                sell_remaining_token = max(0.0, sell_remaining_token - matched_token)
                sell_remaining_usd = max(0.0, sell_remaining_usd - sell_amount)
                if (_safe_float(buy_leg.get("_remaining_token_amount")) or 0.0) <= 1e-12 or (_safe_float(buy_leg.get("_remaining_amount_usd")) or 0.0) <= 1e-8:
                    buy_queues[key].popleft()
        else:
            sell_ts = _parse_timestamp(item.get("timestamp")) or current_time
            while buy_queues[key]:
                buy_leg = buy_queues[key][0]
                buy_remaining_usd = _safe_float(buy_leg.get("_remaining_amount_usd")) or _safe_float(buy_leg.get("amount_usd")) or 0.0
                if buy_remaining_usd <= 1e-8:
                    buy_queues[key].popleft()
                    continue
                buy_ts = _parse_timestamp(buy_leg.get("timestamp")) or current_time
                matched_identifier = dict(buy_leg.get("token_ref") or {})
                pnl_usd = sell_remaining_usd - buy_remaining_usd
                pnl_pct = (pnl_usd / buy_remaining_usd) * 100.0 if buy_remaining_usd > 0 else 0.0
                unmatched_sell_usd = max(0.0, sell_remaining_usd - buy_remaining_usd)
                unmatched_buy_usd = max(0.0, buy_remaining_usd - sell_remaining_usd)
                buy_price_usd = _safe_float(buy_leg.get("_buy_price_usd")) or _activity_price_usd(buy_leg, side="buy")
                sell_price_usd = _activity_price_usd(item, side="sell")
                buy_mcap_usd = _safe_float(buy_leg.get("_buy_mcap_usd"))
                buy_amount_vs_avg_ratio = (
                    (_safe_float(buy_leg.get("_original_amount_usd")) or buy_remaining_usd)
                    / max(_safe_float(buy_leg.get("_token_buy_average_before")) or buy_remaining_usd, 1e-9)
                    if buy_remaining_usd > 0
                    else 1.0
                )
                completed.append(
                    CompletedTrade(
                        token_symbol=_token_display_name(buy_leg) or _token_display_name(item),
                        token_address=_safe_text(token_ref.get("token_address") or matched_identifier.get("token_address")),
                        token_identifier=_safe_text(token_ref.get("identifier") or matched_identifier.get("identifier")),
                        buy_timestamp=buy_ts.isoformat(),
                        sell_timestamp=sell_ts.isoformat(),
                        buy_amount_usd=buy_remaining_usd,
                        sell_amount_usd=sell_remaining_usd,
                        holding_seconds=max(0, int((sell_ts - buy_ts).total_seconds())),
                        pnl_usd=pnl_usd,
                        pnl_pct=pnl_pct,
                        is_profitable=pnl_usd > 0,
                        buy_price_usd=buy_price_usd,
                        sell_price_usd=sell_price_usd,
                        buy_mcap_usd=buy_mcap_usd,
                        buy_amount_vs_avg_ratio=buy_amount_vs_avg_ratio,
                        is_first_buy_for_token=bool(buy_leg.get("_is_first_buy_for_token")),
                        was_in_profit_when_added=bool(buy_leg.get("_was_in_profit_when_added")),
                        buy_tx_hash=_safe_text(buy_leg.get("tx_hash")),
                        sell_tx_hash=_safe_text(item.get("tx_hash")),
                        metadata={
                            "buy_note": buy_leg.get("note"),
                            "sell_note": item.get("note"),
                            "quote_symbol": item.get("quote_symbol") or buy_leg.get("quote_symbol"),
                            "usd_only_matching": True,
                            "matching_mode": "event_fifo",
                            "unmatched_sell_amount_usd": round(unmatched_sell_usd, 8),
                            "unmatched_buy_amount_usd": round(unmatched_buy_usd, 8),
                            "buy_price_usd": round(buy_price_usd, 8) if buy_price_usd is not None else None,
                            "sell_price_usd": round(sell_price_usd, 8) if sell_price_usd is not None else None,
                            "buy_mcap_usd": round(buy_mcap_usd, 8) if buy_mcap_usd is not None else None,
                            "buy_amount_vs_avg_ratio": round(buy_amount_vs_avg_ratio, 8),
                            "is_first_buy_for_token": bool(buy_leg.get("_is_first_buy_for_token")),
                            "was_in_profit_when_added": bool(buy_leg.get("_was_in_profit_when_added")),
                            "buy_sequence_index": int(buy_leg.get("_buy_sequence_index") or 0),
                        },
                    )
                )
                token_profit_totals[key] += pnl_usd
                buy_queues[key].popleft()
                break

    open_positions: list[OpenPosition] = []
    for key, queue in buy_queues.items():
        for buy_leg in queue:
            buy_ts = _parse_timestamp(buy_leg.get("timestamp")) or current_time
            age_seconds = max(0, int((current_time - buy_ts).total_seconds()))
            classification = "long_hold" if age_seconds >= 7 * 24 * 3600 else "unrealized"
            token_ref = dict(buy_leg.get("token_ref") or {})
            open_positions.append(
                OpenPosition(
                    token_symbol=_token_display_name(buy_leg),
                    token_address=_safe_text(token_ref.get("token_address")),
                    token_identifier=_safe_text(token_ref.get("identifier")) or key,
                    buy_timestamp=buy_ts.isoformat(),
                    buy_amount_usd=_safe_float(buy_leg.get("_remaining_amount_usd")) or _safe_float(buy_leg.get("amount_usd")) or 0.0,
                    age_seconds=age_seconds,
                    classification=classification,
                    buy_price_usd=_safe_float(buy_leg.get("_buy_price_usd")) or _activity_price_usd(buy_leg, side="buy"),
                    buy_mcap_usd=_safe_float(buy_leg.get("_buy_mcap_usd")),
                    buy_amount_vs_avg_ratio=_safe_float(buy_leg.get("_token_buy_average_before")) and (
                        (_safe_float(buy_leg.get("_original_amount_usd")) or _safe_float(buy_leg.get("_remaining_amount_usd")) or _safe_float(buy_leg.get("amount_usd")) or 0.0)
                        / max(_safe_float(buy_leg.get("_token_buy_average_before")) or 1.0, 1e-9)
                    ),
                    is_first_buy_for_token=bool(buy_leg.get("_is_first_buy_for_token")),
                    was_in_profit_when_added=bool(buy_leg.get("_was_in_profit_when_added")),
                    tx_hash=_safe_text(buy_leg.get("tx_hash")),
                    metadata={
                        "note": buy_leg.get("note"),
                        "remaining_token_amount": _safe_float(buy_leg.get("_remaining_token_amount")),
                        "buy_price_usd": round(_safe_float(buy_leg.get("_buy_price_usd")) or _activity_price_usd(buy_leg, side="buy"), 8)
                        if (_safe_float(buy_leg.get("_buy_price_usd")) or _activity_price_usd(buy_leg, side="buy")) is not None
                        else None,
                        "buy_mcap_usd": round(_safe_float(buy_leg.get("_buy_mcap_usd")), 8) if _safe_float(buy_leg.get("_buy_mcap_usd")) is not None else None,
                        "buy_amount_vs_avg_ratio": round(
                            (
                                (_safe_float(buy_leg.get("_original_amount_usd")) or _safe_float(buy_leg.get("_remaining_amount_usd")) or _safe_float(buy_leg.get("amount_usd")) or 0.0)
                                / max(_safe_float(buy_leg.get("_token_buy_average_before")) or 1.0, 1e-9)
                            ),
                            8,
                        ),
                        "is_first_buy_for_token": bool(buy_leg.get("_is_first_buy_for_token")),
                        "was_in_profit_when_added": bool(buy_leg.get("_was_in_profit_when_added")),
                        "buy_sequence_index": int(buy_leg.get("_buy_sequence_index") or 0),
                    },
                )
            )
    return completed, open_positions, buy_splits


def _holding_classification(avg_holding_seconds: float) -> str:
    if avg_holding_seconds <= 0:
        return "sparse"
    if avg_holding_seconds < 3600:
        return "scalping"
    if avg_holding_seconds < 86400:
        return "day_trading"
    if avg_holding_seconds < 7 * 24 * 3600:
        return "swing"
    return "position"


def _loss_tolerance_label(avg_loss_pct: float) -> str:
    loss = abs(avg_loss_pct)
    if loss == 0:
        return "unknown"
    if loss <= 8:
        return "tight_stop"
    if loss <= 18:
        return "moderate"
    return "diamond_hands"


def _detect_averaging_pattern(buy_splits: dict[str, list[float]]) -> tuple[str, float]:
    sequences = [splits for splits in buy_splits.values() if len(splits) > 1]
    if not sequences:
        return "none", 1.0 if buy_splits else 0.0
    split_counts = [len(splits) for splits in buy_splits.values() if splits]
    patterns: list[str] = []
    for amounts in sequences:
        diffs = [amounts[index + 1] - amounts[index] for index in range(len(amounts) - 1)]
        if diffs and all(diff > 0 for diff in diffs):
            patterns.append("martingale")
        elif diffs and all(diff < 0 for diff in diffs):
            patterns.append("pyramid")
        elif diffs and all(abs(diff) <= max(amounts[0] * 0.15, 1.0) for diff in diffs):
            patterns.append("linear_dca")
        else:
            patterns.append("mixed")
    dominant = max(set(patterns), key=patterns.count) if patterns else "none"
    avg_splits = sum(split_counts) / len(split_counts) if split_counts else 0.0
    return dominant, avg_splits


def compute_trade_statistics(
    activities: list[dict[str, Any]],
    completed_trades: list[CompletedTrade],
    open_positions: list[OpenPosition],
    buy_splits: dict[str, list[float]],
) -> TradeStatistics:
    def _trade_multiplier(trade: CompletedTrade) -> float:
        if trade.buy_amount_usd <= 0:
            return 0.0
        return trade.sell_amount_usd / trade.buy_amount_usd

    def _buy_record_key(payload: dict[str, Any] | CompletedTrade | OpenPosition) -> str:
        if isinstance(payload, dict):
            token_identifier = _safe_text(payload.get("token_identifier")) or _safe_text(payload.get("token_address")) or _safe_text(payload.get("token_symbol"))
            timestamp = _safe_text(payload.get("buy_timestamp")) or _safe_text(payload.get("timestamp")) or "unknown"
            tx_hash = _safe_text(payload.get("buy_tx_hash")) or _safe_text(payload.get("tx_hash"))
            return _unique_token_buy_key(token_identifier or "unknown", timestamp, tx_hash)
        token_identifier = getattr(payload, "token_identifier", None) or getattr(payload, "token_address", None) or getattr(payload, "token_symbol", None) or "unknown"
        timestamp = getattr(payload, "buy_timestamp", None) or "unknown"
        tx_hash = getattr(payload, "buy_tx_hash", None) or getattr(payload, "tx_hash", None)
        return _unique_token_buy_key(str(token_identifier), str(timestamp), _safe_text(tx_hash))

    def _buy_record_metadata(payload: dict[str, Any] | CompletedTrade | OpenPosition) -> dict[str, Any]:
        metadata = dict(getattr(payload, "metadata", {}) or {}) if not isinstance(payload, dict) else dict(payload.get("metadata") or {})
        if isinstance(payload, dict):
            metadata.setdefault("is_first_buy_for_token", bool(payload.get("is_first_buy_for_token")))
            metadata.setdefault("buy_mcap_usd", _safe_float(payload.get("buy_mcap_usd")))
            metadata.setdefault("buy_amount_vs_avg_ratio", _safe_float(payload.get("buy_amount_vs_avg_ratio")))
            metadata.setdefault("was_in_profit_when_added", payload.get("was_in_profit_when_added"))
        else:
            metadata.setdefault("is_first_buy_for_token", bool(getattr(payload, "is_first_buy_for_token", False)))
            metadata.setdefault("buy_mcap_usd", getattr(payload, "buy_mcap_usd", None))
            metadata.setdefault("buy_amount_vs_avg_ratio", getattr(payload, "buy_amount_vs_avg_ratio", None))
            metadata.setdefault("was_in_profit_when_added", getattr(payload, "was_in_profit_when_added", None))
        return metadata

    pnl_values = [trade.pnl_usd for trade in completed_trades]
    pnl_pct_values = [trade.pnl_pct for trade in completed_trades]
    wins = [trade for trade in completed_trades if trade.is_profitable]
    losses = [trade for trade in completed_trades if not trade.is_profitable]
    holding_seconds = [trade.holding_seconds for trade in completed_trades]
    profitable_holding_seconds = [trade.holding_seconds for trade in wins]
    losing_holding_seconds = [trade.holding_seconds for trade in losses]
    gross_profit = sum(trade.pnl_usd for trade in wins)
    gross_loss = abs(sum(trade.pnl_usd for trade in losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float(gross_profit > 0)
    avg_loss_pct = sum(trade.pnl_pct for trade in losses) / len(losses) if losses else 0.0
    averaging_pattern, avg_position_splits = _detect_averaging_pattern(buy_splits)
    total_sell_notional = sum((_safe_float(item.get("amount_usd")) or 0.0) for item in activities if str(item.get("action") or "").strip().lower() == "sell")
    matched_sell_notional = sum(trade.sell_amount_usd for trade in completed_trades)
    matching_coverage = min(1.0, matched_sell_notional / total_sell_notional) if total_sell_notional > 0 else 0.0
    trade_multipliers = [_trade_multiplier(trade) for trade in completed_trades if trade.buy_amount_usd > 0]
    unique_buy_records: dict[str, dict[str, Any]] = {}
    for trade in completed_trades:
        key = _buy_record_key(trade)
        if trade.is_first_buy_for_token and key not in unique_buy_records:
            unique_buy_records[key] = _buy_record_metadata(trade)
    for position in open_positions:
        key = _buy_record_key(position)
        if position.is_first_buy_for_token and key not in unique_buy_records:
            unique_buy_records[key] = _buy_record_metadata(position)
    first_buy_mcap_values = [
        _safe_float(record.get("buy_mcap_usd"))
        for record in unique_buy_records.values()
        if _safe_float(record.get("buy_mcap_usd")) is not None
    ]
    small_cap_threshold = 5_000_000.0
    small_cap_trade_ratio = 0.0
    if unique_buy_records:
        small_cap_trade_ratio = sum(
            1
            for record in unique_buy_records.values()
            if (_safe_float(record.get("buy_mcap_usd")) or 0.0) > 0 and (_safe_float(record.get("buy_mcap_usd")) or 0.0) <= small_cap_threshold
        ) / len(unique_buy_records)
    profit_add_ratio = 0.0
    if completed_trades:
        profit_add_ratio = sum(
            1
            for trade in completed_trades
            if bool((trade.metadata or {}).get("was_in_profit_when_added")) or bool(trade.was_in_profit_when_added)
        ) / len(completed_trades)
    activities_with_ts = [
        item
        for item in sorted(activities, key=lambda item: _parse_timestamp(item.get("timestamp")) or current_time)
        if str(item.get("action") or "").strip().lower() in {"buy", "sell"}
    ]
    active_days = {
        (_parse_timestamp(item.get("timestamp")) or current_time).date().isoformat()
        for item in activities_with_ts
        if _parse_timestamp(item.get("timestamp")) is not None
    }
    if not active_days and activities_with_ts:
        active_days.add((current_time).date().isoformat())
    trades_per_day = (len(activities_with_ts) / len(active_days)) if active_days else 0.0
    open_position_ratio = len(open_positions) / max(len(open_positions) + len(completed_trades), 1)
    pnl_multiplier_max = max(trade_multipliers) if trade_multipliers else 0.0
    pnl_multiplier_median = median(trade_multipliers) if trade_multipliers else 0.0
    profitable_avg_holding_seconds = (sum(profitable_holding_seconds) / len(profitable_holding_seconds)) if profitable_holding_seconds else 0.0
    losing_avg_holding_seconds = (sum(losing_holding_seconds) / len(losing_holding_seconds)) if losing_holding_seconds else 0.0
    profitable_sell_timestamps = [
        _parse_timestamp(trade.sell_timestamp)
        for trade in sorted(wins, key=lambda trade: trade.sell_timestamp)
        if _parse_timestamp(trade.sell_timestamp) is not None
    ]
    profitable_sell_timestamps = [timestamp for timestamp in profitable_sell_timestamps if timestamp is not None]
    post_profit_buy_notional = 0.0
    if profitable_sell_timestamps:
        earliest_profit_sell = min(profitable_sell_timestamps)
        for item in activities_with_ts:
            if str(item.get("action") or "").strip().lower() != "buy":
                continue
            timestamp = _parse_timestamp(item.get("timestamp"))
            if timestamp is None or timestamp <= earliest_profit_sell:
                continue
            post_profit_buy_notional += _safe_float(item.get("amount_usd")) or 0.0
    total_profitable_sell_notional = sum(trade.sell_amount_usd for trade in wins)
    profit_reinvestment_rate = min(1.0, post_profit_buy_notional / total_profitable_sell_notional) if total_profitable_sell_notional > 0 else 0.0
    return TradeStatistics(
        total_trades=len([item for item in activities if str(item.get("action") or "").strip().lower() in {"buy", "sell"}]),
        completed_trade_count=len(completed_trades),
        open_position_count=len(open_positions),
        matching_coverage=matching_coverage,
        win_rate=(len(wins) / len(completed_trades)) if completed_trades else 0.0,
        avg_pnl_pct=(sum(pnl_pct_values) / len(pnl_pct_values)) if pnl_pct_values else 0.0,
        profit_factor=profit_factor,
        expectancy_usd=(sum(pnl_values) / len(pnl_values)) if pnl_values else 0.0,
        avg_holding_seconds=int(sum(holding_seconds) / len(holding_seconds)) if holding_seconds else 0,
        median_holding_seconds=int(median(holding_seconds)) if holding_seconds else 0,
        holding_classification=_holding_classification((sum(holding_seconds) / len(holding_seconds)) if holding_seconds else 0.0),
        max_drawdown_pct=min(pnl_pct_values) if pnl_pct_values else 0.0,
        avg_loss_pct=avg_loss_pct,
        loss_tolerance_label=_loss_tolerance_label(avg_loss_pct),
        averaging_pattern=averaging_pattern,
        avg_position_splits=avg_position_splits,
        trades_per_day=trades_per_day,
        open_position_ratio=open_position_ratio,
        pnl_multiplier_max=pnl_multiplier_max,
        pnl_multiplier_median=pnl_multiplier_median,
        profitable_avg_holding_seconds=profitable_avg_holding_seconds,
        losing_avg_holding_seconds=losing_avg_holding_seconds,
        profit_reinvestment_rate=profit_reinvestment_rate,
        first_buy_avg_mcap_usd=(sum(first_buy_mcap_values) / len(first_buy_mcap_values)) if first_buy_mcap_values else 0.0,
        small_cap_trade_ratio=small_cap_trade_ratio,
        profit_add_ratio=profit_add_ratio,
    )
