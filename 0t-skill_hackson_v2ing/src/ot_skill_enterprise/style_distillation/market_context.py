from __future__ import annotations

from dataclasses import dataclass, field
import math
from statistics import pstdev
from typing import Any


_MACRO_TOKEN_REFS: dict[str, dict[str, dict[str, str]]] = {
    "bsc": {
        "BTC": {
            "identifier": "bsc:0x7130d2a12b9bcbaea7010387f9f95b8d0f1ead9c",
            "chain": "bsc",
            "token_address": "0x7130d2a12b9bcbaea7010387f9f95b8d0f1ead9c",
            "symbol": "BTC",
        },
        "ETH": {
            "identifier": "bsc:0x2170ed0880ac9a755fd29b2688956bd959f933f8",
            "chain": "bsc",
            "token_address": "0x2170ed0880ac9a755fd29b2688956bd959f933f8",
            "symbol": "ETH",
        },
    },
}


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


def _pct_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in {None, 0}:
        return None
    return round(((current - previous) / previous) * 100.0, 8)


def _momentum_label(change_1h: float | None, change_24h: float | None) -> str:
    if change_1h is None and change_24h is None:
        return "unknown"
    if (change_1h or 0.0) >= 8 or (change_24h or 0.0) >= 25:
        return "pumping"
    if (change_1h or 0.0) <= -8 or (change_24h or 0.0) <= -20:
        return "dumping"
    if (change_1h or 0.0) > 0 and (change_24h or 0.0) < 0:
        return "recovering"
    return "ranging"


def _volatility_regime(closes: list[float]) -> str:
    if len(closes) < 4:
        return "unknown"
    mean = sum(closes) / len(closes)
    if mean <= 0:
        return "unknown"
    volatility_pct = (pstdev(closes) / mean) * 100.0
    if volatility_pct >= 18:
        return "extreme"
    if volatility_pct >= 10:
        return "high"
    if volatility_pct >= 4:
        return "normal"
    return "low"


@dataclass(slots=True)
class TokenMarketContext:
    symbol: str
    token_address: str | None
    price_now: float | None
    price_change_1h_pct: float | None
    price_change_24h_pct: float | None
    momentum_label: str
    volatility_regime: str
    volume_to_liquidity_ratio: float | None
    liquidity_usd: float | None
    volume_24h_usd: float | None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "token_address": self.token_address,
            "price_now": self.price_now,
            "price_change_1h_pct": self.price_change_1h_pct,
            "price_change_24h_pct": self.price_change_24h_pct,
            "momentum_label": self.momentum_label,
            "volatility_regime": self.volatility_regime,
            "volume_to_liquidity_ratio": self.volume_to_liquidity_ratio,
            "liquidity_usd": self.liquidity_usd,
            "volume_24h_usd": self.volume_24h_usd,
            "metadata": dict(self.metadata),
        }

    def to_compact(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "token_address": self.token_address,
            "price_1h_pct": self.price_change_1h_pct,
            "price_24h_pct": self.price_change_24h_pct,
            "momentum": self.momentum_label,
            "volatility_regime": self.volatility_regime,
            "vol_liq_ratio": self.volume_to_liquidity_ratio,
        }


@dataclass(slots=True)
class MacroContext:
    btc_24h_change_pct: float | None
    eth_24h_change_pct: float | None
    market_regime: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "btc_24h_change_pct": self.btc_24h_change_pct,
            "eth_24h_change_pct": self.eth_24h_change_pct,
            "market_regime": self.market_regime,
            "metadata": dict(self.metadata),
        }

    def to_compact(self) -> dict[str, Any]:
        return {
            "btc_24h_pct": self.btc_24h_change_pct,
            "eth_24h_pct": self.eth_24h_change_pct,
            "regime": self.market_regime,
        }


def build_macro_token_refs(chain: str) -> dict[str, dict[str, str]]:
    return dict(_MACRO_TOKEN_REFS.get(str(chain or "").strip().lower(), {}))


def summarize_market_payload(payload: dict[str, Any]) -> TokenMarketContext:
    selected_pair = dict(payload.get("selected_pair") or {})
    base_token = dict(selected_pair.get("base_token_ref") or {})
    market_snapshot = dict(payload.get("market_snapshot") or {})
    ohlcv = [point for point in payload.get("ohlcv", []) if isinstance(point, dict)]
    closes = [_safe_float(point.get("close")) for point in ohlcv]
    close_values = [value for value in closes if value is not None]
    latest = close_values[-1] if close_values else _safe_float(market_snapshot.get("price_usd"))
    previous_1h = close_values[-2] if len(close_values) >= 2 else None
    previous_24h = close_values[0] if len(close_values) >= 24 else (close_values[0] if len(close_values) >= 2 else None)
    liquidity = _safe_float(market_snapshot.get("liquidity_usd")) or _safe_float(selected_pair.get("liquidity_usd"))
    volume_24h = _safe_float(market_snapshot.get("volume_24h_usd")) or _safe_float(selected_pair.get("volume_24h_usd"))
    vol_liq_ratio = None
    if liquidity and liquidity > 0 and volume_24h is not None:
        vol_liq_ratio = round(volume_24h / liquidity, 8)
    price_change_1h = _pct_change(latest, previous_1h)
    if price_change_1h is None:
        price_change_1h = _safe_float(market_snapshot.get("price_change_1h")) or _safe_float(selected_pair.get("price_change_1h"))
    price_change_24h = _pct_change(latest, previous_24h)
    if price_change_24h is None:
        price_change_24h = _safe_float(market_snapshot.get("price_change_24h")) or _safe_float(selected_pair.get("price_change_24h"))
    symbol = str(
        base_token.get("symbol")
        or selected_pair.get("target_symbol")
        or selected_pair.get("token_symbol")
        or selected_pair.get("identifier")
        or "TOKEN"
    ).strip()
    token_address = _safe_text(base_token.get("token_address") or selected_pair.get("target_token") or selected_pair.get("token_address"))
    return TokenMarketContext(
        symbol=symbol,
        token_address=token_address,
        price_now=latest,
        price_change_1h_pct=price_change_1h,
        price_change_24h_pct=price_change_24h,
        momentum_label=_momentum_label(price_change_1h, price_change_24h),
        volatility_regime=_volatility_regime(close_values),
        volume_to_liquidity_ratio=vol_liq_ratio,
        liquidity_usd=liquidity,
        volume_24h_usd=volume_24h,
        metadata={
            "ohlcv_points": len(ohlcv),
            "flow_summary": dict(payload.get("flow_summary") or {}),
            "pair_address": selected_pair.get("pair_address"),
            "market_status": market_snapshot.get("status"),
        },
    )


def summarize_macro_context(chain: str, macro_payloads: dict[str, dict[str, Any]]) -> MacroContext:
    btc_payload = macro_payloads.get("BTC") or {}
    eth_payload = macro_payloads.get("ETH") or {}
    btc = summarize_market_payload(btc_payload) if btc_payload else None
    eth = summarize_market_payload(eth_payload) if eth_payload else None
    btc_change = btc.price_change_24h_pct if btc else None
    eth_change = eth.price_change_24h_pct if eth else None
    average = 0.0
    count = 0
    for value in (btc_change, eth_change):
        if value is None:
            continue
        average += value
        count += 1
    average = average / count if count else 0.0
    if count == 0:
        regime = "unknown"
    elif average >= 4:
        regime = "risk_on"
    elif average <= -4:
        regime = "risk_off"
    else:
        regime = "neutral"
    return MacroContext(
        btc_24h_change_pct=btc_change,
        eth_24h_change_pct=eth_change,
        market_regime=regime,
        metadata={"chain": chain, "sources": [key for key, value in macro_payloads.items() if value]},
    )


def summarize_focus_token_contexts(market_payloads: list[dict[str, Any]]) -> list[TokenMarketContext]:
    contexts = [summarize_market_payload(payload) for payload in market_payloads if isinstance(payload, dict)]
    contexts.sort(
        key=lambda item: (
            abs(item.price_change_1h_pct or 0.0) + abs(item.price_change_24h_pct or 0.0),
            item.volume_to_liquidity_ratio or 0.0,
        ),
        reverse=True,
    )
    deduped: list[TokenMarketContext] = []
    seen: set[str] = set()
    for item in contexts:
        key = (item.token_address or item.symbol).lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def market_context_ready(contexts: list[TokenMarketContext]) -> bool:
    return any(
        item.token_address
        and (
            item.price_change_1h_pct is not None
            or item.price_change_24h_pct is not None
            or item.volume_to_liquidity_ratio is not None
        )
        for item in contexts
    )
