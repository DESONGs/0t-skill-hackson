from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any
from uuid import uuid4

from ot_skill_enterprise.control_plane.candidates import CandidateSurfaceService, build_candidate_surface_service
from ot_skill_enterprise.enterprise_bridge import EnterpriseBridge
from ot_skill_enterprise.reflection.models import ReflectionJobSpec
from ot_skill_enterprise.reflection.service import (
    PiReflectionService,
    build_wallet_style_output_schema,
    parse_wallet_style_review_report,
)
from ot_skill_enterprise.runs.pipeline import RunIngestionPipeline
from ot_skill_enterprise.service_entrypoints import build_ave_provider
from ot_skill_enterprise.service_locator import project_root as resolve_project_root
from ot_skill_enterprise.shared.contracts import InspectMarketRequest, InspectTokenRequest, InspectWalletRequest, ReviewSignalsRequest, TokenReference

from .extractors import DEFAULT_EXTRACTION_PROMPT, WalletStyleExtractor
from .models import ExecutionIntent, StrategyCondition, StrategySpec, StyleDistillationSummary
from .backtesting import run_backtest
from .market_context import build_macro_token_refs, summarize_focus_token_contexts, summarize_macro_context
from .signal_filters import build_risk_filters, build_signal_context, distill_entry_factors, filters_to_anti_patterns
from .trade_pairing import compute_trade_statistics, pair_trades

_STABLE_SYMBOLS = {"USDT", "USDC", "DAI", "FDUSD", "TUSD"}
_QUOTE_SYMBOLS = _STABLE_SYMBOLS | {"WBNB", "BNB", "WETH", "ETH"}
_EVM_CHAINS = {
    "ethereum",
    "eth",
    "bsc",
    "base",
    "arbitrum",
    "optimism",
    "polygon",
    "avalanche",
    "avax",
    "fantom",
    "celo",
    "linea",
    "blast",
    "zksync",
}
_SESSION_WINDOWS = (
    (0, 6, "asia-late"),
    (6, 12, "asia-open"),
    (12, 18, "europe-overlap"),
    (18, 24, "us-session"),
)
_MAX_COMPACT_BYTES = 6144
_MAX_ACTIVITY_PAGES = 5
_CHAIN_DEFAULT_SOURCE = {
    "bsc": {
        "default_source_token": "USDT",
        "default_source_token_address": "0x55d398326f99059ff775485246999027b3197955",
    }
}


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


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


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return round((ordered[middle - 1] + ordered[middle]) / 2.0, 8)


def _is_evm_chain(chain: Any) -> bool:
    text = _safe_text(chain)
    return bool(text and text.lower() in _EVM_CHAINS)


def _is_evm_address(value: Any) -> bool:
    text = _safe_text(value)
    if text is None:
        return False
    return len(text) == 42 and text.startswith("0x") and all(char in "0123456789abcdefABCDEF" for char in text[2:])


def _is_placeholder_identifier(value: Any) -> bool:
    text = (_safe_text(value) or "").lower()
    return text in {"", "unknown", "token", "none", "null"}


def _normalize_token_ref(token_ref: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(token_ref)
    if _is_evm_address(normalized.get("token_address")):
        normalized["token_address"] = str(normalized["token_address"]).lower()
    return normalized


def _token_ref_is_valid(token_ref: dict[str, Any], *, require_address: bool = False) -> bool:
    identifier = _safe_text(token_ref.get("identifier"))
    symbol = _safe_text(token_ref.get("symbol"))
    chain = _safe_text(token_ref.get("chain"))
    token_address = _safe_text(token_ref.get("token_address"))
    if require_address and _is_evm_chain(chain):
        return _is_evm_address(token_address)
    if token_address and _is_evm_address(token_address):
        return True
    if identifier and not _is_placeholder_identifier(identifier):
        return True
    return bool(symbol and not _is_placeholder_identifier(symbol))


def _token_ref_symbol(token_ref: dict[str, Any]) -> str:
    return str(token_ref.get("symbol") or "").strip().upper()


def _recent_trade_sample(item: dict[str, Any]) -> dict[str, Any]:
    token_ref = _normalize_token_ref(dict(item.get("token_ref") or {}))
    return {
        "tx_hash": item.get("tx_hash"),
        "timestamp": item.get("timestamp"),
        "action": item.get("action"),
        "symbol": token_ref.get("symbol"),
        "identifier": token_ref.get("identifier"),
        "token_address": token_ref.get("token_address"),
        "amount_usd": item.get("amount_usd"),
        "quote_symbol": item.get("quote_symbol"),
        "from_symbol": item.get("from_symbol"),
        "to_symbol": item.get("to_symbol"),
        "note": item.get("note"),
    }


def _timestamp_hour(value: Any) -> int | None:
    text = _safe_text(value)
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.hour


def _active_window_labels(items: list[dict[str, Any]]) -> list[str]:
    labels: list[str] = []
    seen: set[str] = set()
    for item in items:
        hour = _timestamp_hour(item.get("timestamp"))
        if hour is None:
            continue
        for start, end, label in _SESSION_WINDOWS:
            if start <= hour < end and label not in seen:
                seen.add(label)
                labels.append(label)
                break
    return labels


def _burst_profile(items: list[dict[str, Any]]) -> str:
    timestamps: list[datetime] = []
    for item in items:
        text = _safe_text(item.get("timestamp"))
        if text is None:
            continue
        try:
            timestamps.append(datetime.fromisoformat(text.replace("Z", "+00:00")))
        except ValueError:
            continue
    if len(timestamps) < 2:
        return "sparse"
    timestamps.sort()
    tight_gaps = 0
    for previous, current in zip(timestamps, timestamps[1:]):
        if (current - previous).total_seconds() <= 60:
            tight_gaps += 1
    if tight_gaps >= max(2, len(timestamps) // 4):
        return "same-minute-burst"
    if tight_gaps:
        return "short-burst"
    return "staggered"


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _job_sort_key(payload: dict[str, Any]) -> str:
    return str(payload.get("created_at") or "")


def _compact_job_payload(payload: dict[str, Any]) -> dict[str, Any]:
    summary = dict(payload.get("summary") or {})
    profile = dict(payload.get("profile") or {})
    qa = dict(payload.get("qa") or {})
    strategy = dict(payload.get("strategy") or {})
    execution_intent = dict(payload.get("execution_intent") or {})
    candidate = dict(payload.get("candidate") or {})
    promotion = dict(payload.get("promotion") or {})
    reflection = dict(payload.get("reflection") or {})
    return {
        "job_id": payload.get("job_id") or summary.get("job_id"),
        "wallet": payload.get("wallet") or summary.get("wallet"),
        "chain": payload.get("chain") or summary.get("chain"),
        "created_at": payload.get("created_at") or summary.get("created_at"),
        "review_backend": payload.get("review_backend") or summary.get("review_backend"),
        "reflection_flow_id": payload.get("reflection_flow_id") or summary.get("reflection_flow_id"),
        "reflection_run_id": payload.get("reflection_run_id") or summary.get("reflection_run_id"),
        "reflection_session_id": payload.get("reflection_session_id") or summary.get("reflection_session_id"),
        "reflection_status": payload.get("reflection_status") or summary.get("reflection_status"),
        "fallback_used": bool(payload.get("fallback_used") if payload.get("fallback_used") is not None else summary.get("fallback_used")),
        "summary": summary,
        "execution_readiness": payload.get("execution_readiness") or summary.get("execution_readiness"),
        "profile": {
            "summary": profile.get("summary"),
            "confidence": profile.get("confidence"),
            "execution_tempo": profile.get("execution_tempo"),
            "risk_appetite": profile.get("risk_appetite"),
            "conviction_profile": profile.get("conviction_profile"),
            "stablecoin_bias": profile.get("stablecoin_bias"),
            "dominant_actions": list(profile.get("dominant_actions") or []),
            "preferred_tokens": list(profile.get("preferred_tokens") or []),
            "active_windows": list(profile.get("active_windows") or []),
        },
        "strategy": {
            "setup_label": strategy.get("setup_label"),
            "summary": strategy.get("summary"),
            "entry_conditions": list(strategy.get("entry_conditions") or []),
        },
        "execution_intent": {
            "adapter": execution_intent.get("adapter"),
            "mode": execution_intent.get("mode"),
            "preferred_workflow": execution_intent.get("preferred_workflow"),
            "preflight_checks": list(execution_intent.get("preflight_checks") or []),
        },
        "backtest": {
            "signal_accuracy": payload.get("backtest", {}).get("signal_accuracy"),
            "pnl_capture_ratio": payload.get("backtest", {}).get("pnl_capture_ratio"),
            "confidence_score": payload.get("backtest", {}).get("confidence_score"),
            "confidence_label": payload.get("backtest", {}).get("confidence_label"),
        },
        "fetch_metadata": dict(payload.get("fetch_metadata") or {}),
        "qa": {
            "status": qa.get("status"),
            "checks": list(qa.get("checks") or []),
        },
        "candidate": {
            "candidate_id": candidate.get("candidate_id"),
            "target_skill_name": candidate.get("target_skill_name"),
        },
        "promotion": {
            "promotion_id": promotion.get("promotion_id"),
            "package_root": promotion.get("package_root"),
        },
        "reflection": {
            "review_backend": reflection.get("review_backend") or payload.get("review_backend") or summary.get("review_backend"),
            "reflection_flow_id": reflection.get("reflection_flow_id") or payload.get("reflection_flow_id") or summary.get("reflection_flow_id"),
            "reflection_run_id": reflection.get("reflection_run_id") or payload.get("reflection_run_id") or summary.get("reflection_run_id"),
            "reflection_session_id": reflection.get("reflection_session_id") or payload.get("reflection_session_id") or summary.get("reflection_session_id"),
            "reflection_status": reflection.get("status") or payload.get("reflection_status") or summary.get("reflection_status"),
            "fallback_used": bool(
                reflection.get("fallback_used")
                if reflection.get("fallback_used") is not None
                else payload.get("fallback_used")
                if payload.get("fallback_used") is not None
                else summary.get("fallback_used")
            ),
        },
    }


def _compact_size_bytes(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"))


def _shrink_compact_payload(payload: dict[str, Any], *, max_bytes: int = _MAX_COMPACT_BYTES) -> dict[str, Any]:
    compact = _json_safe(payload)
    if _compact_size_bytes(compact) <= max_bytes:
        compact["compact_size_bytes"] = _compact_size_bytes(compact)
        return compact
    shrinkers: tuple[tuple[str, int], ...] = (
        ("recent_trade_samples", 6),
        ("recent_activity", 6),
        ("signals", 4),
        ("holdings", 4),
        ("token_snapshots", 3),
    )
    for key, limit in shrinkers:
        if key in compact and isinstance(compact[key], list):
            compact[key] = compact[key][:limit]
            if _compact_size_bytes(compact) <= max_bytes:
                compact["compact_size_bytes"] = _compact_size_bytes(compact)
                return compact
    market_context = dict(compact.get("market_context") or {})
    focus_context = list(market_context.get("focus_token_context") or [])
    if focus_context:
        market_context["focus_token_context"] = focus_context[:3]
        compact["market_context"] = market_context
        if _compact_size_bytes(compact) <= max_bytes:
            compact["compact_size_bytes"] = _compact_size_bytes(compact)
            return compact
    derived = dict(compact.get("derived_stats") or {})
    for noisy_key in ("active_windows", "dominant_actions", "preferred_tokens", "top_quote_tokens"):
        if noisy_key in derived and isinstance(derived[noisy_key], list):
            derived[noisy_key] = derived[noisy_key][:3]
    compact["derived_stats"] = derived
    compact["compact_size_bytes"] = _compact_size_bytes(compact)
    return compact


def _serialize_trade_pairing(completed_trades: list[Any], open_positions: list[Any], statistics: Any) -> dict[str, Any]:
    return {
        "completed_trades": [item.to_dict() for item in completed_trades],
        "open_positions": [item.to_dict() for item in open_positions],
        "statistics": statistics.to_dict(),
    }


def _market_request(token_ref: dict[str, Any]) -> InspectMarketRequest | None:
    if not _token_ref_is_valid(token_ref, require_address=True):
        return None
    try:
        return InspectMarketRequest(token_ref=TokenReference.model_validate(token_ref))
    except Exception:  # noqa: BLE001
        return None


def _signal_factor_hint(item: dict[str, Any]) -> str | None:
    title = str(item.get("title") or "").strip().lower()
    if "volume" in title:
        return "volume_spike"
    if "momentum" in title:
        return "momentum_chase"
    return None


def _pick_focus_tokens(wallet_profile: dict[str, Any], *, limit: int = 4) -> list[dict[str, Any]]:
    ranked: dict[str, dict[str, Any]] = {}
    for item in wallet_profile.get("recent_activity", []):
        if not isinstance(item, dict):
            continue
        token_ref = item.get("token_ref")
        if not isinstance(token_ref, dict):
            continue
        normalized = _normalize_token_ref(token_ref)
        if not _token_ref_is_valid(normalized, require_address=True):
            continue
        symbol = _token_ref_symbol(normalized)
        if symbol in _QUOTE_SYMBOLS:
            continue
        identifier = str(normalized.get("identifier") or normalized.get("token_address") or "").strip()
        if not identifier:
            continue
        entry = ranked.setdefault(identifier, {"token_ref": normalized, "score": 0.0, "count": 0})
        entry["count"] += 1
        entry["score"] += (_safe_float(item.get("amount_usd")) or 0.0) + 50.0

    holdings = [
        item
        for item in wallet_profile.get("holdings", [])
        if isinstance(item, dict) and isinstance(item.get("token_ref"), dict)
    ]
    holdings.sort(key=lambda item: float(item.get("allocation_pct") or 0.0), reverse=True)
    for item in holdings:
        normalized = _normalize_token_ref(dict(item.get("token_ref") or {}))
        if not _token_ref_is_valid(normalized, require_address=True):
            continue
        symbol = _token_ref_symbol(normalized)
        if symbol in _QUOTE_SYMBOLS:
            continue
        identifier = str(normalized.get("identifier") or normalized.get("token_address") or "").strip()
        if not identifier:
            continue
        entry = ranked.setdefault(identifier, {"token_ref": normalized, "score": 0.0, "count": 0})
        entry["score"] += (_safe_float(item.get("value_usd")) or 0.0) + float(item.get("allocation_pct") or 0.0)

    selected = sorted(
        ranked.values(),
        key=lambda item: (float(item.get("score") or 0.0), int(item.get("count") or 0)),
        reverse=True,
    )
    tokens = [dict(item["token_ref"]) for item in selected[:limit]]
    if tokens:
        return tokens

    fallback_tokens: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in holdings:
        normalized = _normalize_token_ref(dict(item.get("token_ref") or {}))
        if not _token_ref_is_valid(normalized, require_address=True):
            continue
        identifier = str(normalized.get("identifier") or normalized.get("token_address") or "").strip()
        if not identifier or identifier in seen:
            continue
        seen.add(identifier)
        fallback_tokens.append(normalized)
        if len(fallback_tokens) >= limit:
            break
    return fallback_tokens


def _compact_token_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    identity = dict(payload.get("identity") or {})
    market_snapshot = dict(payload.get("market_snapshot") or {})
    risk_snapshot = dict(payload.get("risk_snapshot") or {})
    return {
        "identifier": identity.get("identifier"),
        "symbol": identity.get("symbol"),
        "chain": identity.get("chain"),
        "price_usd": market_snapshot.get("price_usd"),
        "liquidity_usd": market_snapshot.get("liquidity_usd"),
        "volume_24h_usd": market_snapshot.get("volume_24h_usd"),
        "risk_level": risk_snapshot.get("risk_level"),
        "flags": list(risk_snapshot.get("flags") or []),
    }


def _filter_signals(signals: dict[str, Any] | None, *, focus_tokens: list[dict[str, Any]], preferred_symbols: set[str]) -> list[dict[str, Any]]:
    items = list((signals or {}).get("signals") or [])
    if not items:
        return []
    focus_addresses = {str(item.get("token_address") or "").lower() for item in focus_tokens if item.get("token_address")}
    filtered: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        token_ref = dict(item.get("token_ref") or {})
        symbol = str(token_ref.get("symbol") or "").strip().upper()
        token_address = str(token_ref.get("token_address") or "").strip().lower()
        if focus_addresses and token_address and token_address in focus_addresses:
            filtered.append(item)
            continue
        if preferred_symbols and symbol and symbol in preferred_symbols:
            filtered.append(item)
    return filtered


def _preprocess_wallet_data(
    wallet: str,
    chain: str,
    wallet_profile: dict[str, Any],
    token_profiles: list[dict[str, Any]],
    signals: dict[str, Any] | None,
    *,
    focus_tokens: list[dict[str, Any]] | None = None,
    enrich_warnings: list[dict[str, Any]] | None = None,
    trade_statistics: dict[str, Any] | None = None,
    market_contexts: list[dict[str, Any]] | None = None,
    macro_context: dict[str, Any] | None = None,
    entry_factors: list[dict[str, Any]] | None = None,
    risk_filters: list[dict[str, Any]] | None = None,
    fetch_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    wallet_summary = dict(wallet_profile.get("wallet_summary") or {})
    holdings = [
        {
            "symbol": item.get("token_ref", {}).get("symbol"),
            "identifier": item.get("token_ref", {}).get("identifier"),
            "token_address": item.get("token_ref", {}).get("token_address"),
            "allocation_pct": item.get("allocation_pct"),
            "value_usd": item.get("value_usd"),
            "quantity": item.get("quantity"),
            "metadata": dict(item.get("metadata") or {}),
        }
        for item in wallet_profile.get("holdings", [])
        if isinstance(item, dict)
    ]
    holdings.sort(key=lambda item: float(item.get("allocation_pct") or 0.0), reverse=True)

    raw_recent_activity = [item for item in wallet_profile.get("recent_activity", []) if isinstance(item, dict)]
    recent_activity = [_recent_trade_sample(item) for item in raw_recent_activity]
    filtered_activity = [item for item in recent_activity if str(item.get("symbol") or "").strip()]

    action_counter = Counter(str(item.get("action") or "unknown") for item in filtered_activity)
    preferred_tokens = Counter(
        str(item.get("symbol") or "").strip()
        for item in filtered_activity
        if str(item.get("symbol") or "").strip() and str(item.get("symbol") or "").strip().upper() not in _QUOTE_SYMBOLS
    )
    quote_tokens = Counter(str(item.get("quote_symbol") or "").strip() for item in filtered_activity if str(item.get("quote_symbol") or "").strip())
    for holding in holdings[:3]:
        symbol = str(holding.get("symbol") or "").strip()
        if symbol and symbol.upper() not in _QUOTE_SYMBOLS:
            preferred_tokens[symbol] += 1

    balance_usd = _safe_float(wallet_summary.get("total_balance_usd")) or _safe_float(wallet_summary.get("balance_usd")) or 0.0
    activity_amounts = [_safe_float(item.get("amount_usd")) or 0.0 for item in filtered_activity if _safe_float(item.get("amount_usd")) is not None]
    top_holding = holdings[0] if holdings else {}
    stablecoin_allocation = sum(
        float(item.get("allocation_pct") or 0.0)
        for item in holdings
        if str(item.get("symbol") or "").upper() in _STABLE_SYMBOLS
    )
    compact_tokens = [_compact_token_snapshot(item) for item in token_profiles]
    risky_token_count = sum(1 for item in compact_tokens if str(item.get("risk_level") or "").lower() in {"high", "critical"})
    activity_windows = _active_window_labels(filtered_activity)
    burst_profile = _burst_profile(filtered_activity)
    focus_token_payload = [dict(item) for item in list(focus_tokens or [])]
    preferred_symbols = {
        str(token).strip().upper()
        for token, _count in preferred_tokens.most_common(6)
        if str(token).strip()
    }
    filtered_signals = _filter_signals(signals, focus_tokens=focus_token_payload, preferred_symbols=preferred_symbols)
    trade_stats = dict(trade_statistics or {})
    signal_context = {
        "top_entry_factors": [],
        "hard_blocks": [],
        "warnings": [],
        "active_signals": len(filtered_signals[:5]),
        "high_severity_count": sum(
            1 for item in filtered_signals[:5] if str(item.get("severity") or "").lower() in {"high", "critical"}
        ),
    }
    if entry_factors is not None or risk_filters is not None:
        signal_context = {
            "top_entry_factors": [
                {
                    "factor_type": str(item.get("factor_type") or "").strip(),
                    "description": item.get("description"),
                    "frequency": item.get("frequency"),
                    "confidence": item.get("confidence"),
                }
                for item in list(entry_factors or [])[:3]
                if isinstance(item, dict)
            ],
            "hard_blocks": [str(item.get("filter_type") or "").strip() for item in list(risk_filters or []) if isinstance(item, dict) and item.get("is_hard_block")],
            "warnings": [str(item.get("filter_type") or "").strip() for item in list(risk_filters or []) if isinstance(item, dict) and not item.get("is_hard_block")],
            "active_signals": len(filtered_signals[:5]),
            "high_severity_count": sum(
                1 for item in filtered_signals[:5] if str(item.get("severity") or "").lower() in {"high", "critical"}
            ),
        }

    payload = {
        "wallet": wallet,
        "chain": chain,
        "wallet_summary": {
            "wallet_address": wallet_summary.get("wallet_address") or wallet,
            "chain": wallet_summary.get("chain") or chain,
            "label": wallet_summary.get("label"),
            "balance_usd": wallet_summary.get("balance_usd"),
            "total_balance_usd": wallet_summary.get("total_balance_usd") or wallet_summary.get("balance_usd"),
            "token_count": wallet_summary.get("token_count"),
            "total_profit_ratio": wallet_summary.get("total_profit_ratio"),
            "total_win_ratio": wallet_summary.get("total_win_ratio"),
            "purchase_count": wallet_summary.get("total_purchase"),
            "sell_count": wallet_summary.get("total_sold"),
        },
        "holdings": holdings[:5],
        "recent_activity": recent_activity[:8],
        "recent_trade_samples": recent_activity[:8],
        "focus_tokens": focus_token_payload,
        "token_snapshots": compact_tokens[:4],
        "signals": filtered_signals[:5],
        "market_context": {
            "macro": dict(macro_context or {}),
            "focus_token_context": list(market_contexts or [])[:4],
        },
        "signal_context": signal_context,
        "derived_stats": {
            "activity_count": len(filtered_activity),
            "buy_count": action_counter.get("buy", 0),
            "sell_count": action_counter.get("sell", 0),
            "dominant_actions": [action for action, _count in action_counter.most_common(3)],
            "preferred_tokens": [token for token, _count in preferred_tokens.most_common(4)],
            "top_quote_tokens": [token for token, _count in quote_tokens.most_common(3)],
            "avg_activity_usd": round(sum(activity_amounts) / len(activity_amounts), 2) if activity_amounts else 0.0,
            "median_activity_usd": round(_median(activity_amounts), 2) if activity_amounts else 0.0,
            "largest_activity_usd": max(activity_amounts) if activity_amounts else 0.0,
            "activity_to_balance_ratio": round(sum(activity_amounts) / balance_usd, 4) if balance_usd > 0 and activity_amounts else 0.0,
            "top_holding_symbol": top_holding.get("symbol"),
            "top_holding_allocation_pct": top_holding.get("allocation_pct") or 0.0,
            "stablecoin_allocation_pct": round(stablecoin_allocation, 2),
            "risky_token_count": risky_token_count,
            "active_windows": activity_windows,
            "burst_profile": burst_profile,
            "focus_token_count": len(focus_token_payload),
            "enrich_warning_count": len(list(enrich_warnings or [])),
            "completed_trade_count": trade_stats.get("completed_trade_count", 0),
            "win_rate": trade_stats.get("win_rate", 0.0),
            "profit_factor": trade_stats.get("profit_factor", 0.0),
            "expectancy_usd": trade_stats.get("expectancy_usd", 0.0),
            "avg_holding_seconds": trade_stats.get("avg_holding_seconds", 0),
            "holding_classification": trade_stats.get("holding_classification", "sparse"),
            "max_drawdown_pct": trade_stats.get("max_drawdown_pct", 0.0),
            "loss_tolerance_label": trade_stats.get("loss_tolerance_label", "unknown"),
            "averaging_pattern": trade_stats.get("averaging_pattern", "none"),
            "avg_position_splits": trade_stats.get("avg_position_splits", 0.0),
        },
        "enrichment": {
            "token_profile_count": len(token_profiles),
            "warnings": list(enrich_warnings or []),
        },
        "fetch_metadata": dict(fetch_metadata or {}),
    }
    return _shrink_compact_payload(payload)


def _reflection_mock_enabled() -> bool:
    return str(os.getenv("OT_PI_REFLECTION_MOCK") or "").strip().lower() in {"1", "true", "yes", "on"}


def _fallback_strategy_spec(preprocessed: dict[str, Any], profile_payload: dict[str, Any]) -> StrategySpec:
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


def _fallback_execution_intent(preprocessed: dict[str, Any], strategy: StrategySpec) -> ExecutionIntent:
    derived = dict(preprocessed.get("derived_stats") or {})
    route_preferences = tuple(derived.get("top_quote_tokens") or ("USDC", "USDT"))
    position_sizing = dict(strategy.position_sizing or {})
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
        metadata={"chain": preprocessed.get("chain"), "source": "fallback"},
    )


class WalletStyleDistillationService:
    def __init__(
        self,
        *,
        project_root: Path | None = None,
        workspace_root: Path | None = None,
        provider: Any | None = None,
        reflection_service: PiReflectionService | None = None,
    ) -> None:
        self.project_root = Path(project_root).expanduser().resolve() if project_root is not None else resolve_project_root()
        self.workspace_root = Path(workspace_root).expanduser().resolve() if workspace_root is not None else (self.project_root / ".ot-workspace").resolve()
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.provider = provider or build_ave_provider()
        self.candidate_service: CandidateSurfaceService = build_candidate_surface_service(
            project_root=self.project_root,
            workspace_root=self.workspace_root,
        )
        self.bridge = EnterpriseBridge.from_project_root(self.project_root)
        self.reflection_service = reflection_service or PiReflectionService(
            project_root=self.project_root,
            workspace_root=self.workspace_root,
        )
        self.registry_root = self.workspace_root / "evolution-registry"
        self.registry_root.mkdir(parents=True, exist_ok=True)

    @property
    def job_root(self) -> Path:
        path = self.workspace_root / "style-distillations"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def list_jobs(self, *, limit: int = 20) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        for summary_path in sorted(self.job_root.glob("*/summary.json")):
            try:
                payload = json.loads(summary_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(payload, dict):
                items.append(_compact_job_payload(payload))
        items.sort(key=_job_sort_key, reverse=True)
        return {
            "status": "ready",
            "count": len(items),
            "items": items[:limit],
            "latest": items[0] if items else None,
        }

    def _build_reflection_spec(
        self,
        *,
        wallet: str,
        chain: str,
        prompt: str,
        preprocessed: dict[str, Any],
        artifacts_dir: Path,
    ) -> ReflectionJobSpec:
        mock_response: dict[str, Any] | None = None
        if _reflection_mock_enabled():
            extractor = WalletStyleExtractor()
            mock_profile, mock_review = extractor.extract(preprocessed, system_prompt=prompt)
            mock_strategy = _fallback_strategy_spec(preprocessed, mock_profile.to_dict())
            mock_execution_intent = _fallback_execution_intent(preprocessed, mock_strategy)
            mock_response = {
                "profile": mock_profile.to_dict(),
                "strategy": mock_strategy.to_dict(),
                "execution_intent": mock_execution_intent.to_dict(),
                "review": mock_review.to_dict(),
            }
        return ReflectionJobSpec(
            subject_kind="wallet_style_reflection",
            subject_id=wallet,
            flow_id="wallet_style_reflection_review",
            system_prompt=prompt,
            compact_input=preprocessed,
            expected_output_schema=build_wallet_style_output_schema(),
            artifact_root=artifacts_dir,
            prompt=f"Review wallet style for {wallet} on {chain} and return strict JSON only.",
            metadata={
                "schema_mode": "wallet_style_review",
                "chain": chain,
                "wallet": wallet,
                "mock_response": mock_response,
            },
        )

    def distill_wallet_style(
        self,
        *,
        wallet: str,
        chain: str | None = None,
        skill_name: str | None = None,
        extractor_prompt: str | None = None,
    ) -> dict[str, Any]:
        resolved_wallet = str(wallet or "").strip()
        if not resolved_wallet:
            raise ValueError("wallet is required")
        requested_chain = str(chain or "").strip() or "unknown"
        prompt = str(extractor_prompt or DEFAULT_EXTRACTION_PROMPT).strip() or DEFAULT_EXTRACTION_PROMPT
        job_id = f"style-job-{uuid4().hex[:10]}"
        job_dir = self.job_root / job_id
        artifacts_dir = job_dir / "artifacts"
        started_at = time.perf_counter()

        wallet_request = InspectWalletRequest(
            wallet=resolved_wallet,
            chain=chain,
            include_holdings=True,
            include_activity=True,
            activity_pages=_MAX_ACTIVITY_PAGES,
            recent_activity_limit=20,
        )
        raw_wallet_profile = _json_safe(self.provider.inspect_wallet(wallet_request))
        resolved_chain = (
            str(raw_wallet_profile.get("wallet_summary", {}).get("chain") or requested_chain or "unknown").strip()
            or "unknown"
        )
        focus_tokens = _pick_focus_tokens(raw_wallet_profile)
        token_profiles: list[dict[str, Any]] = []
        enrich_warnings: list[dict[str, Any]] = []
        market_payloads: list[dict[str, Any]] = []
        macro_payloads: dict[str, dict[str, Any]] = {}
        signals: dict[str, Any] = {"signals": []}
        fetch_metadata = dict(raw_wallet_profile.get("fetch_metadata") or {})
        macro_token_refs = build_macro_token_refs(resolved_chain)
        max_workers = max(4, len(focus_tokens) * 2 + len(macro_token_refs) + 1)
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures: dict[Any, tuple[str, Any]] = {
                pool.submit(self.provider.review_signals, ReviewSignalsRequest(chain=resolved_chain, limit=5)): ("signals", None),
            }
            for token_ref in focus_tokens:
                try:
                    futures[
                        pool.submit(
                            self.provider.inspect_token,
                            InspectTokenRequest(token_ref=TokenReference.model_validate(token_ref)),
                        )
                    ] = ("token", dict(token_ref))
                except Exception as exc:  # noqa: BLE001
                    enrich_warnings.append({"token_ref": dict(token_ref), "error": str(exc)})
                    continue
                market_request = _market_request(token_ref)
                if market_request is not None and hasattr(self.provider, "inspect_market"):
                    futures[pool.submit(self.provider.inspect_market, market_request)] = ("market", dict(token_ref))
            for macro_name, token_ref in macro_token_refs.items():
                market_request = _market_request(token_ref)
                if market_request is not None and hasattr(self.provider, "inspect_market"):
                    futures[pool.submit(self.provider.inspect_market, market_request)] = ("macro", macro_name)

            for future in as_completed(futures):
                category, detail = futures[future]
                try:
                    payload = _json_safe(future.result())
                except Exception as exc:  # noqa: BLE001
                    if category == "signals":
                        signals = {"signals": [], "warnings": [str(exc)]}
                    else:
                        enrich_warnings.append(
                            {
                                "category": category,
                                "token_ref": detail,
                                "error": str(exc),
                            }
                        )
                    continue
                if category == "signals":
                    signals = payload
                elif category == "token":
                    token_profiles.append(payload)
                elif category == "market":
                    market_payloads.append(payload)
                elif category == "macro" and isinstance(detail, str):
                    macro_payloads[detail] = payload

        full_activity_history = list(raw_wallet_profile.get("full_activity_history") or raw_wallet_profile.get("recent_activity") or [])
        completed_trades, open_positions, buy_splits = pair_trades(full_activity_history)
        trade_statistics = compute_trade_statistics(full_activity_history, completed_trades, open_positions, buy_splits)
        focus_market_contexts = summarize_focus_token_contexts(market_payloads)
        macro_context = summarize_macro_context(resolved_chain, macro_payloads)
        risk_filters = build_risk_filters(token_profiles)
        entry_factors = distill_entry_factors(completed_trades, focus_market_contexts)
        signal_context = build_signal_context(entry_factors, risk_filters, _filter_signals(signals, focus_tokens=focus_tokens, preferred_symbols=set()))
        fetch_metadata.update(
            {
                "parallel": True,
                "token_fetch_count": len(token_profiles),
                "activity_pages_fetched": fetch_metadata.get("activity_pages_fetched", _MAX_ACTIVITY_PAGES),
                "latency_ms": int((time.perf_counter() - started_at) * 1000),
            }
        )
        preprocessed = _preprocess_wallet_data(
            resolved_wallet,
            resolved_chain,
            raw_wallet_profile,
            token_profiles,
            signals,
            focus_tokens=focus_tokens,
            enrich_warnings=enrich_warnings,
            trade_statistics=trade_statistics.to_dict(),
            market_contexts=[item.to_compact() for item in focus_market_contexts],
            macro_context=macro_context.to_compact(),
            entry_factors=[item.to_dict() for item in entry_factors],
            risk_filters=[item.to_dict() for item in risk_filters],
            fetch_metadata=fetch_metadata,
        )
        reflection_spec = self._build_reflection_spec(
            wallet=resolved_wallet,
            chain=resolved_chain,
            prompt=prompt,
            preprocessed=preprocessed,
            artifacts_dir=artifacts_dir,
        )
        reflection_result = self.reflection_service.run(reflection_spec)
        fallback_used = False
        review_backend = reflection_result.review_backend
        try:
            reflection_report = parse_wallet_style_review_report(
                reflection_result.normalized_output,
                wallet=resolved_wallet,
                chain=resolved_chain,
            )
            profile = reflection_report.profile
            strategy = reflection_report.strategy
            execution_intent = reflection_report.execution_intent
            review = reflection_report.review
        except Exception as exc:  # noqa: BLE001
            extractor = WalletStyleExtractor()
            profile, review = extractor.extract(preprocessed, system_prompt=prompt)
            strategy = _fallback_strategy_spec(preprocessed, profile.to_dict())
            execution_intent = _fallback_execution_intent(preprocessed, strategy)
            fallback_used = True
            review_backend = "wallet-style-extractor-fallback"
            reflection_result.fallback_used = True
            reflection_result.metadata = {
                **dict(reflection_result.metadata or {}),
                "fallback_error": str(exc),
                "attempted_review_backend": reflection_result.review_backend,
            }
            reflection_result.normalized_output = {
                "profile": profile.to_dict(),
                "strategy": strategy.to_dict(),
                "execution_intent": execution_intent.to_dict(),
                "review": review.to_dict(),
                "fallback_reason": str(exc),
            }

        reflection_flow_id = reflection_spec.flow_id
        reflection_status = reflection_result.status
        risk_anti_patterns = filters_to_anti_patterns(risk_filters)
        if risk_anti_patterns:
            merged_anti_patterns = []
            for item in [*profile.anti_patterns, *tuple(risk_anti_patterns)]:
                if item and item not in merged_anti_patterns:
                    merged_anti_patterns.append(item)
            profile.anti_patterns = tuple(merged_anti_patterns)

        strategy_metadata = {
            **dict(strategy.metadata or {}),
            "entry_factors": [item.to_dict() for item in entry_factors],
            "risk_filters": [item.to_dict() for item in risk_filters],
            "preferred_tokens": list(preprocessed.get("derived_stats", {}).get("preferred_tokens") or []),
            "market_context": preprocessed.get("market_context"),
            "signal_context": preprocessed.get("signal_context"),
            "trade_statistics": trade_statistics.to_dict(),
        }
        strategy.metadata = strategy_metadata

        if not strategy.preferred_setups:
            strategy.preferred_setups = tuple(preprocessed.get("derived_stats", {}).get("preferred_tokens") or ())
        if not strategy.risk_controls and risk_anti_patterns:
            strategy.risk_controls = tuple(risk_anti_patterns)
        if entry_factors and not strategy.entry_conditions:
            strategy.entry_conditions = (
                StrategyCondition(
                    condition=f"entry_factors includes {entry_factors[0].factor_type}",
                    data_source="ave.signal_context.top_entry_factors",
                    weight=entry_factors[0].confidence,
                    rationale=entry_factors[0].description,
                    metadata=entry_factors[0].to_dict(),
                ),
            )

        execution_metadata = {
            **dict(execution_intent.metadata or {}),
            "chain": resolved_chain,
            "entry_factors": [item.to_dict() for item in entry_factors],
            "risk_filters": [item.to_dict() for item in risk_filters],
            "market_context": preprocessed.get("market_context"),
            **dict(_CHAIN_DEFAULT_SOURCE.get(resolved_chain.lower(), {})),
        }
        execution_intent.metadata = execution_metadata

        backtest_result = run_backtest(
            strategy.to_dict(),
            completed_trades,
            focus_market_contexts,
            signal_context=preprocessed.get("signal_context"),
        )
        profile_payload = profile.to_dict()
        profile_payload["metadata"] = {
            **dict(profile_payload.get("metadata") or {}),
            "review_backend": review_backend,
            "reflection_flow_id": reflection_flow_id,
            "reflection_run_id": reflection_result.reflection_run_id,
            "reflection_session_id": reflection_result.reflection_session_id,
            "reflection_status": reflection_status,
            "fallback_used": fallback_used,
            "reflection_confidence": profile_payload.get("confidence"),
            "backtest_confidence_label": backtest_result.confidence_label,
        }
        profile_payload["confidence"] = backtest_result.confidence_score
        strategy_payload = strategy.to_dict()
        strategy_payload["metadata"] = {
            **dict(strategy_payload.get("metadata") or {}),
            "review_backend": review_backend,
            "reflection_flow_id": reflection_flow_id,
            "reflection_run_id": reflection_result.reflection_run_id,
            "reflection_session_id": reflection_result.reflection_session_id,
            "reflection_status": reflection_status,
            "fallback_used": fallback_used,
            "backtest": backtest_result.to_dict(),
        }
        execution_intent_payload = execution_intent.to_dict()
        execution_intent_payload["metadata"] = {
            **dict(execution_intent_payload.get("metadata") or {}),
            "review_backend": review_backend,
            "reflection_flow_id": reflection_flow_id,
            "reflection_run_id": reflection_result.reflection_run_id,
            "reflection_session_id": reflection_result.reflection_session_id,
            "reflection_status": reflection_status,
            "fallback_used": fallback_used,
            "chain": resolved_chain,
            "backtest_confidence_label": backtest_result.confidence_label,
        }
        review_payload = review.to_dict()
        review_payload["metadata"] = {
            **dict(review_payload.get("metadata") or {}),
            "review_backend": review_backend,
            "reflection_flow_id": reflection_flow_id,
            "reflection_run_id": reflection_result.reflection_run_id,
            "reflection_session_id": reflection_result.reflection_session_id,
            "reflection_status": reflection_status,
            "fallback_used": fallback_used,
            "backtest": backtest_result.to_dict(),
        }

        target_skill_name = str(skill_name or f"wallet-style-{resolved_wallet[-6:]}").strip()
        style_generation_spec = {
            "wallet_style_profile": profile_payload,
            "strategy_spec": strategy_payload,
            "execution_intent": execution_intent_payload,
            "style_review": review_payload,
            "backtest": backtest_result.to_dict(),
            "fetch_metadata": fetch_metadata,
            "source_wallet": resolved_wallet,
            "source_chain": resolved_chain,
            "preprocessed_wallet": preprocessed,
            "extractor_prompt": prompt,
            "review_backend": review_backend,
            "reflection_flow_id": reflection_flow_id,
            "reflection_run_id": reflection_result.reflection_run_id,
            "reflection_session_id": reflection_result.reflection_session_id,
            "reflection_status": reflection_status,
            "fallback_used": fallback_used,
        }

        _write_text(artifacts_dir / "extractor_prompt.txt", prompt + "\n")
        _write_json(artifacts_dir / "wallet_profile.raw.json", raw_wallet_profile)
        _write_json(artifacts_dir / "wallet_profile.full_activity_history.json", full_activity_history)
        _write_json(artifacts_dir / "token_profiles.raw.json", token_profiles)
        _write_json(artifacts_dir / "token_enrichment_warnings.json", enrich_warnings)
        _write_json(artifacts_dir / "signals.raw.json", signals)
        _write_json(artifacts_dir / "wallet_profile.preprocessed.json", preprocessed)
        _write_json(artifacts_dir / "trade_pairing.json", _serialize_trade_pairing(completed_trades, open_positions, trade_statistics))
        _write_json(
            artifacts_dir / "market_context.json",
            {
                "focus_token_context": [item.to_dict() for item in focus_market_contexts],
                "macro": macro_context.to_dict(),
            },
        )
        _write_json(
            artifacts_dir / "signal_filters.json",
            {
                "entry_factors": [item.to_dict() for item in entry_factors],
                "risk_filters": [item.to_dict() for item in risk_filters],
                "signal_context": preprocessed.get("signal_context"),
            },
        )
        _write_json(artifacts_dir / "reflection_job.json", reflection_spec.to_dict())
        _write_json(artifacts_dir / "reflection_result.json", reflection_result.to_dict())
        _write_json(artifacts_dir / "reflection_normalized_output.json", reflection_result.normalized_output)
        _write_json(artifacts_dir / "reflection_raw_output.json", reflection_result.raw_output)
        _write_json(artifacts_dir / "style_profile.json", profile_payload)
        _write_json(artifacts_dir / "strategy_spec.json", strategy_payload)
        _write_json(artifacts_dir / "execution_intent.json", execution_intent_payload)
        _write_json(artifacts_dir / "style_review.json", review_payload)
        _write_json(artifacts_dir / "backtest_result.json", backtest_result.to_dict())

        run_payload = {
            "run_id": f"style-distill-run-{job_id}",
            "runtime_id": "style-distillation",
            "runtime_session_id": f"style-session-{job_id}",
            "subject_kind": "wallet_style",
            "subject_id": resolved_wallet,
            "agent_id": "style-distillation-agent",
            "agent": {
                "agent_id": "style-distillation-agent",
                "display_name": "Wallet Style Distillation",
                "execution_mode": "sync-mvp",
                "metadata": {"source": "hackathon-mvp"},
            },
            "flow_id": "wallet_style_distillation",
            "status": "succeeded",
            "ok": True,
            "summary": profile.summary,
            "candidate_type": "script",
            "target_skill_name": target_skill_name,
            "target_skill_kind": "wallet_style",
            "events": [
                {
                    "event_id": f"{job_id}-fetch-wallet",
                    "event_type": "provider.inspect_wallet",
                    "status": "succeeded",
                "summary": f"fetched wallet profile for {resolved_wallet}",
                },
                {
                    "event_id": f"{job_id}-token-enrich",
                    "event_type": "provider.inspect_token",
                    "status": "succeeded" if not enrich_warnings else ("partial" if token_profiles else "degraded"),
                    "summary": (
                        f"enriched {len(token_profiles)} focus tokens"
                        if not enrich_warnings
                        else f"enriched {len(token_profiles)} focus tokens with {len(enrich_warnings)} warnings"
                    ),
                },
                {
                    "event_id": f"{job_id}-extract-style",
                    "event_type": "llm.style_extract",
                    "status": "succeeded",
                    "summary": review.reasoning,
                },
            ],
            "artifacts": [
                {
                    "artifact_id": f"{job_id}-wallet-profile",
                    "kind": "wallet.profile.json",
                    "uri": str((artifacts_dir / "wallet_profile.raw.json").resolve()),
                    "label": "Raw wallet profile",
                },
                {
                    "artifact_id": f"{job_id}-preprocessed",
                    "kind": "wallet.preprocessed.json",
                    "uri": str((artifacts_dir / "wallet_profile.preprocessed.json").resolve()),
                    "label": "Preprocessed wallet profile",
                },
                {
                    "artifact_id": f"{job_id}-style-profile",
                    "kind": "wallet.style.json",
                    "uri": str((artifacts_dir / "style_profile.json").resolve()),
                    "label": "Extracted wallet style profile",
                },
            ],
            "metadata": {
                "source": "style-distillation",
                "runtime_status": "succeeded",
                "contract_pass": True,
                "contract_summary": "wallet data, prompt, and style artifacts persisted",
                "task_match_score": round(backtest_result.confidence_score, 4),
                "task_match_threshold": 0.55,
                "task_match_summary": review.reasoning,
                "suggested_action": "generate wallet style skill package",
                "llm_review_hook": review_payload,
                "candidate_generation_spec": style_generation_spec,
                "candidate_metadata": {
                    "skill_family": "wallet_style",
                    "wallet_address": resolved_wallet,
                    "chain": resolved_chain,
                    "style_summary": profile.summary,
                    "style_confidence": backtest_result.confidence_score,
                    "extractor_prompt": prompt,
                    "job_id": job_id,
                    "focus_token_count": len(focus_tokens),
                    "token_profile_count": len(token_profiles),
                    "enrich_warning_count": len(enrich_warnings),
                    "fetch_metadata": fetch_metadata,
                    "backtest": backtest_result.to_dict(),
                    "review_backend": review_backend,
                    "reflection_flow_id": reflection_flow_id,
                    "reflection_run_id": reflection_result.reflection_run_id,
                    "reflection_session_id": reflection_result.reflection_session_id,
                    "reflection_status": reflection_status,
                    "fallback_used": fallback_used,
                },
                "change_summary": profile.summary,
                "review_backend": review_backend,
                "reflection_flow_id": reflection_flow_id,
                "reflection_run_id": reflection_result.reflection_run_id,
                "reflection_session_id": reflection_result.reflection_session_id,
                "reflection_status": reflection_status,
                "fallback_used": fallback_used,
            },
        }

        pipeline_result = RunIngestionPipeline(self.registry_root).record(run_payload)
        lifecycle = dict(pipeline_result.lifecycle or {})
        candidate_payload = dict(lifecycle.get("candidate") or {})
        if not candidate_payload:
            raise RuntimeError("style distillation did not produce a candidate")

        candidate_payload.update(
            {
                "candidate_type": "script",
                "target_skill_name": target_skill_name,
                "target_skill_kind": "wallet_style",
                "change_summary": profile.summary,
                "generation_spec": {
                    **dict(candidate_payload.get("generation_spec") or {}),
                    **style_generation_spec,
                },
                "metadata": {
                    **dict(candidate_payload.get("metadata") or {}),
                    "skill_family": "wallet_style",
                    "wallet_address": resolved_wallet,
                    "chain": resolved_chain,
                    "style_summary": profile.summary,
                    "style_confidence": backtest_result.confidence_score,
                    "extractor_prompt": prompt,
                    "job_id": job_id,
                    "focus_token_count": len(focus_tokens),
                    "token_profile_count": len(token_profiles),
                    "enrich_warning_count": len(enrich_warnings),
                    "fetch_metadata": fetch_metadata,
                    "backtest": backtest_result.to_dict(),
                    "review_backend": review_backend,
                    "reflection_flow_id": reflection_flow_id,
                    "reflection_run_id": reflection_result.reflection_run_id,
                    "reflection_session_id": reflection_result.reflection_session_id,
                    "reflection_status": reflection_status,
                    "fallback_used": fallback_used,
                    "strategy_spec": strategy_payload,
                    "execution_intent": execution_intent_payload,
                },
            }
        )

        compile_result = self.candidate_service.compile_candidate(candidate_payload, package_kind="script")
        validate_result = self.candidate_service.validate_candidate(candidate_payload["candidate_id"])
        promote_result = self.candidate_service.promote_candidate(candidate_payload["candidate_id"], package_kind="script")
        promoted_root = Path(promote_result["promotion"]["package_root"]).expanduser().resolve()

        adoption_ok = any(
            summary.skill_name == promoted_root.name
            for summary in self.bridge.discover_local_skill_packages()
        )
        smoke_result = self._smoke_test_skill(promoted_root, preprocessed)
        execution_smoke = self._execution_smoke_test(promoted_root, smoke_result, execution_intent_payload)
        _write_json(artifacts_dir / "skill_smoke_output.json", smoke_result)
        _write_json(artifacts_dir / "execution_smoke_output.json", execution_smoke)

        strategy_qa_checks = [
            {
                "check": "candidate_generated",
                "passed": bool(candidate_payload.get("candidate_id")),
                "detail": candidate_payload.get("candidate_id"),
            },
            {
                "check": "skill_auto_adopted",
                "passed": adoption_ok,
                "detail": promoted_root.name,
            },
            {
                "check": "skill_runnable",
                "passed": bool(smoke_result.get("ok")),
                "detail": smoke_result.get("summary") or smoke_result.get("stderr"),
            },
            {
                "check": "strategy_spec_generated",
                "passed": bool(strategy_payload.get("entry_conditions")),
                "detail": strategy_payload.get("summary"),
            },
            {
                "check": "backtest_scored",
                "passed": backtest_result.confidence_score >= 0.1,
                "detail": backtest_result.to_dict(),
            },
        ]
        execution_qa_checks = [
            {
                "check": "execute_action_generated",
                "passed": (promoted_root / "scripts" / "execute.py").is_file(),
                "detail": str((promoted_root / "scripts" / "execute.py").resolve()),
            },
            {
                "check": "dry_run_ready",
                "passed": bool(execution_smoke.get("ok")),
                "detail": execution_smoke.get("summary") or execution_smoke.get("stderr"),
            },
        ]
        execution_readiness = str(execution_smoke.get("execution_readiness") or "blocked_by_risk")
        qa_status = "passed" if all(item["passed"] for item in strategy_qa_checks + execution_qa_checks) else "failed"

        summary_record = StyleDistillationSummary(
            job_id=job_id,
            wallet=resolved_wallet,
            chain=resolved_chain,
            target_skill_name=target_skill_name,
            candidate_id=candidate_payload.get("candidate_id"),
            promotion_id=promote_result["promotion"]["promotion_id"],
            summary=profile.summary,
            confidence=backtest_result.confidence_score,
            qa_status=qa_status,
            execution_readiness=execution_readiness,
            review_backend=review_backend,
            reflection_flow_id=reflection_flow_id,
            reflection_run_id=reflection_result.reflection_run_id,
            reflection_session_id=reflection_result.reflection_session_id,
            reflection_status=reflection_status,
            fallback_used=fallback_used,
        )
        result = {
            "status": qa_status,
            "job_id": job_id,
            "wallet": resolved_wallet,
            "chain": resolved_chain,
            "created_at": summary_record.created_at,
            "extractor_prompt": prompt,
            "review_backend": review_backend,
            "reflection_flow_id": reflection_flow_id,
            "reflection_run_id": reflection_result.reflection_run_id,
            "reflection_session_id": reflection_result.reflection_session_id,
            "reflection_status": reflection_status,
            "fallback_used": fallback_used,
            "profile": profile_payload,
            "strategy": strategy_payload,
            "execution_intent": execution_intent_payload,
            "review": review_payload,
            "backtest": backtest_result.to_dict(),
            "fetch_metadata": fetch_metadata,
            "execution_readiness": execution_readiness,
            "reflection": reflection_result.to_dict(),
            "run": pipeline_result.summary_dict(),
            "candidate": compile_result["candidate"],
            "package": compile_result["package"],
            "validation": validate_result["validation_report"],
            "promotion": promote_result["promotion"],
            "qa": {
                "status": qa_status,
                "checks": strategy_qa_checks + execution_qa_checks,
                "strategy_qa": {
                    "status": "passed" if all(item["passed"] for item in strategy_qa_checks) else "failed",
                    "checks": strategy_qa_checks,
                    "smoke_test": smoke_result,
                },
                "execution_qa": {
                    "status": "passed" if all(item["passed"] for item in execution_qa_checks) else "failed",
                    "checks": execution_qa_checks,
                    "smoke_test": execution_smoke,
                },
            },
            "artifacts": {
                "job_root": str(job_dir.resolve()),
                "wallet_profile": str((artifacts_dir / "wallet_profile.raw.json").resolve()),
                "full_activity_history": str((artifacts_dir / "wallet_profile.full_activity_history.json").resolve()),
                "preprocessed_wallet": str((artifacts_dir / "wallet_profile.preprocessed.json").resolve()),
                "style_profile": str((artifacts_dir / "style_profile.json").resolve()),
                "strategy_spec": str((artifacts_dir / "strategy_spec.json").resolve()),
                "execution_intent": str((artifacts_dir / "execution_intent.json").resolve()),
                "style_review": str((artifacts_dir / "style_review.json").resolve()),
                "trade_pairing": str((artifacts_dir / "trade_pairing.json").resolve()),
                "market_context": str((artifacts_dir / "market_context.json").resolve()),
                "signal_filters": str((artifacts_dir / "signal_filters.json").resolve()),
                "backtest_result": str((artifacts_dir / "backtest_result.json").resolve()),
                "reflection_job": str((artifacts_dir / "reflection_job.json").resolve()),
                "reflection_result": str((artifacts_dir / "reflection_result.json").resolve()),
                "reflection_normalized_output": str((artifacts_dir / "reflection_normalized_output.json").resolve()),
                "reflection_raw_output": str((artifacts_dir / "reflection_raw_output.json").resolve()),
                "token_enrichment_warnings": str((artifacts_dir / "token_enrichment_warnings.json").resolve()),
                "skill_smoke_output": str((artifacts_dir / "skill_smoke_output.json").resolve()),
                "execution_smoke_output": str((artifacts_dir / "execution_smoke_output.json").resolve()),
            },
            "summary": summary_record.to_dict(),
        }
        _write_json(job_dir / "summary.json", result)
        return result

    def _smoke_test_skill(self, promoted_root: Path, preprocessed: dict[str, Any]) -> dict[str, Any]:
        script_path = promoted_root / "scripts" / "primary.py"
        preferred_tokens = list(preprocessed.get("derived_stats", {}).get("preferred_tokens") or [])
        context = {
            "market_bias": "bullish" if (preprocessed.get("derived_stats", {}).get("activity_to_balance_ratio") or 0.0) >= 0.2 else "range",
            "wallet_activity_count": preprocessed.get("derived_stats", {}).get("activity_count"),
            "preferred_tokens": preferred_tokens,
            "top_holding_symbol": preprocessed.get("derived_stats", {}).get("top_holding_symbol"),
            "candidate_tokens": preferred_tokens,
            "available_routes": preprocessed.get("derived_stats", {}).get("top_quote_tokens"),
            "burst_profile": preprocessed.get("derived_stats", {}).get("burst_profile"),
            "desired_notional_usd": preprocessed.get("derived_stats", {}).get("avg_activity_usd") or 300.0,
            "market_context": preprocessed.get("market_context"),
            "signal_context": preprocessed.get("signal_context"),
        }
        completed = subprocess.run(
            [sys.executable, str(script_path)],
            input=json.dumps(context, ensure_ascii=False),
            text=True,
            capture_output=True,
            check=False,
        )
        stdout = completed.stdout.strip()
        stderr = completed.stderr.strip()
        parsed: Any = None
        if stdout:
            try:
                parsed = json.loads(stdout)
            except json.JSONDecodeError:
                parsed = stdout
        return {
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "parsed_output": parsed,
            "summary": parsed.get("summary") if isinstance(parsed, dict) else stdout,
        }

    def _execution_smoke_test(
        self,
        promoted_root: Path,
        primary_smoke_result: dict[str, Any],
        execution_intent: dict[str, Any],
    ) -> dict[str, Any]:
        script_path = promoted_root / "scripts" / "execute.py"
        if not script_path.is_file():
            return {
                "ok": False,
                "returncode": 1,
                "stdout": "",
                "stderr": "execute.py missing",
                "parsed_output": None,
                "summary": "execute.py missing",
                "execution_readiness": "blocked_by_risk",
            }
        primary_output = primary_smoke_result.get("parsed_output")
        trade_plan = dict(primary_output.get("trade_plan") or {}) if isinstance(primary_output, dict) else {}
        payload = {
            "trade_plan": trade_plan,
            "execution_intent": execution_intent,
            "mode": (
                "dry_run"
                if all(str(os.environ.get(key) or "").strip() for key in ("OKX_API_KEY", "OKX_SECRET_KEY", "OKX_PASSPHRASE"))
                else "prepare_only"
            ),
            "approval_granted": False,
        }
        completed = subprocess.run(
            [sys.executable, str(script_path)],
            input=json.dumps(payload, ensure_ascii=False),
            text=True,
            capture_output=True,
            check=False,
        )
        stdout = completed.stdout.strip()
        stderr = completed.stderr.strip()
        parsed: Any = None
        if stdout:
            try:
                parsed = json.loads(stdout)
            except json.JSONDecodeError:
                parsed = stdout
        return {
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "parsed_output": parsed,
            "summary": parsed.get("summary") if isinstance(parsed, dict) else stdout,
            "execution_readiness": parsed.get("execution_readiness") if isinstance(parsed, dict) else "blocked_by_risk",
        }


def build_wallet_style_distillation_service(
    *,
    project_root: Path | None = None,
    workspace_root: Path | None = None,
    provider: Any | None = None,
    reflection_service: PiReflectionService | None = None,
) -> WalletStyleDistillationService:
    return WalletStyleDistillationService(
        project_root=project_root,
        workspace_root=workspace_root,
        provider=provider,
        reflection_service=reflection_service,
    )
