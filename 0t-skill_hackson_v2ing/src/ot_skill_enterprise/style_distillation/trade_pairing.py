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
            "tx_hash": self.tx_hash,
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class TradeStatistics:
    total_trades: int
    completed_trade_count: int
    open_position_count: int
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_trades": self.total_trades,
            "completed_trade_count": self.completed_trade_count,
            "open_position_count": self.open_position_count,
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
            buy_queues[key].append(item)
            buy_splits[key].append(amount_usd)
            continue
        if action != "sell" or not buy_queues[key]:
            continue

        buy_leg = buy_queues[key].popleft()
        buy_amount = _safe_float(buy_leg.get("amount_usd")) or 0.0
        sell_amount = amount_usd
        buy_ts = _parse_timestamp(buy_leg.get("timestamp")) or current_time
        sell_ts = _parse_timestamp(item.get("timestamp")) or current_time
        pnl_usd = sell_amount - buy_amount
        pnl_pct = (pnl_usd / buy_amount) * 100.0 if buy_amount > 0 else 0.0
        completed.append(
            CompletedTrade(
                token_symbol=str(token_ref.get("symbol") or buy_leg.get("token_ref", {}).get("symbol") or "").strip(),
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
                buy_tx_hash=_safe_text(buy_leg.get("tx_hash")),
                sell_tx_hash=_safe_text(item.get("tx_hash")),
                metadata={
                    "buy_note": buy_leg.get("note"),
                    "sell_note": item.get("note"),
                    "quote_symbol": item.get("quote_symbol") or buy_leg.get("quote_symbol"),
                },
            )
        )

    open_positions: list[OpenPosition] = []
    for key, queue in buy_queues.items():
        for buy_leg in queue:
            buy_ts = _parse_timestamp(buy_leg.get("timestamp")) or current_time
            age_seconds = max(0, int((current_time - buy_ts).total_seconds()))
            classification = "long_hold" if age_seconds >= 7 * 24 * 3600 else "unrealized"
            token_ref = dict(buy_leg.get("token_ref") or {})
            open_positions.append(
                OpenPosition(
                    token_symbol=str(token_ref.get("symbol") or "").strip(),
                    token_address=_safe_text(token_ref.get("token_address")),
                    token_identifier=_safe_text(token_ref.get("identifier")) or key,
                    buy_timestamp=buy_ts.isoformat(),
                    buy_amount_usd=_safe_float(buy_leg.get("amount_usd")) or 0.0,
                    age_seconds=age_seconds,
                    classification=classification,
                    tx_hash=_safe_text(buy_leg.get("tx_hash")),
                    metadata={"note": buy_leg.get("note")},
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
    pnl_values = [trade.pnl_usd for trade in completed_trades]
    pnl_pct_values = [trade.pnl_pct for trade in completed_trades]
    wins = [trade for trade in completed_trades if trade.is_profitable]
    losses = [trade for trade in completed_trades if not trade.is_profitable]
    holding_seconds = [trade.holding_seconds for trade in completed_trades]
    gross_profit = sum(trade.pnl_usd for trade in wins)
    gross_loss = abs(sum(trade.pnl_usd for trade in losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float(gross_profit > 0)
    avg_loss_pct = sum(trade.pnl_pct for trade in losses) / len(losses) if losses else 0.0
    averaging_pattern, avg_position_splits = _detect_averaging_pattern(buy_splits)
    return TradeStatistics(
        total_trades=len([item for item in activities if str(item.get("action") or "").strip().lower() in {"buy", "sell"}]),
        completed_trade_count=len(completed_trades),
        open_position_count=len(open_positions),
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
    )
