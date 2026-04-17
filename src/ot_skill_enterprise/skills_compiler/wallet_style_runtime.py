from __future__ import annotations

import json
import math
import re
from typing import Any

from ot_skill_enterprise.chain_assets import chain_benchmark_defaults, chain_quote_symbols, chain_wrapped_native

QUOTE_TOKENS = {"USDT", "USDC", "DAI", "FDUSD", "TUSD"} | chain_quote_symbols()
NO_STABLE_ARCHETYPE = "no_stable_archetype"
ARCHETYPE_FIELD_NAMES = (
    "primary_archetype",
    "secondary_archetypes",
    "behavioral_patterns",
    "archetype_confidence",
    "archetype_evidence_summary",
    "archetype_token_preference",
)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _safe_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _safe_text(value).lower() in {"1", "true", "yes", "y", "on"}


def _is_evm_address(value: Any) -> bool:
    text = _safe_text(value)
    return len(text) == 42 and text.startswith("0x") and all(char in "0123456789abcdefABCDEF" for char in text[2:])


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _compact_text(value: Any, *, max_chars: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= max_chars:
        return text
    trimmed = text[: max_chars - 3].rsplit(" ", 1)[0].strip()
    if not trimmed:
        trimmed = text[: max_chars - 3].strip()
    return f"{trimmed}..."


def _first_text(*values: Any) -> str:
    for value in values:
        text = _safe_text(value)
        if text:
            return text
    return ""


def _unique_texts(values: Any) -> list[str]:
    items: list[str] = []
    for value in values or []:
        text = _safe_text(value)
        if text and text not in items:
            items.append(text)
    return items


def _humanize_label(value: str) -> str:
    parts = []
    for part in str(value or "").replace("-", " ").replace("_", " ").split():
        parts.append("frequency" if part == "freq" else part)
    return " ".join(parts).strip()


def _pattern_labels(patterns: Any) -> list[str]:
    labels: list[str] = []
    for item in patterns or []:
        if isinstance(item, dict):
            label = _first_text(
                item.get("pattern_label"),
                item.get("label"),
                item.get("name"),
                item.get("pattern"),
                item.get("pattern_type"),
            )
        else:
            label = _safe_text(item)
        if label and label not in labels:
            labels.append(label)
    return labels


def _should_replace_archetype_field(field_name: str, current: Any, incoming: Any) -> bool:
    if incoming is None:
        return False
    if current is None:
        return True
    if field_name == "primary_archetype":
        current_text = _safe_text(current).lower()
        incoming_text = _safe_text(incoming).lower()
        if not current_text:
            return bool(incoming_text)
        if current_text == NO_STABLE_ARCHETYPE and incoming_text and incoming_text != NO_STABLE_ARCHETYPE:
            return True
        return False
    if field_name in {"secondary_archetypes", "behavioral_patterns", "archetype_token_preference"}:
        return not _unique_texts(current) and bool(_unique_texts(incoming))
    if field_name == "archetype_confidence":
        return _safe_float(current, 0.0) <= 0.0 and _safe_float(incoming, 0.0) > 0.0
    if field_name == "archetype_evidence_summary":
        return not _first_text(current) and bool(_first_text(incoming))
    return False


def _merge_archetype_source(target: dict[str, Any], payload: dict[str, Any]) -> None:
    nested = payload.get("archetype")
    if isinstance(nested, dict):
        _merge_archetype_source(target, nested)

    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        nested_metadata = metadata.get("archetype")
        if isinstance(nested_metadata, dict):
            _merge_archetype_source(target, nested_metadata)
        for field_name in ARCHETYPE_FIELD_NAMES:
            if field_name in metadata and _should_replace_archetype_field(field_name, target.get(field_name), metadata.get(field_name)):
                target[field_name] = metadata.get(field_name)
        for alias, target_name in (
            ("primary_label", "primary_archetype"),
            ("trading_archetype", "primary_archetype"),
            ("trading_archetype_label", "primary_archetype"),
            ("label", "primary_archetype"),
            ("confidence", "archetype_confidence"),
            ("evidence", "archetype_evidence_summary"),
            ("token_preference", "archetype_token_preference"),
            ("preferred_tokens", "archetype_token_preference"),
        ):
            if alias in metadata and _should_replace_archetype_field(target_name, target.get(target_name), metadata.get(alias)):
                target[target_name] = metadata.get(alias)

    for field_name in ARCHETYPE_FIELD_NAMES:
        if field_name in payload and _should_replace_archetype_field(field_name, target.get(field_name), payload.get(field_name)):
            target[field_name] = payload.get(field_name)
    for alias, target_name in (
        ("primary_label", "primary_archetype"),
        ("trading_archetype", "primary_archetype"),
        ("trading_archetype_label", "primary_archetype"),
        ("label", "primary_archetype"),
        ("confidence", "archetype_confidence"),
        ("evidence", "archetype_evidence_summary"),
        ("token_preference", "archetype_token_preference"),
        ("preferred_tokens", "archetype_token_preference"),
    ):
        if alias in payload and _should_replace_archetype_field(target_name, target.get(target_name), payload.get(alias)):
            target[target_name] = payload.get(alias)


def _normalize_archetype_payload(
    profile: dict[str, Any],
    strategy: dict[str, Any],
    context: dict[str, Any],
    explicit: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    sources: list[dict[str, Any]] = []
    for payload in (
        explicit,
        profile.get("archetype"),
        (profile.get("metadata") or {}).get("archetype") if isinstance(profile.get("metadata"), dict) else None,
        profile.get("metadata") if isinstance(profile.get("metadata"), dict) else None,
        strategy.get("archetype"),
        (strategy.get("metadata") or {}).get("archetype"),
        strategy.get("metadata") if isinstance(strategy.get("metadata"), dict) else None,
        context.get("archetype"),
        profile,
    ):
        if isinstance(payload, dict):
            sources.append(payload)

    combined: dict[str, Any] = {}
    for payload in sources:
        _merge_archetype_source(combined, payload)

    primary = _first_text(
        combined.get("primary_archetype"),
        combined.get("primary_label"),
        combined.get("trading_archetype"),
        combined.get("style_label"),
    )
    secondary = _unique_texts(combined.get("secondary_archetypes") or [])
    raw_patterns = combined.get("behavioral_patterns") or []
    pattern_labels = _pattern_labels(raw_patterns)
    confidence = _safe_float(combined.get("archetype_confidence"), 0.0)
    if not confidence:
        confidence = _safe_float(combined.get("confidence"), 0.0)
    evidence_summary = _first_text(
        combined.get("archetype_evidence_summary"),
        combined.get("evidence_summary"),
        combined.get("evidence"),
    )
    token_preference = _unique_texts(
        combined.get("archetype_token_preference")
        or combined.get("token_preference")
        or combined.get("preferred_tokens")
        or []
    )
    if not primary and not secondary and not pattern_labels and not evidence_summary and not token_preference:
        return None
    if not primary:
        primary = NO_STABLE_ARCHETYPE
    if not evidence_summary and pattern_labels:
        evidence_summary = ", ".join(pattern_labels)
    summary_parts: list[str] = []
    display_primary = _first_text(combined.get("style_label"), primary)
    if primary == NO_STABLE_ARCHETYPE:
        summary_parts.append("no stable archetype yet")
    else:
        summary_parts.append(f"{_humanize_label(display_primary)} trader")
    if secondary:
        summary_parts.append(f"secondary patterns: {', '.join(_humanize_label(item) for item in secondary[:3])}")
    if pattern_labels:
        summary_parts.append(f"behavioral patterns: {', '.join(_humanize_label(item) for item in pattern_labels[:3])}")
    if token_preference:
        summary_parts.append(f"token preference: {', '.join(_humanize_label(item) for item in token_preference[:3])}")
    if confidence:
        summary_parts.append(f"confidence {confidence:.2f}")
    if evidence_summary:
        summary_parts.append(f"evidence: {_compact_text(evidence_summary, max_chars=96)}")
    return {
        "primary_archetype": primary,
        "secondary_archetypes": secondary,
        "behavioral_patterns": _json_safe(raw_patterns),
        "behavioral_pattern_labels": pattern_labels,
        "archetype_confidence": round(confidence, 4) if confidence else 0.0,
        "archetype_evidence_summary": evidence_summary,
        "archetype_token_preference": token_preference,
        "summary": "; ".join(summary_parts),
    }


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
        symbol = _safe_text(item.get("symbol") or item.get("token_symbol") or item.get("token_name") or item.get("name")).upper()
        if symbol and symbol not in indexed:
            indexed[symbol] = item
    return indexed


def _focus_tokens(market_context: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for item in list(market_context.get("focus_token_context") or []):
        if not isinstance(item, dict):
            continue
        symbol = _safe_text(item.get("symbol") or item.get("token_symbol") or item.get("token_name") or item.get("name"))
        if symbol and symbol.upper() not in QUOTE_TOKENS and symbol not in values:
            values.append(symbol)
    return values


def _match_focus_context(target: str, market_context: dict[str, Any]) -> dict[str, Any]:
    needle = _safe_text(target)
    if not needle:
        return {}
    upper = needle.upper()
    lower = needle.lower()
    for item in list(market_context.get("focus_token_context") or []):
        if not isinstance(item, dict):
            continue
        aliases = {
            _safe_text(item.get("symbol")).upper(),
            _safe_text(item.get("token_symbol")).upper(),
            _safe_text(item.get("token_name")).upper(),
            _safe_text(item.get("name")).upper(),
        }
        token_address = _safe_text(item.get("token_address") or item.get("address")).lower()
        if upper in aliases or (_is_evm_address(needle) and token_address == lower):
            return dict(item)
    return {}


def _merged_market_context(context: dict[str, Any], strategy: dict[str, Any]) -> dict[str, Any]:
    merged = dict((strategy.get("metadata") or {}).get("market_context") or {})
    merged.update(dict(context.get("market_context") or {}))
    return merged


def _merged_signal_context(context: dict[str, Any], strategy: dict[str, Any]) -> dict[str, Any]:
    merged = dict((strategy.get("metadata") or {}).get("signal_context") or {})
    merged.update(dict(context.get("signal_context") or {}))
    return merged


def _pick_target_token(context: dict[str, Any], profile: dict[str, Any], strategy: dict[str, Any], market_context: dict[str, Any]) -> tuple[str, str]:
    explicit = _safe_text(context.get("target_token"))
    if explicit:
        return explicit, "explicit_target"
    focus_tokens = _focus_tokens(market_context)
    focus_index = {item.upper() for item in focus_tokens}
    candidates = _non_quote_tokens(list(context.get("candidate_tokens") or []) + list((strategy.get("metadata") or {}).get("preferred_tokens") or []))
    for token in candidates:
        if token.upper() in focus_index:
            return token, "runtime_focus"
    if focus_tokens:
        return focus_tokens[0], "runtime_focus"
    if candidates:
        return candidates[0], "candidate_watchlist"
    preferred = _non_quote_tokens(profile.get("preferred_tokens") or [])
    if preferred:
        return preferred[0], "profile_preferred"
    return "watchlist", "fallback_watchlist"


def _resolve_token(
    symbol: str,
    token_catalog: dict[str, Any],
    execution_intent: dict[str, Any],
    profile: dict[str, Any],
    *,
    target_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = dict(execution_intent.get("metadata") or {})
    chain = _safe_text(metadata.get("chain") or profile.get("chain"))
    context_token = dict(target_context or {})
    context_address = _safe_text(context_token.get("token_address") or context_token.get("address"))
    context_price = (
        _safe_float(context_token.get("price_now"))
        or _safe_float(context_token.get("price"))
        or _safe_float(context_token.get("unit_price_usd"))
        or None
    )
    context_symbol = _safe_text(
        context_token.get("symbol")
        or context_token.get("token_symbol")
        or context_token.get("token_name")
        or context_token.get("name")
        or symbol
    )
    if context_address and _is_evm_address(context_address):
        return {
            "symbol": context_symbol or symbol,
            "token_address": context_address,
            "chain": chain,
            "unit_price_usd": context_price,
        }
    if _is_evm_address(symbol):
        return {
            "symbol": context_symbol or symbol,
            "token_address": symbol,
            "chain": chain,
            "unit_price_usd": context_price,
        }
    for key in (symbol, symbol.upper(), symbol.lower()):
        token = dict(token_catalog.get(key) or {})
        if token:
            return token
    target_lower = _safe_text(symbol).lower()
    for token in list(token_catalog.values()):
        if not isinstance(token, dict):
            continue
        if _safe_text(token.get("token_address")).lower() == target_lower:
            return dict(token)
    wrapped = chain_wrapped_native(chain)
    if wrapped and _safe_text(symbol).upper() in chain_quote_symbols(chain):
        return {
            "symbol": wrapped[0],
            "token_address": wrapped[1],
            "chain": chain,
            "unit_price_usd": wrapped[2],
        }
    return {}


def _source_meta(chain: str, route_symbols: list[str], execution_intent: dict[str, Any], token_catalog: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    metadata = dict(execution_intent.get("metadata") or {})
    wrapped = chain_wrapped_native(chain)
    wrapped_symbols = chain_quote_symbols(chain)
    rules_upper = " ".join(str(item) for item in profile.get("execution_rules") or []).upper()
    prefer_wrapped = any(symbol in rules_upper for symbol in wrapped_symbols)
    if prefer_wrapped and wrapped and wrapped[0] in route_symbols:
        return {"symbol": wrapped[0], "token_address": wrapped[1], "chain": chain, "unit_price_usd": wrapped[2]}
    default_symbol = _safe_text(metadata.get("default_source_token"))
    default_address = _safe_text(metadata.get("default_source_token_address"))
    if default_symbol and default_address:
        unit_price = _safe_float(metadata.get("default_source_unit_price_usd"), wrapped[2] if wrapped and wrapped[2] is not None else 0.0)
        return {"symbol": default_symbol, "token_address": default_address, "chain": chain, "unit_price_usd": unit_price or None}
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
    chain = _safe_text(context.get("chain") or profile.get("chain"))
    default_source_symbol = _safe_text(
        dict(execution_intent.get("metadata") or {}).get("default_source_token")
        or chain_benchmark_defaults(chain).get("default_source_token")
    )
    chain_quotes = chain_quote_symbols(chain)
    rules = " ".join(str(item) for item in profile.get("execution_rules") or []).upper()
    for symbol in list(context.get("available_routes") or []) + list(execution_intent.get("route_preferences") or []):
        text = _safe_text(symbol)
        if text and text.upper() in QUOTE_TOKENS and text not in route:
            route.append(text)
    if default_source_symbol and any(symbol in rules for symbol in chain_quotes) and default_source_symbol not in route:
        route.insert(0, default_source_symbol)
    if not route:
        route = [default_source_symbol or "USDC"]
    if route[-1] != target_token:
        route.append(target_token)
    return route


def _market_discovery_defaults(
    *,
    chain: str,
    profile: dict[str, Any],
    strategy: dict[str, Any],
    execution_intent: dict[str, Any],
    context: dict[str, Any],
    source_symbol: str,
) -> dict[str, Any]:
    profile_text = " ".join(
        [
            _safe_text(profile.get("summary")),
            _safe_text(profile.get("style_label")),
            _safe_text(profile.get("risk_appetite")),
            _safe_text(profile.get("execution_tempo")),
            " ".join(str(item) for item in profile.get("execution_rules") or []),
            " ".join(str(item) for item in strategy.get("preferred_setups") or []),
        ]
    ).lower()
    aggressive = any(marker in profile_text for marker in ("degen", "aggressive", "memecoin", "microcap", "burst", "scalp"))
    discovery = dict(((execution_intent.get("metadata") or {}).get("market_discovery") or {}))
    runtime_discovery = dict(context.get("market_discovery") or {})
    if runtime_discovery:
        discovery.update(runtime_discovery)
    filters = dict(discovery.get("filters") or {})
    runtime_filters = dict(runtime_discovery.get("filters") or {})
    if runtime_filters:
        filters.update(runtime_filters)
    filters.setdefault("chain", chain)
    filters.setdefault("ranking_type", "4")
    filters.setdefault("time_frame", "2" if any(marker in profile_text for marker in ("same-minute", "burst", "scalp")) else "4")
    filters.setdefault("stable_token_filter", True)
    filters.setdefault("risk_filter", not aggressive)
    filters.setdefault("volume_min", 10000 if aggressive else 25000)
    filters.setdefault("liquidity_min", 5000 if aggressive else 15000)
    filters.setdefault("txs_min", 25 if aggressive else 50)
    if aggressive and any(marker in profile_text for marker in ("microcap", "memecoin")):
        filters.setdefault("market_cap_max", 250000000)
    tags: list[str] = []
    for marker in ("volume_spike", "same-minute-burst", "scalp", "microcap", "memecoin", "momentum", "pyramid"):
        if marker.replace("_", " ") in profile_text or marker in profile_text:
            tags.append(marker)
    discovery.setdefault("enabled", True)
    discovery.setdefault("scan_mode", "hot_tokens")
    discovery.setdefault("wss_price_enabled", True)
    discovery.setdefault("allow_target_override", False)
    discovery.setdefault("novelty_preferred", True)
    discovery.setdefault("max_candidates", 8)
    discovery.setdefault(
        "preferred_quote_symbol",
        source_symbol or _safe_text((execution_intent.get("metadata") or {}).get("default_source_token")),
    )
    discovery.setdefault("historical_tokens", _non_quote_tokens(profile.get("preferred_tokens") or []))
    discovery["style_tags"] = sorted(set(list(discovery.get("style_tags") or []) + tags))
    discovery["filters"] = filters
    return discovery


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
    chain = _safe_text(profile.get("chain") or context.get("chain") or "bsc")
    chain_quotes = chain_quote_symbols(chain)
    if "quote token" in condition_text or any(symbol.lower() in condition_text for symbol in chain_quotes):
        default_source_symbol = _safe_text(chain_benchmark_defaults(chain).get("default_source_token")).upper()
        routes = [str(item).upper() for item in context.get("available_routes") or []]
        if (
            (default_source_symbol and default_source_symbol in routes)
            or any(symbol in routes for symbol in chain_quotes)
            or any(symbol in " ".join(str(item) for item in profile.get("execution_rules") or []).upper() for symbol in chain_quotes)
        ):
            return True, "available route includes configured benchmark source"
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
    archetype: dict[str, Any] | None = None,
) -> dict[str, Any]:
    market_context = _merged_market_context(context, strategy)
    signal_context = _merged_signal_context(context, strategy)
    trade_statistics = dict((strategy.get("metadata") or {}).get("trade_statistics") or {})
    archetype_payload = _normalize_archetype_payload(profile, strategy, context, explicit=archetype)
    archetype_label = _safe_text((archetype_payload or {}).get("primary_archetype"))
    archetype_summary = _safe_text((archetype_payload or {}).get("summary"))
    focus_index = _focus_context_by_symbol(market_context)
    target_token, target_source = _pick_target_token(context, profile, strategy, market_context)
    target_context = _match_focus_context(target_token, market_context) or dict(focus_index.get(target_token.upper()) or {})
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
    chain = _safe_text(profile.get("chain") or context.get("chain") or "bsc")
    requested_target_token = target_token
    route_symbols = _infer_route(context, execution_intent, profile, target_token)
    target_meta = _resolve_token(target_token, token_catalog, execution_intent, profile, target_context=target_context)
    source_meta = _source_meta(chain, route_symbols, execution_intent, token_catalog, profile)
    market_discovery = _market_discovery_defaults(
        chain=chain,
        profile=profile,
        strategy=strategy,
        execution_intent=execution_intent,
        context=context,
        source_symbol=_safe_text(source_meta.get("symbol")),
    )
    target_address = _safe_text(target_meta.get("token_address"))
    if target_address:
        target_resolution = {
            "runtime_focus": "runtime_context",
            "explicit_target": "explicit_target",
            "candidate_watchlist": "style_watchlist_candidate",
            "profile_preferred": "style_preferred_catalog",
            "fallback_watchlist": "style_watchlist_candidate",
        }.get(target_source, "runtime_context")
    else:
        scan_requested = _safe_bool(market_discovery.get("allow_target_override")) or _safe_bool(market_discovery.get("scan_requested"))
        target_resolution = {
            "explicit_target": "market_search_pending",
            "runtime_focus": "market_search_pending",
            "candidate_watchlist": "market_scan_pending" if scan_requested else "unresolved_target",
            "profile_preferred": "market_scan_pending" if scan_requested else "unresolved_target",
            "fallback_watchlist": "market_scan_pending" if scan_requested else "unresolved_target",
        }.get(target_source, "unresolved_target")
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
            archetype_summary or (f"Archetype: {_humanize_label(archetype_label)}" if archetype_label else "Archetype unavailable"),
            profile.get("summary") or "wallet-style profile available",
            strategy.get("summary") or "strategy summary unavailable",
            *(profile.get("execution_rules") or []),
        ],
        "guardrails": profile.get("anti_patterns") or [],
        "trader_archetype": archetype_label or None,
    }
    execution_payload = _json_safe(execution_intent)
    execution_metadata = dict(execution_payload.get("metadata") or {})
    execution_metadata["market_discovery"] = _json_safe(market_discovery)
    execution_payload["metadata"] = execution_metadata
    candidate_tokens = _non_quote_tokens(
        list(context.get("candidate_tokens") or [])
        + _focus_tokens(market_context)
        + list(profile.get("preferred_tokens") or [])
    )
    context_sources = _json_safe(
        {
            "style_profile": {"kind": "static_payload"},
            "strategy_spec": {"kind": "static_payload"},
            "execution_intent": {"kind": "static_payload"},
            "token_catalog": {"kind": "static_payload"},
            "archetype": {"kind": "static_payload"},
            "input_context": {"kind": "runtime_input"},
            "dynamic": list(context.get("context_sources") or [])
            + list((strategy.get("metadata") or {}).get("context_sources") or []),
        }
    )
    trade_plan = {
        "mode": "style-simulated-trade",
        "chain": chain,
        "wallet_address": profile.get("wallet"),
        "trader_archetype": archetype_label or None,
        "trader_archetype_summary": archetype_summary or None,
        "archetype": _json_safe(archetype_payload or {}),
        "entry_action": recommendation.get("action"),
        "target_token": target_token,
        "target_token_address": target_meta.get("token_address"),
        "requested_target_token": requested_target_token,
        "target_token_resolution": target_resolution,
        "route": route_symbols,
        "desired_notional_usd": round(desired_notional, 2),
        "max_leg_usd": round(max(per_leg_usd, _safe_float(sizing.get("max_usd"), per_leg_usd)), 2),
        "leg_count": planned_legs,
        "per_leg_usd": per_leg_usd,
        "execution_source_symbol": source_meta.get("symbol"),
        "execution_source_address": source_meta.get("token_address"),
        "execution_source_unit_price_usd": source_meta.get("unit_price_usd"),
        "execution_source_readable_amount": round(per_leg_usd / float(source_meta.get("unit_price_usd")), 8) if source_meta.get("unit_price_usd") else None,
        "candidate_tokens": candidate_tokens,
        "historical_tokens": _non_quote_tokens(profile.get("preferred_tokens") or []),
        "market_context": _json_safe(market_context),
        "signal_context": _json_safe(signal_context),
        "target_token_context": _json_safe(target_context),
        "market_discovery": _json_safe(market_discovery),
        "execution_windows": [str(item) for item in profile.get("active_windows") or [] if _safe_text(item)],
        "burst_profile": _safe_text(context.get("burst_profile") or trade_statistics.get("burst_profile") or profile.get("execution_tempo")),
        "tempo": profile.get("execution_tempo"),
        "guardrails": profile.get("anti_patterns") or [],
        "rules": profile.get("execution_rules") or [],
        "execution_intent_mode": execution_intent.get("mode"),
        "entry_score": round(entry_score, 4),
    }
    metadata = {
        "skill_family": "wallet_style",
        "trader_archetype": archetype_label or None,
        "trader_archetype_summary": archetype_summary or None,
        "primary_archetype": archetype_label or None,
        "secondary_archetypes": list((archetype_payload or {}).get("secondary_archetypes") or []),
        "behavioral_patterns": _json_safe((archetype_payload or {}).get("behavioral_patterns") or []),
        "behavioral_pattern_labels": list((archetype_payload or {}).get("behavioral_pattern_labels") or []),
        "archetype_confidence": (archetype_payload or {}).get("archetype_confidence"),
        "archetype_evidence_summary": (archetype_payload or {}).get("archetype_evidence_summary"),
        "archetype_token_preference": list((archetype_payload or {}).get("archetype_token_preference") or []),
        "archetype": _json_safe(archetype_payload or {}),
    }
    style_profile_payload = dict(profile)
    if archetype_payload:
        existing_archetype = (
            dict(style_profile_payload.get("archetype") or {})
            if isinstance(style_profile_payload.get("archetype"), dict)
            else {}
        )
        for field_name in ARCHETYPE_FIELD_NAMES:
            value = archetype_payload.get(field_name)
            if _should_replace_archetype_field(field_name, existing_archetype.get(field_name), value):
                existing_archetype[field_name] = value
        style_profile_payload["archetype"] = _json_safe(existing_archetype or archetype_payload)
        for field_name in ARCHETYPE_FIELD_NAMES:
            value = archetype_payload.get(field_name)
            if _should_replace_archetype_field(field_name, style_profile_payload.get(field_name), value):
                style_profile_payload[field_name] = value
        profile_metadata = (
            dict(style_profile_payload.get("metadata") or {})
            if isinstance(style_profile_payload.get("metadata"), dict)
            else {}
        )
        nested_metadata_archetype = (
            dict(profile_metadata.get("archetype") or {})
            if isinstance(profile_metadata.get("archetype"), dict)
            else {}
        )
        for field_name in ARCHETYPE_FIELD_NAMES:
            value = archetype_payload.get(field_name)
            if _should_replace_archetype_field(field_name, profile_metadata.get(field_name), value):
                profile_metadata[field_name] = value
            if _should_replace_archetype_field(field_name, nested_metadata_archetype.get(field_name), value):
                nested_metadata_archetype[field_name] = value
        if nested_metadata_archetype:
            profile_metadata["archetype"] = _json_safe(nested_metadata_archetype)
        if profile_metadata:
            style_profile_payload["metadata"] = profile_metadata
    return {
        "ok": True,
        "action": "primary",
        "summary": summary,
        "style_profile": _json_safe(style_profile_payload),
        "strategy": _json_safe(strategy),
        "execution_intent": execution_payload,
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
        "metadata": metadata,
    }
