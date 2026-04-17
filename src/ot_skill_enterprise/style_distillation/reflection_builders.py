from __future__ import annotations

from typing import Any

from ot_skill_enterprise.chain_assets import chain_benchmark_defaults

from .models import ExecutionIntent, StrategyCondition, StrategySpec


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


def _is_evm_address(value: Any) -> bool:
    text = _safe_text(value)
    if text is None:
        return False
    return len(text) == 42 and text.startswith("0x") and all(char in "0123456789abcdefABCDEF" for char in text[2:])


def _execution_chain_defaults(chain: Any) -> dict[str, Any]:
    defaults = dict(chain_benchmark_defaults(chain))
    return {
        key: defaults.get(key)
        for key in (
            "default_source_token",
            "default_source_token_address",
            "default_source_unit_price_usd",
        )
        if defaults.get(key) is not None
    }


def build_fallback_strategy_spec(preprocessed: dict[str, Any], profile_payload: dict[str, Any]) -> StrategySpec:
    derived = dict(preprocessed.get("derived_stats") or {})
    signal_context = dict(preprocessed.get("signal_context") or {})
    market_context = dict(preprocessed.get("market_context") or {})
    buy_tokens = list(derived.get("preferred_tokens") or [])
    factor_hints = [item for item in signal_context.get("top_entry_factors") or [] if isinstance(item, dict)]
    entry_conditions = (
        StrategyCondition(
            condition=f"market_bias in ['bullish','range'] and candidate_token in {buy_tokens or ['watchlist']}",
            data_source="ave.compact_input.derived_stats",
            weight=0.82,
            rationale="Prefer the same rotated token set and market regime seen in AVE trade samples.",
            metadata={"preferred_tokens": buy_tokens, "market_context": market_context.get("macro")},
        ),
        StrategyCondition(
            condition=f"burst_profile == '{derived.get('burst_profile') or 'staggered'}'",
            data_source="ave.compact_input.derived_stats",
            weight=0.68,
            rationale="Keep execution cadence aligned with observed wallet burst profile.",
            metadata={"burst_profile": derived.get("burst_profile")},
        ),
        *(
            StrategyCondition(
                condition=f"entry_factor == '{item.get('factor_type')}'",
                data_source="ave.signal_context.top_entry_factors",
                weight=float(item.get("confidence") or 0.5),
                rationale=str(item.get("description") or "Observed profitable entry pattern."),
                metadata=dict(item),
            )
            for item in factor_hints[:2]
        ),
    )
    return StrategySpec(
        setup_label=str(profile_payload.get("style_label") or "wallet-style-setup"),
        summary=str(profile_payload.get("summary") or "Derived AVE wallet-style strategy."),
        entry_conditions=entry_conditions,
        exit_conditions={
            "stop_loss_model": "soft-percent",
            "stop_loss_pct": 12,
            "take_profit_model": "ladder",
            "take_profit_targets": [
                {"pct_gain": 18, "size_pct": 0.5},
                {"pct_gain": 35, "size_pct": 0.5},
            ],
        },
        position_sizing={
            "model": "split_by_observed_leg_size",
            "max_position_pct": 12,
            "split_legs": True,
            "leg_count": max(1, int(derived.get("focus_token_count") or 1)),
        },
        risk_controls=tuple(profile_payload.get("anti_patterns") or ()),
        preferred_setups=tuple(buy_tokens),
        invalidation_rules=("block_if_security_scan_fails",),
        metadata={"source": "fallback", "entry_factors": factor_hints, "market_context": market_context},
    )


def build_fallback_execution_intent(preprocessed: dict[str, Any], strategy: StrategySpec) -> ExecutionIntent:
    derived = dict(preprocessed.get("derived_stats") or {})
    route_preferences = tuple(derived.get("top_quote_tokens") or ("USDC", "USDT"))
    position_sizing = dict(strategy.position_sizing or {})
    metadata = {
        "chain": preprocessed.get("chain"),
        "source": "fallback",
    }
    token_catalog: dict[str, dict[str, Any]] = {}
    for collection_name in ("focus_tokens", "holdings", "recent_activity"):
        for item in list(preprocessed.get(collection_name) or []):
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol") or "").strip().upper()
            token_address = _safe_text(item.get("token_address"))
            if not symbol or symbol in token_catalog:
                continue
            token_catalog[symbol] = {
                "symbol": symbol,
                "token_address": token_address.lower() if _is_evm_address(token_address) else token_address,
            }
    source_defaults = _execution_chain_defaults(preprocessed.get("chain"))
    if not source_defaults:
        for candidate in derived.get("top_quote_tokens") or []:
            symbol = str(candidate or "").strip().upper()
            token_address = _safe_text(dict(token_catalog.get(symbol) or {}).get("token_address"))
            if symbol and token_address:
                source_defaults = {
                    "default_source_token": symbol,
                    "default_source_token_address": token_address,
                }
                break
    metadata.update({key: value for key, value in source_defaults.items() if value})
    chain_defaults = _execution_chain_defaults(preprocessed.get("chain"))
    metadata.setdefault("default_source_token", chain_defaults.get("default_source_token"))
    metadata.setdefault("default_source_token_address", chain_defaults.get("default_source_token_address"))
    metadata.setdefault("default_source_unit_price_usd", chain_defaults.get("default_source_unit_price_usd"))
    return ExecutionIntent(
        adapter="onchainos_cli",
        mode="dry_run_ready",
        preferred_workflow="swap_execute",
        preflight_checks=("security_token_scan",),
        route_preferences=route_preferences,
        split_legs=bool(position_sizing.get("split_legs")),
        leg_count=max(1, int(position_sizing.get("leg_count") or 1)),
        max_position_pct=_safe_float(position_sizing.get("max_position_pct")),
        requires_explicit_approval=True,
        metadata=metadata,
    )
