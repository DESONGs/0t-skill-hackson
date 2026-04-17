from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .market_context import TokenMarketContext
from .trade_pairing import CompletedTrade


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass(slots=True)
class EntryFactor:
    factor_type: str
    description: str
    frequency: float
    avg_pnl_when_present: float
    confidence: float
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "factor_type": self.factor_type,
            "description": self.description,
            "frequency": round(self.frequency, 8),
            "avg_pnl_when_present": round(self.avg_pnl_when_present, 8),
            "confidence": round(self.confidence, 8),
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class RiskFilter:
    filter_type: str
    description: str
    threshold: Any
    is_hard_block: bool
    source: str
    symbol: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "filter_type": self.filter_type,
            "description": self.description,
            "threshold": self.threshold,
            "is_hard_block": self.is_hard_block,
            "source": self.source,
            "symbol": self.symbol,
            "metadata": dict(self.metadata),
        }


def build_risk_filters(token_profiles: list[dict[str, Any]]) -> list[RiskFilter]:
    filters: list[RiskFilter] = []
    for profile in token_profiles:
        identity = dict(profile.get("identity") or {})
        symbol = str(identity.get("symbol") or "").strip() or None
        risk = dict(profile.get("risk_snapshot") or {})
        holder = dict(profile.get("holder_snapshot") or {})
        risk_meta = dict(risk.get("metadata") or {})
        risk_summary = dict(risk_meta.get("ai_report_summary") or {})
        ai_risk_names = [
            str(item).strip().lower().replace(" ", "_")
            for item in list(risk_meta.get("ai_risk_names") or [])
            if str(item).strip()
        ]
        flags = [str(item).strip().lower() for item in risk.get("flags") or [] if str(item).strip()]
        flags.extend(ai_risk_names)
        if risk.get("honeypot") is True:
            filters.append(
                RiskFilter(
                    filter_type="honeypot",
                    description=f"{symbol or 'token'} flagged as honeypot",
                    threshold=True,
                    is_hard_block=True,
                    source="inspect_token.risk_snapshot",
                    symbol=symbol,
                )
            )
        buy_tax = int(_safe_float(risk.get("buy_tax_bps")) or 0)
        sell_tax = int(_safe_float(risk.get("sell_tax_bps")) or 0)
        if buy_tax > 500 or sell_tax > 500:
            filters.append(
                RiskFilter(
                    filter_type="high_tax",
                    description=f"{symbol or 'token'} exceeds 5% transfer tax",
                    threshold=max(buy_tax, sell_tax),
                    is_hard_block=True,
                    source="inspect_token.risk_snapshot",
                    symbol=symbol,
                    metadata={"buy_tax_bps": buy_tax, "sell_tax_bps": sell_tax},
                )
            )
        risk_level = str(risk.get("risk_level") or risk_summary.get("risk_level") or "").strip().lower()
        if risk_level in {"high", "critical"}:
            filters.append(
                RiskFilter(
                    filter_type="elevated_risk_level",
                    description=f"{symbol or 'token'} carries {risk_level} protocol risk",
                    threshold=risk_level,
                    is_hard_block=risk_level == "critical",
                    source="inspect_token.risk_snapshot.risk_level",
                    symbol=symbol,
                )
            )
        if risk_summary.get("has_freeze_mechanism") is True or any("transfer_restriction" in flag or "freeze_mechanism" in flag for flag in flags):
            filters.append(
                RiskFilter(
                    filter_type="transfer_restriction",
                    description=f"{symbol or 'token'} can restrict transfers or freeze holders",
                    threshold=True,
                    is_hard_block=True,
                    source="inspect_token.risk_snapshot.metadata.ai_report_summary",
                    symbol=symbol,
                    metadata={"risk_names": ai_risk_names},
                )
            )
        if risk_summary.get("has_transfer_risk") is True or any("transfer_controlled_mode" in flag or "blacklist" in flag for flag in flags):
            filters.append(
                RiskFilter(
                    filter_type="owner_transfer_control",
                    description=f"{symbol or 'token'} has owner-controlled transfer rules",
                    threshold=True,
                    is_hard_block=False,
                    source="inspect_token.risk_snapshot.metadata.ai_report_summary",
                    symbol=symbol,
                    metadata={"risk_names": ai_risk_names},
                )
            )
        if risk_summary.get("has_mint_burn_risk") is True or any("mint_burn" in flag or "owner-only_initialization" in flag for flag in flags):
            filters.append(
                RiskFilter(
                    filter_type="mint_burn_risk",
                    description=f"{symbol or 'token'} has owner-controlled supply or burn risk",
                    threshold=True,
                    is_hard_block=False,
                    source="inspect_token.risk_snapshot.metadata.ai_report_summary",
                    symbol=symbol,
                    metadata={"risk_names": ai_risk_names},
                )
            )
        top_holder_share = _safe_float(holder.get("top_holder_share_pct")) or 0.0
        if top_holder_share > 50:
            filters.append(
                RiskFilter(
                    filter_type="holder_concentration",
                    description=f"{symbol or 'token'} top holders control {round(top_holder_share, 2)}% of supply",
                    threshold=50,
                    is_hard_block=False,
                    source="inspect_token.holder_snapshot",
                    symbol=symbol,
                    metadata={"top_holder_share_pct": top_holder_share},
                )
            )
        elif top_holder_share > 20:
            filters.append(
                RiskFilter(
                    filter_type="holder_concentration",
                    description=f"{symbol or 'token'} top holders control {round(top_holder_share, 2)}% of supply",
                    threshold=20,
                    is_hard_block=False,
                    source="inspect_token.holder_snapshot",
                    symbol=symbol,
                    metadata={"top_holder_share_pct": top_holder_share},
                )
            )
        if any("lp" in flag or "liquidity" in flag for flag in flags):
            filters.append(
                RiskFilter(
                    filter_type="lp_stability",
                    description=f"{symbol or 'token'} has LP stability warning",
                    threshold="best_effort",
                    is_hard_block=False,
                    source="inspect_token.risk_snapshot.flags",
                    symbol=symbol,
                    metadata={"flags": flags},
                )
            )
    unique: list[RiskFilter] = []
    seen: set[tuple[str, str | None]] = set()
    for item in filters:
        marker = (item.filter_type, item.symbol)
        if marker in seen:
            continue
        seen.add(marker)
        unique.append(item)
    return unique


def filters_to_anti_patterns(filters: list[RiskFilter]) -> list[str]:
    patterns: list[str] = []
    for risk_filter in filters:
        prefix = "BLOCK" if risk_filter.is_hard_block else "WARN"
        patterns.append(f"{prefix}: {risk_filter.description}")
    return patterns


def distill_entry_factors(
    completed_trades: list[CompletedTrade],
    market_contexts: list[TokenMarketContext],
) -> list[EntryFactor]:
    profitable = [trade for trade in completed_trades if trade.is_profitable]
    if not profitable:
        return []
    context_by_symbol = {context.symbol.upper(): context for context in market_contexts if context.symbol}
    factor_matches: dict[str, list[CompletedTrade]] = {
        "dip_buy": [],
        "momentum_chase": [],
        "volume_spike": [],
        "volatility_play": [],
    }
    for trade in profitable:
        context = context_by_symbol.get(trade.token_symbol.upper())
        if context is None:
            continue
        if (context.price_change_1h_pct or 0.0) <= -10:
            factor_matches["dip_buy"].append(trade)
        if (context.price_change_1h_pct or 0.0) >= 8:
            factor_matches["momentum_chase"].append(trade)
        if (context.volume_to_liquidity_ratio or 0.0) >= 1.5:
            factor_matches["volume_spike"].append(trade)
        if context.volatility_regime in {"high", "extreme"}:
            factor_matches["volatility_play"].append(trade)
    descriptions = {
        "dip_buy": "Entry when price retraced sharply over the last hour.",
        "momentum_chase": "Entry when token was already accelerating upward.",
        "volume_spike": "Entry when volume-to-liquidity ratio indicated strong participation.",
        "volatility_play": "Entry when volatility regime was elevated.",
    }
    distilled: list[EntryFactor] = []
    for factor_type, matches in factor_matches.items():
        if not matches:
            continue
        avg_pnl = sum(trade.pnl_pct for trade in matches) / len(matches)
        frequency = len(matches) / len(profitable)
        confidence = min(0.9, 0.2 + frequency * 0.7)
        distilled.append(
            EntryFactor(
                factor_type=factor_type,
                description=descriptions[factor_type],
                frequency=frequency,
                avg_pnl_when_present=avg_pnl,
                confidence=confidence,
                metadata={"match_count": len(matches), "profitable_trade_count": len(profitable)},
            )
        )
    distilled.sort(key=lambda item: (item.frequency, item.avg_pnl_when_present), reverse=True)
    if distilled:
        return distilled[:5]
    short_hold_winners = [trade for trade in profitable if trade.holding_seconds and trade.holding_seconds <= 30 * 60]
    split_position_winners = [
        trade
        for trade in profitable
        if float((trade.metadata or {}).get("matched_token_amount") or 0.0) > 0.0
    ]
    fallback: list[EntryFactor] = []
    if short_hold_winners:
        fallback.append(
            EntryFactor(
                factor_type="momentum_chase",
                description="Fallback inferred from profitable short-hold trades when direct market context is unavailable.",
                frequency=len(short_hold_winners) / len(profitable),
                avg_pnl_when_present=sum(trade.pnl_pct for trade in short_hold_winners) / len(short_hold_winners),
                confidence=min(0.55, 0.2 + (len(short_hold_winners) / len(profitable)) * 0.4),
                metadata={"source_mode": "completed_trade_pattern", "match_count": len(short_hold_winners)},
            )
        )
    if split_position_winners:
        fallback.append(
            EntryFactor(
                factor_type="volume_spike",
                description="Fallback inferred from profitable split-leg participation when direct liquidity context is unavailable.",
                frequency=len(split_position_winners) / len(profitable),
                avg_pnl_when_present=sum(trade.pnl_pct for trade in split_position_winners) / len(split_position_winners),
                confidence=min(0.5, 0.2 + (len(split_position_winners) / len(profitable)) * 0.35),
                metadata={"source_mode": "completed_trade_pattern", "match_count": len(split_position_winners)},
            )
        )
    fallback.sort(key=lambda item: (item.frequency, item.avg_pnl_when_present), reverse=True)
    if fallback:
        return fallback[:5]
    return distilled[:5]


def build_signal_context(
    entry_factors: list[EntryFactor],
    risk_filters: list[RiskFilter],
    active_signals: list[dict[str, Any]],
) -> dict[str, Any]:
    hard_blocks = [item.filter_type for item in risk_filters if item.is_hard_block]
    warnings = [item.filter_type for item in risk_filters if not item.is_hard_block]
    high_severity_count = sum(1 for signal in active_signals if str(signal.get("severity") or "").lower() in {"high", "critical"})
    return {
        "top_entry_factors": [
            {
                "factor_type": item.factor_type,
                "description": item.description,
                "frequency": round(item.frequency, 4),
                "confidence": round(item.confidence, 4),
            }
            for item in entry_factors[:3]
        ],
        "hard_blocks": hard_blocks,
        "warnings": warnings,
        "active_signals": len(active_signals),
        "high_severity_count": high_severity_count,
    }
