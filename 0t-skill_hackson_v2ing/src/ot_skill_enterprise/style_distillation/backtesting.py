from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .market_context import TokenMarketContext
from .trade_pairing import CompletedTrade


@dataclass(slots=True)
class BacktestResult:
    total_signals: int
    executed_trades: int
    correct_signals: int
    signal_accuracy: float
    simulated_pnl_usd: float
    actual_pnl_usd: float
    pnl_capture_ratio: float
    max_drawdown_pct: float
    confidence_score: float
    confidence_label: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_signals": self.total_signals,
            "executed_trades": self.executed_trades,
            "correct_signals": self.correct_signals,
            "signal_accuracy": round(self.signal_accuracy, 8),
            "simulated_pnl_usd": round(self.simulated_pnl_usd, 8),
            "actual_pnl_usd": round(self.actual_pnl_usd, 8),
            "pnl_capture_ratio": round(self.pnl_capture_ratio, 8),
            "max_drawdown_pct": round(self.max_drawdown_pct, 8),
            "confidence_score": round(self.confidence_score, 8),
            "confidence_label": self.confidence_label,
            "metadata": dict(self.metadata),
        }


def _confidence_label(score: float) -> str:
    if score >= 0.7:
        return "high"
    if score >= 0.45:
        return "medium"
    if score >= 0.25:
        return "low"
    return "insufficient_data"


def _compute_confidence(*, sample_size: int, signal_accuracy: float, pnl_capture_ratio: float, max_drawdown_pct: float) -> float:
    data_score = min(1.0, sample_size / 15.0)
    capture_score = min(1.0, max(0.0, pnl_capture_ratio))
    risk_score = 1.0
    if max_drawdown_pct < -20:
        risk_score = 0.4
    elif max_drawdown_pct < -10:
        risk_score = 0.7
    score = data_score * 0.3 + signal_accuracy * 0.35 + capture_score * 0.2 + risk_score * 0.15
    return round(max(0.1, min(0.95, score)), 8)


def run_backtest(
    strategy: dict[str, Any],
    completed_trades: list[CompletedTrade],
    market_contexts: list[TokenMarketContext],
    signal_context: dict[str, Any] | None = None,
) -> BacktestResult:
    preferred = {
        str(token).strip().upper()
        for token in (
            list(strategy.get("preferred_setups") or [])
            + list((strategy.get("metadata") or {}).get("preferred_tokens") or [])
        )
        if str(token).strip()
    }
    factor_hints = {
        str(item.get("factor_type") or "").strip()
        for item in list((strategy.get("metadata") or {}).get("entry_factors") or [])
        if isinstance(item, dict)
    }
    context_by_symbol = {context.symbol.upper(): context for context in market_contexts if context.symbol}
    matched: list[CompletedTrade] = []
    for trade in completed_trades:
        symbol = trade.token_symbol.upper()
        if preferred and symbol not in preferred:
            continue
        context = context_by_symbol.get(symbol)
        if not factor_hints:
            matched.append(trade)
            continue
        if context is None:
            continue
        if "dip_buy" in factor_hints and (context.price_change_1h_pct or 0.0) <= -10:
            matched.append(trade)
            continue
        if "momentum_chase" in factor_hints and (context.price_change_1h_pct or 0.0) >= 8:
            matched.append(trade)
            continue
        if "volume_spike" in factor_hints and (context.volume_to_liquidity_ratio or 0.0) >= 1.5:
            matched.append(trade)
            continue
        if "volatility_play" in factor_hints and context.volatility_regime in {"high", "extreme"}:
            matched.append(trade)
            continue
    total_signals = len(matched)
    correct_signals = sum(1 for trade in matched if trade.is_profitable)
    simulated_pnl = sum(trade.pnl_usd for trade in matched)
    actual_pnl = sum(trade.pnl_usd for trade in completed_trades)
    pnl_capture_ratio = simulated_pnl / actual_pnl if actual_pnl > 0 else 0.0
    signal_accuracy = correct_signals / total_signals if total_signals else 0.0
    max_drawdown_pct = min((trade.pnl_pct for trade in matched), default=0.0)
    confidence_score = _compute_confidence(
        sample_size=len(completed_trades),
        signal_accuracy=signal_accuracy,
        pnl_capture_ratio=pnl_capture_ratio,
        max_drawdown_pct=max_drawdown_pct,
    )
    return BacktestResult(
        total_signals=total_signals,
        executed_trades=len(completed_trades),
        correct_signals=correct_signals,
        signal_accuracy=signal_accuracy,
        simulated_pnl_usd=simulated_pnl,
        actual_pnl_usd=actual_pnl,
        pnl_capture_ratio=pnl_capture_ratio,
        max_drawdown_pct=max_drawdown_pct,
        confidence_score=confidence_score,
        confidence_label=_confidence_label(confidence_score),
        metadata={
            "preferred_token_count": len(preferred),
            "factor_hint_count": len(factor_hints),
            "active_signal_count": int((signal_context or {}).get("active_signals") or 0),
        },
    )
