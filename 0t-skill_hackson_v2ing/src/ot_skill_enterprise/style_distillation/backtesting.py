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
    data_quality_score: float
    strategy_fit_score: float
    backtest_score: float
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
            "data_quality_score": round(self.data_quality_score, 8),
            "strategy_fit_score": round(self.strategy_fit_score, 8),
            "backtest_score": round(self.backtest_score, 8),
            "confidence_score": round(self.confidence_score, 8),
            "confidence_label": self.confidence_label,
            "metadata": dict(self.metadata),
        }


def _confidence_label(score: float) -> str:
    if score >= 0.7:
        return "high"
    if score >= 0.45:
        return "medium"
    if score >= 0.2:
        return "low"
    return "insufficient_data"


def _compute_confidence(*, data_quality_score: float, strategy_fit_score: float, backtest_score: float) -> float:
    score = data_quality_score * 0.4 + strategy_fit_score * 0.3 + backtest_score * 0.3
    return round(max(0.05, min(0.95, score)), 8)


def _compute_backtest_score(*, signal_accuracy: float, pnl_capture_ratio: float, max_drawdown_pct: float) -> float:
    capture_score = min(1.0, max(0.0, pnl_capture_ratio))
    risk_score = 1.0
    if max_drawdown_pct < -20:
        risk_score = 0.4
    elif max_drawdown_pct < -10:
        risk_score = 0.7
    score = signal_accuracy * 0.45 + capture_score * 0.35 + risk_score * 0.2
    return round(max(0.0, min(1.0, score)), 8)


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
    risk_filters = [
        item
        for item in list((strategy.get("metadata") or {}).get("risk_filters") or [])
        if isinstance(item, dict)
    ]
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
    active_signal_count = int((signal_context or {}).get("active_signals") or 0)
    sample_size = len(completed_trades)
    market_ready = len(context_by_symbol)
    factor_ready = len(factor_hints)
    risk_ready = len(risk_filters)
    data_quality_score = min(
        1.0,
        min(1.0, sample_size / 20.0) * 0.45
        + (0.25 if market_ready else 0.0)
        + (0.15 if factor_ready else 0.0)
        + (0.1 if risk_ready else 0.0)
        + (0.05 if active_signal_count else 0.0),
    )
    strategy_fit_score = (
        (0.55 * signal_accuracy)
        + (0.2 if factor_ready else 0.0)
        + (0.15 if active_signal_count else 0.0)
        + (0.1 if total_signals else 0.0)
    )
    backtest_score = _compute_backtest_score(
        signal_accuracy=signal_accuracy,
        pnl_capture_ratio=pnl_capture_ratio,
        max_drawdown_pct=max_drawdown_pct,
    )
    confidence_score = _compute_confidence(
        data_quality_score=data_quality_score,
        strategy_fit_score=strategy_fit_score,
        backtest_score=backtest_score,
    )
    insufficient_reasons: list[str] = []
    if not market_ready:
        insufficient_reasons.append("market_context_missing")
    if not factor_ready:
        insufficient_reasons.append("entry_factors_missing")
    if not risk_ready:
        insufficient_reasons.append("risk_filters_missing")
    if not active_signal_count:
        insufficient_reasons.append("active_signals_missing")
    if sample_size < 8:
        insufficient_reasons.append("sample_size_low")
    baseline_only = not factor_ready
    if baseline_only:
        confidence_score = min(confidence_score, 0.35)
    if len(insufficient_reasons) >= 2:
        confidence_score = min(confidence_score, 0.3)
    confidence_label = _confidence_label(confidence_score)
    return BacktestResult(
        total_signals=total_signals,
        executed_trades=sample_size,
        correct_signals=correct_signals,
        signal_accuracy=signal_accuracy,
        simulated_pnl_usd=simulated_pnl,
        actual_pnl_usd=actual_pnl,
        pnl_capture_ratio=pnl_capture_ratio,
        max_drawdown_pct=max_drawdown_pct,
        data_quality_score=data_quality_score,
        strategy_fit_score=strategy_fit_score,
        backtest_score=backtest_score,
        confidence_score=confidence_score,
        confidence_label=confidence_label,
        metadata={
            "preferred_token_count": len(preferred),
            "factor_hint_count": len(factor_hints),
            "risk_filter_count": len(risk_filters),
            "market_context_count": len(context_by_symbol),
            "active_signal_count": active_signal_count,
            "baseline_only": baseline_only,
            "insufficient_reasons": insufficient_reasons,
        },
    )
