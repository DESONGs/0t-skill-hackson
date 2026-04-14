from __future__ import annotations

import json
import math
import re
from typing import Any


QUOTE_TOKENS = {"USDT", "USDC", "DAI", "FDUSD", "TUSD", "WBNB", "BNB", "WETH", "ETH"}
WRAPPED_NATIVE = {
    "bsc": ("WBNB", "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c", 600.0),
}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _non_quote_tokens(values: list[Any] | tuple[Any, ...] | None) -> list[str]:
    items: list[str] = []
    for value in values or []:
        token = _safe_text(value)
        if token and token.upper() not in QUOTE_TOKENS and token not in items:
            items.append(token)
    return items


def _extract_price_hint(texts: list[str]) -> float | None:
    matches: list[float] = []
    for text in texts:
        for raw in re.findall(r"\$?([0-9]+(?:\.[0-9]+)?)", text):
            value = _safe_float(raw)
            if value >= 25:
                matches.append(value)
    if not matches:
        return None
    return min(matches)


def _focus_context_by_symbol(market_context: dict[str, Any]) -> dict[str, dict[str, Any]]:
    focus = list(market_context.get("focus_token_context") or [])
    indexed: dict[str, dict[str, Any]] = {}
    for item in focus:
        if not isinstance(item, dict):
            continue
        symbol = _safe_text(item.get("symbol")).upper()
        if symbol and symbol not in indexed:
            indexed[symbol] = item
    return indexed


def _merged_market_context(context: dict[str, Any], strategy: dict[str, Any]) -> dict[str, Any]:
    merged = dict((strategy.get("metadata") or {}).get("market_context") or {})
    merged.update(dict(context.get("market_context") or {}))
    return merged


def _merged_signal_context(context: dict[str, Any], strategy: dict[str, Any]) -> dict[str, Any]:
    merged = dict((strategy.get("metadata") or {}).get("signal_context") or {})
    merged.update(dict(context.get("signal_context") or {}))
    return merged


def _pick_target_token(context: dict[str, Any], profile: dict[str, Any], strategy: dict[str, Any], market_context: dict[str, Any]) -> str:
    explicit = _safe_text(context.get("target_token"))
    if explicit:
        return explicit
    focus_index = _focus_context_by_symbol(market_context)
    candidates = _non_quote_tokens(list(context.get("candidate_tokens") or []) + list((strategy.get("metadata") or {}).get("preferred_tokens") or []))
    for token in candidates:
        if token.upper() in focus_index:
            return token
    if candidates:
        return candidates[0]
    preferred = _non_quote_tokens(profile.get("preferred_tokens") or [])
    if preferred:
        return preferred[0]
    return "watchlist"


def _resolve_token(symbol: str, token_catalog: dict[str, Any], execution_intent: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    token = dict(token_catalog.get(symbol) or {})
    metadata = dict(execution_intent.get("metadata") or {})
    if not token and symbol == _safe_text(metadata.get("default_target_token")):
        token = {
            "symbol": symbol,
            "token_address": metadata.get("default_target_token_address"),
            "chain": metadata.get("chain") or profile.get("chain"),
        }
    return token


def _source_meta(chain: str, route_symbols: list[str], execution_intent: dict[str, Any], token_catalog: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    metadata = dict(execution_intent.get("metadata") or {})
    prefer_wrapped = "WBNB" in " ".join(str(item) for item in profile.get("execution_rules") or []).upper()
    wrapped = WRAPPED_NATIVE.get(chain)
    if prefer_wrapped and wrapped and wrapped[0] in route_symbols:
        return {"symbol": wrapped[0], "token_address": wrapped[1], "chain": chain, "unit_price_usd": wrapped[2]}
    default_symbol = _safe_text(metadata.get("default_source_token"))
    default_address = _safe_text(metadata.get("default_source_token_address"))
    if default_symbol and default_address:
        return {"symbol": default_symbol, "token_address": default_address, "chain": chain, "unit_price_usd": _safe_float(metadata.get("default_source_unit_price_usd"), wrapped[2] if wrapped else 1.0)}
    for symbol in route_symbols:
        token = dict(token_catalog.get(symbol) or {})
        if token:
            return token
    if wrapped:
        return {"symbol": wrapped[0], "token_address": wrapped[1], "chain": chain, "unit_price_usd": wrapped[2]}
    return {}


def _infer_route(context: dict[str, Any], execution_intent: dict[str, Any], profile: dict[str, Any], target_token: str) -> list[str]:
    explicit = [str(item).strip() for item in context.get("preferred_route") or [] if _safe_text(item)]
    if explicit:
        return explicit if explicit[-1] == target_token else [*explicit, target_token]
    route: list[str] = []
    rules = " ".join(str(item) for item in profile.get("execution_rules") or []).upper()
    for symbol in list(context.get("available_routes") or []) + list(execution_intent.get("route_preferences") or []):
        text = _safe_text(symbol)
        if text and text.upper() in QUOTE_TOKENS and text not in route:
            route.append(text)
    if "WBNB" in rules and "WBNB" not in route:
        route.insert(0, "WBNB")
    if not route:
        route = ["USDC"]
    if route[-1] != target_token:
        route.append(target_token)
    return route


def _match_condition(
    condition: dict[str, Any],
    *,
    context: dict[str, Any],
    profile: dict[str, Any],
    signal_context: dict[str, Any],
    market_context: dict[str, Any],
    target_context: dict[str, Any],
    trade_statistics: dict[str, Any],
) -> tuple[bool, str]:
    condition_text = _safe_text(condition.get("condition")).lower()
    active_factors = {str(item.get("factor_type") or "").strip() for item in signal_context.get("top_entry_factors") or [] if isinstance(item, dict)}
    if "entry_factor" in str(condition.get("data_source") or "").lower() and active_factors:
        return True, "signal_context.top_entry_factors matched"
    if "asia-open" in condition_text and "asia-open" in [str(item) for item in profile.get("active_windows") or []]:
        return True, "profile.active_windows includes asia-open"
    if "split" in condition_text or "pyramid" in condition_text:
        legs = int(trade_statistics.get("avg_position_splits") or condition.get("metadata", {}).get("legs") or 1)
        if legs >= 2:
            return True, "trade_statistics.avg_position_splits >= 2"
    if "quote token" in condition_text or "wbnb" in condition_text:
        routes = [str(item).upper() for item in context.get("available_routes") or []]
        if "WBNB" in routes or "WBNB" in " ".join(str(item) for item in profile.get("execution_rules") or []).upper():
            return True, "available route includes WBNB"
    if "liquidity" in condition_text or "volume" in condition_text or "momentum" in condition_text:
        if target_context and (
            target_context.get("vol_liq_ratio") is not None
            or target_context.get("price_1h_pct") is not None
            or target_context.get("price_24h_pct") is not None
        ):
            return True, "target market context available"
    market_bias = _safe_text(context.get("market_bias")).lower()
    if any(marker in condition_text for marker in ("bullish", "breakout", "burst")) and market_bias in {"bullish", "up"}:
        return True, "market_bias supports breakout condition"
    return False, "no direct evidence"


def build_primary_payload(
    *,
    summary: str,
    profile: dict[str, Any],
    strategy: dict[str, Any],
    execution_intent: dict[str, Any],
    token_catalog: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    market_context = _merged_market_context(context, strategy)
    signal_context = _merged_signal_context(context, strategy)
    trade_statistics = dict((strategy.get("metadata") or {}).get("trade_statistics") or {})
    focus_index = _focus_context_by_symbol(market_context)
    target_token = _pick_target_token(context, profile, strategy, market_context)
    target_context = dict(focus_index.get(target_token.upper()) or {})
    hard_blocks = [str(item) for item in signal_context.get("hard_blocks") or [] if _safe_text(item)]
    warnings = [str(item) for item in signal_context.get("warnings") or [] if _safe_text(item)]
    decision_trace: list[dict[str, Any]] = []
    matched_entry_conditions: list[dict[str, Any]] = []
    total_weight = 0.0
    matched_weight = 0.0
    for condition in list(strategy.get("entry_conditions") or []):
        if not isinstance(condition, dict):
            continue
        weight = max(0.05, _safe_float(condition.get("weight"), 0.2))
        matched, reason = _match_condition(
            condition,
            context=context,
            profile=profile,
            signal_context=signal_context,
            market_context=market_context,
            target_context=target_context,
            trade_statistics=trade_statistics,
        )
        total_weight += weight
        if matched:
            matched_weight += weight
            matched_entry_conditions.append(
                {
                    "condition": condition.get("condition"),
                    "data_source": condition.get("data_source"),
                    "weight": round(weight, 4),
                    "reason": reason,
                }
            )
        decision_trace.append(
            {
                "condition": condition.get("condition"),
                "matched": matched,
                "weight": round(weight, 4),
                "reason": reason,
            }
        )
    entry_score = matched_weight / total_weight if total_weight else 0.0
    blocking_reasons: list[str] = []
    if hard_blocks:
        blocking_reasons.extend([f"hard_block:{item}" for item in hard_blocks])
    if not focus_index and not (strategy.get("metadata") or {}).get("entry_factors") and not (strategy.get("metadata") or {}).get("risk_filters"):
        blocking_reasons.append("missing_market_and_signal_features")
    macro_regime = _safe_text((market_context.get("macro") or {}).get("regime")).lower()
    if macro_regime == "risk_off" and blocking_reasons:
        blocking_reasons.append("macro_risk_off")
    action = "watch"
    dominant_actions = [str(item).lower() for item in profile.get("dominant_actions") or []]
    market_bias = _safe_text(context.get("market_bias")).lower()
    if not blocking_reasons:
        if market_bias in {"bearish", "down"} and "sell" in dominant_actions and target_context.get("price_1h_pct", 0) < 0:
            action = "sell"
        elif entry_score >= 0.45 or signal_context.get("top_entry_factors"):
            action = "buy"
        elif dominant_actions and dominant_actions[0] in {"buy", "swap"} and market_bias in {"bullish", "up", "range"}:
            action = "buy"
    feature_ready = bool(focus_index) or bool((strategy.get("metadata") or {}).get("entry_factors")) or bool((strategy.get("metadata") or {}).get("risk_filters"))
    example_readiness = "strategy_ready" if feature_ready else "blocked_by_missing_features"
    confidence = min(
        0.95,
        _safe_float(profile.get("confidence"), 0.25) * 0.55
        + entry_score * 0.3
        + (0.05 if signal_context.get("top_entry_factors") else 0.0)
        + (0.05 if target_context else 0.0),
    )
    if blocking_reasons:
        confidence = min(confidence, 0.35)
    route_symbols = _infer_route(context, execution_intent, profile, target_token)
    target_meta = _resolve_token(target_token, token_catalog, execution_intent, profile)
    source_meta = _source_meta(_safe_text(profile.get("chain") or context.get("chain") or "bsc"), route_symbols, execution_intent, token_catalog, profile)
    sizing = dict(strategy.get("position_sizing") or {})
    sizing_range = sizing.get("usd_range") if isinstance(sizing.get("usd_range"), list) else []
    desired_notional = _safe_float(context.get("desired_notional_usd"), 0.0)
    if desired_notional <= 0 and sizing_range:
        desired_notional = max(_safe_float(sizing_range[0]), _safe_float(sizing_range[-1]))
    if desired_notional <= 0:
        desired_notional = _safe_float(sizing.get("median_usd"), 0.0) or _safe_float(sizing.get("max_usd"), 0.0)
    if desired_notional <= 0:
        desired_notional = _extract_price_hint(list(profile.get("execution_rules") or []) + [_safe_text(profile.get("sizing_note"))]) or 300.0
    planned_legs = max(1, int(execution_intent.get("leg_count") or sizing.get("legs") or 1))
    per_leg_usd = round(max(1.0, desired_notional / planned_legs), 2)
    matched_market_context = {
        "target_token": target_token,
        "context": target_context,
        "macro": dict(market_context.get("macro") or {}),
    }
    recommendation = {
        "action": action,
        "confidence": round(confidence, 4),
        "entry_score": round(entry_score, 4),
        "rationale": [
            profile.get("summary") or "wallet-style profile available",
            strategy.get("summary") or "strategy summary unavailable",
            *(profile.get("execution_rules") or []),
        ],
        "guardrails": profile.get("anti_patterns") or [],
    }
    context_sources = _json_safe(
        {
            "style_profile": {"kind": "static_payload"},
            "strategy_spec": {"kind": "static_payload"},
            "execution_intent": {"kind": "static_payload"},
            "token_catalog": {"kind": "static_payload"},
            "input_context": {"kind": "runtime_input"},
            "dynamic": list(context.get("context_sources") or [])
            + list((strategy.get("metadata") or {}).get("context_sources") or []),
        }
    )
    trade_plan = {
        "mode": "style-simulated-trade",
        "chain": _safe_text(profile.get("chain") or context.get("chain") or "bsc"),
        "wallet_address": profile.get("wallet"),
        "entry_action": recommendation.get("action"),
        "target_token": target_token,
        "target_token_address": target_meta.get("token_address"),
        "route": route_symbols,
        "desired_notional_usd": round(desired_notional, 2),
        "max_leg_usd": round(max(per_leg_usd, _safe_float(sizing.get("max_usd"), per_leg_usd)), 2),
        "leg_count": planned_legs,
        "per_leg_usd": per_leg_usd,
        "execution_source_symbol": source_meta.get("symbol"),
        "execution_source_address": source_meta.get("token_address"),
        "execution_source_unit_price_usd": source_meta.get("unit_price_usd"),
        "execution_source_readable_amount": round(per_leg_usd / float(source_meta.get("unit_price_usd")), 8) if source_meta.get("unit_price_usd") else None,
        "execution_windows": [str(item) for item in profile.get("active_windows") or [] if _safe_text(item)],
        "burst_profile": _safe_text(context.get("burst_profile") or trade_statistics.get("burst_profile") or profile.get("execution_tempo")),
        "tempo": profile.get("execution_tempo"),
        "guardrails": profile.get("anti_patterns") or [],
        "rules": profile.get("execution_rules") or [],
        "execution_intent_mode": execution_intent.get("mode"),
        "entry_score": round(entry_score, 4),
    }
    return {
        "ok": True,
        "action": "primary",
        "summary": summary,
        "style_profile": _json_safe(profile),
        "strategy": _json_safe(strategy),
        "execution_intent": _json_safe(execution_intent),
        "input_context": _json_safe(context),
        "recommendation": _json_safe(recommendation),
        "trade_plan": _json_safe(trade_plan),
        "decision_trace": _json_safe(decision_trace),
        "matched_entry_conditions": _json_safe(matched_entry_conditions),
        "matched_market_context": _json_safe(matched_market_context),
        "blocking_reasons": list(blocking_reasons),
        "example_readiness": example_readiness,
        "context_sources": context_sources,
        "artifacts": [],
        "metadata": {"skill_family": "wallet_style"},
    }
