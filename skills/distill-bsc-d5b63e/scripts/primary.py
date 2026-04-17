from __future__ import annotations

import json
import math
from pathlib import Path
import re
import sys


PROFILE = json.loads('{"wallet": "0xd5b63edd7cdf4c23718cc8a6a83e312dc8ae3fe1", "chain": "bsc", "style_label": "Micro-Position Memecoin Scalper", "summary": "Ultra-active BSC scalper trading microcap tokens in sub-15-minute bursts using WBNB. Deploys tiny clip sizes (~0.05% of NAV) across pyramid-style entries, tolerates extreme drawdowns, and maintains zero stablecoin buffer.", "confidence": 0.78647059, "execution_tempo": "Ultra-high (same-minute bursts, ~14 min average hold)", "risk_appetite": "Aggressive micro-sizing with macro drawdown tolerance", "conviction_profile": "Low per-trade conviction, high volume, pyramiding", "stablecoin_bias": "None (0% stablecoin allocation, WBNB-native)", "dominant_actions": ["sell", "buy"], "preferred_tokens": ["BNBELIEF", "jelly", "币安故事"], "active_windows": ["us-session"], "sizing_note": "Micro clips averaging ~$233 (~0.05% of $513k balance) with ~3.2 splits per position", "execution_rules": ["Enter in same-minute bursts using WBNB quote", "Pyramid into positions across 3+ clips", "Hold time target under 15 minutes", "Recycle all proceeds back into WBNB; no stablecoin parking", "Accept drawdowns >80% before exit"], "anti_patterns": ["No stop losses (diamond-hands through -87% drawdowns)", "Zero stablecoin risk buffer", "Oversized trade frequency relative to position edge"], "prompt_focus": ["BNBELIEF scalp setups", "jelly momentum continuation", "microcap volume breakout alerts"], "metadata": {"avg_holding_seconds": 844, "profit_factor": 1.16162628, "win_rate": 0.56896552, "max_drawdown_pct": -87.80225482, "review_backend": "pi-reflection-agent:kimi-coding/kimi-k2-thinking", "reflection_flow_id": "wallet_style_reflection_review", "reflection_run_id": "run-cf907193ef55", "reflection_session_id": "pi-session-f19485506a", "reflection_status": "succeeded", "fallback_used": false, "reflection_confidence": 0.78, "backtest_confidence_label": "high"}}')
STRATEGY = json.loads('{"setup_label": "WBNB Microcap Momentum Scalping", "summary": "Exploit short-term volatility in BSC memecoins via rapid in-and-out trades using WBNB. Positions are built in 3+ clips and exited within minutes to capture micro-moves.", "entry_conditions": [{"condition": "Token shows burst in same-minute buy interest or volume spike", "data_source": "on-chain activity + DEX flow", "weight": 0.4, "rationale": "Wallet enters in rapid bursts coinciding with momentum", "metadata": {}}, {"condition": "Price action aligns with 14-min-or-less scalp window", "data_source": "derived holding time statistics", "weight": 0.3, "rationale": "Average hold is 844 seconds; entries are timed for immediate liquidity", "metadata": {}}, {"condition": "WBNB available as quote token", "data_source": "wallet holdings and recent activity", "weight": 0.3, "rationale": "100% of recent activity routes through WBNB", "metadata": {}}], "exit_conditions": {"take_profit": "No fixed target; exit when micro-momentum exhausts (usually within 1-15 minutes)", "stop_loss": "None operational; wallet holds through -87% drawdowns", "time_stop": "Automatic exit if position held >15 minutes (statistical average)", "rationale": "Ultra-short hold times suggest time-based exits and momentum exhaustion rather than fixed price targets"}, "position_sizing": {"clip_size_usd": 233, "clip_size_pct": 0.045, "build_splits": 3.24, "max_position_pct": 0.15, "pattern": "pyramid", "rationale": "Positions are built from tiny clips to avoid slippage and manage microcap risk"}, "risk_controls": ["Position fragmentation across 3+ clips", "Tiny per-trade exposure (<0.1% NAV)", "No overnight/stablecoin parking"], "preferred_setups": ["Same-minute volume bursts", "Microcap WBNB pair scalps", "Pyramid accumulation into momentum"], "invalidation_rules": ["Momentum dies before 15-minute window", "WBNB pair liquidity dries up", "Unable to split entry into 3+ clips without slippage"], "metadata": {"entry_factors": [], "risk_filters": [], "preferred_tokens": ["BNBELIEF", "jelly", "币安故事"], "market_context": {"macro": {"btc_24h_pct": null, "eth_24h_pct": null, "regime": "unknown"}, "focus_token_context": []}, "signal_context": {"top_entry_factors": [], "hard_blocks": [], "warnings": [], "active_signals": 0, "high_severity_count": 0}, "trade_statistics": {"total_trades": 177, "completed_trade_count": 58, "open_position_count": 36, "win_rate": 0.56896552, "avg_pnl_pct": 2024.47429894, "profit_factor": 1.16162628, "expectancy_usd": 11.84536037, "avg_holding_seconds": 844, "median_holding_seconds": 130, "holding_classification": "scalping", "max_drawdown_pct": -87.80225482, "avg_loss_pct": -42.67974728, "loss_tolerance_label": "diamond_hands", "averaging_pattern": "pyramid", "avg_position_splits": 3.24137931}, "review_backend": "pi-reflection-agent:kimi-coding/kimi-k2-thinking", "reflection_flow_id": "wallet_style_reflection_review", "reflection_run_id": "run-cf907193ef55", "reflection_session_id": "pi-session-f19485506a", "reflection_status": "succeeded", "fallback_used": false, "backtest": {"total_signals": 17, "executed_trades": 58, "correct_signals": 11, "signal_accuracy": 0.64705882, "simulated_pnl_usd": 893.53081962, "actual_pnl_usd": 687.03090167, "pnl_capture_ratio": 1.3005686, "max_drawdown_pct": -76.81924076, "confidence_score": 0.78647059, "confidence_label": "high", "metadata": {"preferred_token_count": 6, "factor_hint_count": 0, "active_signal_count": 0}}}}')
EXECUTION_INTENT = json.loads('{"adapter": "bsc-dex-router", "mode": "burst-scalp", "preferred_workflow": "WBNB -> microcap token -> WBNB rapid cycle with same-minute multi-leg entry", "preflight_checks": ["WBNB balance > $50", "Slippage < 3% for target clip size", "Gas estimate < 0.01 BNB", "Same-minute burst throttle not exceeded"], "route_preferences": ["WBNB direct pairs", "pancakeswap-v3", "high-liquidity microcap pools"], "split_legs": true, "leg_count": 3, "max_position_pct": 0.15, "requires_explicit_approval": false, "metadata": {"chain": "bsc", "entry_factors": [], "risk_filters": [], "market_context": {"macro": {"btc_24h_pct": null, "eth_24h_pct": null, "regime": "unknown"}, "focus_token_context": []}, "review_backend": "pi-reflection-agent:kimi-coding/kimi-k2-thinking", "reflection_flow_id": "wallet_style_reflection_review", "reflection_run_id": "run-cf907193ef55", "reflection_session_id": "pi-session-f19485506a", "reflection_status": "succeeded", "fallback_used": false, "backtest_confidence_label": "high"}}')
TOKEN_CATALOG = json.loads('{"jelly": {"symbol": "jelly", "token_address": "0xf581ee357f11d7478fafd183b4a41347c35a4444", "chain": "bsc"}, "BNBELIEF": {"symbol": "BNBELIEF", "token_address": "0xa4ca2cde650f452c6a0e2dbf75b013e3e3b77777", "chain": "bsc"}, "币安故事": {"symbol": "币安故事", "token_address": "0x2c598dffec4e4f4a6c65756b14a216fc61134444", "chain": "bsc"}, "Ghost": {"symbol": "Ghost", "token_address": "0x5f1f08f6cf3af3c9e7cbd1fd26d9b66f7f964444", "chain": "bsc"}}')
ENTRY_FACTORS = list((STRATEGY.get("metadata") or {}).get("entry_factors") or [])
QUOTE_TOKENS = {"USDT", "USDC", "DAI", "FDUSD", "TUSD", "WBNB", "BNB", "WETH", "ETH"}
CHAIN_ROUTE_TOKENS = {
    "bsc": {
        "WBNB": "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c",
        "USDT": "0x55d398326f99059ff775485246999027b3197955",
        "USDC": "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d",
    },
}


def _load_context() -> dict:
    if len(sys.argv) > 1:
        candidate = sys.argv[1]
        path = Path(candidate).expanduser()
        if path.exists() and path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
        return json.loads(candidate)
    if not sys.stdin.isatty():
        raw = sys.stdin.read().strip()
        if raw:
            return json.loads(raw)
    return {}


def _non_quote_tokens(values) -> list[str]:
    items: list[str] = []
    for value in values or []:
        token = str(value or "").strip()
        if token and token.upper() not in QUOTE_TOKENS and token not in items:
            items.append(token)
    return items


def _extract_max_leg_usd(context: dict) -> float:
    explicit = context.get("max_leg_usd")
    try:
        if explicit is not None:
            return max(50.0, float(explicit))
    except (TypeError, ValueError):
        pass
    matches: list[float] = []
    for text in [*(PROFILE.get("execution_rules") or []), PROFILE.get("sizing_note") or ""]:
        for raw in re.findall(r"\$?([0-9]+(?:\.[0-9]+)?)", str(text)):
            try:
                value = float(raw)
            except ValueError:
                continue
            if value >= 50:
                matches.append(value)
    return min(matches) if matches else 300.0


def _resolve_token(symbol: str) -> dict:
    token = dict(TOKEN_CATALOG.get(symbol) or {})
    metadata = dict(EXECUTION_INTENT.get("metadata") or {})
    if not token and symbol == str(metadata.get("default_target_token") or "").strip():
        token = {
            "symbol": symbol,
            "token_address": metadata.get("default_target_token_address"),
            "chain": metadata.get("chain") or PROFILE.get("chain"),
        }
    return token


def _pick_target_token(context: dict) -> str:
    explicit = str(context.get("target_token") or "").strip()
    if explicit:
        return explicit
    candidates = _non_quote_tokens(context.get("candidate_tokens") or [])
    if candidates:
        return candidates[0]
    preferred = _non_quote_tokens(PROFILE.get("preferred_tokens") or [])
    if preferred:
        return preferred[0]
    return str((PROFILE.get("preferred_tokens") or ["watchlist"])[0])


def _resolve_source_token(route_symbols: list[str], chain: str) -> dict:
    chain_routes = CHAIN_ROUTE_TOKENS.get(chain, {})
    metadata = dict(EXECUTION_INTENT.get("metadata") or {})
    stable_first = [symbol for symbol in route_symbols if symbol.upper() in {"USDT", "USDC", "DAI", "FDUSD", "TUSD"}]
    ordered = stable_first or route_symbols
    for symbol in ordered:
        if symbol in TOKEN_CATALOG:
            return dict(TOKEN_CATALOG[symbol])
        if symbol in chain_routes:
            return {"symbol": symbol, "token_address": chain_routes[symbol], "chain": chain}
    default_symbol = str(metadata.get("default_source_token") or "").strip()
    default_address = str(metadata.get("default_source_token_address") or "").strip()
    if default_symbol and default_address:
        return {"symbol": default_symbol, "token_address": default_address, "chain": chain}
    return {}


def _infer_route(context: dict, target_token: str) -> list[str]:
    explicit = [str(item or "").strip() for item in context.get("preferred_route") or [] if str(item or "").strip()]
    if explicit:
        return explicit if explicit[-1] == target_token else [*explicit, target_token]
    route: list[str] = []
    rules = " ".join(str(item) for item in PROFILE.get("execution_rules") or [])
    for quote in ("WBNB", "USDC", "USDT"):
        if quote in rules and quote not in route:
            route.append(quote)
    for quote in context.get("available_routes") or []:
        text = str(quote or "").strip()
        if text and text.upper() in QUOTE_TOKENS and text not in route:
            route.append(text)
    if not route:
        route = ["USDC"]
    if route[-1] != target_token:
        route.append(target_token)
    return route


def _recommend(context: dict) -> dict:
    market_bias = str(context.get("market_bias") or "range").lower()
    dominant_actions = [str(item).lower() for item in PROFILE.get("dominant_actions") or []]
    market_context = dict(context.get("market_context") or {})
    macro = dict(market_context.get("macro") or {})
    signal_context = dict(context.get("signal_context") or {})
    active_entry_factors = {str(item.get('factor_type') or '').strip() for item in signal_context.get('top_entry_factors') or [] if isinstance(item, dict)}
    action = "watch"
    if macro.get('regime') == 'risk_off' and (signal_context.get('hard_blocks') or []):
        action = 'watch'
    elif market_bias in {"bullish", "up"} and PROFILE.get("risk_appetite") in {"aggressive", "balanced"}:
        action = "buy"
    elif market_bias in {"bearish", "down"} and "sell" in dominant_actions:
        action = "sell"
    elif dominant_actions and dominant_actions[0] in {"buy", "swap"}:
        action = "buy"
    if ENTRY_FACTORS and not active_entry_factors.intersection({str(item.get('factor_type') or '').strip() for item in ENTRY_FACTORS if isinstance(item, dict)}):
        action = 'watch' if action == 'buy' else action
    confidence = min(0.95, float(PROFILE.get("confidence") or 0.4) * 0.85 + (0.08 if action != "watch" else 0.0))
    return {
        "action": action,
        "confidence": round(confidence, 4),
        "rationale": [
            PROFILE.get("summary") or "wallet-style profile available",
            STRATEGY.get("summary") or "strategy summary unavailable",
            *(PROFILE.get("execution_rules") or []),
        ],
        "guardrails": PROFILE.get("anti_patterns") or [],
    }


def _build_trade_plan(context: dict, recommendation: dict) -> dict:
    target_token = _pick_target_token(context)
    target_meta = _resolve_token(target_token)
    max_leg_usd = _extract_max_leg_usd(context)
    desired_notional = context.get("desired_notional_usd")
    try:
        desired_notional_value = float(desired_notional) if desired_notional is not None else max_leg_usd * 2
    except (TypeError, ValueError):
        desired_notional_value = max_leg_usd * 2
    desired_notional_value = max(max_leg_usd, desired_notional_value)
    leg_count = max(1, math.ceil(desired_notional_value / max_leg_usd))
    per_leg_usd = round(desired_notional_value / leg_count, 2)
    chain = str(PROFILE.get("chain") or context.get("chain") or "bsc")
    execution_windows = [str(item) for item in PROFILE.get("active_windows") or [] if str(item).strip()]
    burst_profile = str(context.get("burst_profile") or ("same-minute-burst" if "burst" in str(PROFILE.get("execution_tempo") or "").lower() else "staggered"))
    route_symbols = _infer_route(context, target_token)
    source_meta = _resolve_source_token(route_symbols, chain)
    return {
        "mode": "style-simulated-trade",
        "chain": chain,
        "wallet_address": PROFILE.get("wallet"),
        "entry_action": recommendation.get("action"),
        "target_token": target_token,
        "target_token_address": target_meta.get("token_address"),
        "route": route_symbols,
        "desired_notional_usd": round(desired_notional_value, 2),
        "max_leg_usd": round(max_leg_usd, 2),
        "leg_count": leg_count,
        "per_leg_usd": per_leg_usd,
        "execution_source_symbol": source_meta.get("symbol"),
        "execution_source_address": source_meta.get("token_address"),
        "execution_windows": execution_windows,
        "burst_profile": burst_profile,
        "tempo": PROFILE.get("execution_tempo"),
        "guardrails": PROFILE.get("anti_patterns") or [],
        "rules": PROFILE.get("execution_rules") or [],
        "execution_intent_mode": EXECUTION_INTENT.get("mode"),
    }


def main() -> int:
    context = _load_context()
    recommendation = _recommend(context)
    trade_plan = _build_trade_plan(context, recommendation)
    payload = {
        "ok": True,
        "action": "primary",
        "summary": "Ultra-active BSC scalper trading microcap tokens in sub-15-minute bursts using WBNB. Deploys tiny clip sizes (~0.05% of NAV) across pyramid-style entries, tolerates extreme drawdowns, and maintains zero stablecoin buffer.",
        "style_profile": PROFILE,
        "strategy": STRATEGY,
        "execution_intent": EXECUTION_INTENT,
        "input_context": context,
        "recommendation": recommendation,
        "trade_plan": trade_plan,
        "artifacts": [],
        "metadata": {"skill_family": "wallet_style"},
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
