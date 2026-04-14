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

from .context import (
    ContextAssembler,
    DerivedMemoryStore,
    JobLedgerStore,
    ReviewHintStore,
    ReviewAgent,
    StageCacheRegistry,
    StageArtifactStore,
    hash_payload,
)
from .extractors import DEFAULT_EXTRACTION_PROMPT, WalletStyleExtractor
from .models import ExecutionIntent, StrategyCondition, StrategySpec, StyleDistillationSummary
from .backtesting import run_backtest
from .market_context import (
    MacroContext,
    TokenMarketContext,
    build_macro_token_refs,
    market_context_ready,
    summarize_focus_token_contexts,
    summarize_macro_context,
)
from .signal_filters import build_risk_filters, build_signal_context, distill_entry_factors, filters_to_anti_patterns
from .trade_pairing import CompletedTrade, OpenPosition, TradeStatistics, compute_trade_statistics, pair_trades

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
_MIN_LIVE_LEG_USD = 5.0
_DISTILL_STAGE_VERSION = "2"
_REFLECTION_STAGE_VERSION = "2"
_BUILD_STAGE_VERSION = "2"
_EXECUTION_STAGE_VERSION = "2"


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _strip_volatile_fields(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    for key in ("metadata", "cache_hit", "cache_key", "cache_source_job_id", "job_id", "created_at"):
        normalized.pop(key, None)
    return normalized


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
        "example_readiness": payload.get("example_readiness") or summary.get("example_readiness"),
        "strategy_quality": payload.get("strategy_quality") or summary.get("strategy_quality"),
        "data_completeness": dict(payload.get("data_completeness") or {}),
        "stage_statuses": dict(payload.get("stage_statuses") or summary.get("stage_statuses") or {}),
        "lineage": dict(payload.get("lineage") or summary.get("lineage") or {}),
        "cache_keys": dict(payload.get("cache_keys") or summary.get("cache_keys") or {}),
        "context_sources": list(payload.get("context_sources") or summary.get("context_sources") or []),
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


def _strategy_quality_label(backtest: dict[str, Any]) -> str:
    label = str(backtest.get("confidence_label") or "insufficient_data")
    baseline_only = bool(dict(backtest.get("metadata") or {}).get("baseline_only"))
    if label == "high" and not baseline_only:
        return "high"
    if label in {"medium", "high"}:
        return "medium"
    if label == "low":
        return "low"
    return "insufficient_data"


def _compute_data_completeness(
    *,
    focus_market_contexts: list[Any],
    entry_factors: list[Any],
    risk_filters: list[Any],
    backtest_result: dict[str, Any],
    execution_smoke: dict[str, Any] | None = None,
) -> dict[str, Any]:
    backtest_meta = dict(backtest_result.get("metadata") or {})
    return {
        "market_context_ready": market_context_ready(focus_market_contexts),
        "entry_factors_ready": bool(entry_factors),
        "risk_filters_ready": bool(risk_filters),
        "backtest_ready": not bool(backtest_meta.get("baseline_only")) and str(backtest_result.get("confidence_label") or "") != "insufficient_data",
        "live_execution_ready_inputs": bool(execution_smoke and execution_smoke.get("execution_readiness") in {"dry_run_ready", "live_ready"}),
        "macro_ready": bool(backtest_meta.get("market_context_count")),
    }


def _example_readiness(
    *,
    data_completeness: dict[str, Any],
    execution_readiness: str,
    strategy_quality: str,
) -> str:
    missing_count = sum(
        1
        for key in ("market_context_ready", "entry_factors_ready", "risk_filters_ready")
        if not bool(data_completeness.get(key))
    )
    if missing_count >= 2:
        return "blocked_by_missing_features"
    if execution_readiness == "live_ready":
        return "live_ready"
    if execution_readiness == "dry_run_ready":
        return "dry_run_ready"
    if strategy_quality in {"high", "medium"}:
        return "strategy_ready"
    return "blocked_by_missing_features"


def _compact_size_bytes(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"))


def _memory_payload_fingerprint(items: list[dict[str, Any]]) -> str:
    normalized = [
        {
            "memory_id": str(item.get("memory_id") or ""),
            "memory_type": str(item.get("memory_type") or ""),
            "summary": str(item.get("summary") or ""),
            "payload": dict(item.get("payload") or {}),
        }
        for item in items
        if isinstance(item, dict)
    ]
    return hash_payload(normalized)


_STAGE_ORDER = (
    "distill_features",
    "reflection_report",
    "skill_build",
    "execution_outcome",
)


def _unique_context_sources(*groups: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group in groups:
        for item in group or ():
            if not isinstance(item, dict):
                continue
            marker = json.dumps(_json_safe(item), ensure_ascii=False, sort_keys=True)
            if marker in seen:
                continue
            seen.add(marker)
            items.append(_json_safe(item))
    return items


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


def _completed_trade_from_dict(payload: dict[str, Any]) -> CompletedTrade:
    return CompletedTrade(
        token_symbol=str(payload.get("token_symbol") or "").strip(),
        token_address=_safe_text(payload.get("token_address")),
        token_identifier=_safe_text(payload.get("token_identifier")),
        buy_timestamp=str(payload.get("buy_timestamp") or ""),
        sell_timestamp=str(payload.get("sell_timestamp") or ""),
        buy_amount_usd=_safe_float(payload.get("buy_amount_usd")) or 0.0,
        sell_amount_usd=_safe_float(payload.get("sell_amount_usd")) or 0.0,
        holding_seconds=int(payload.get("holding_seconds") or 0),
        pnl_usd=_safe_float(payload.get("pnl_usd")) or 0.0,
        pnl_pct=_safe_float(payload.get("pnl_pct")) or 0.0,
        is_profitable=bool(payload.get("is_profitable")),
        buy_tx_hash=_safe_text(payload.get("buy_tx_hash")),
        sell_tx_hash=_safe_text(payload.get("sell_tx_hash")),
        metadata=dict(payload.get("metadata") or {}),
    )


def _open_position_from_dict(payload: dict[str, Any]) -> OpenPosition:
    return OpenPosition(
        token_symbol=str(payload.get("token_symbol") or "").strip(),
        token_address=_safe_text(payload.get("token_address")),
        token_identifier=_safe_text(payload.get("token_identifier")),
        buy_timestamp=str(payload.get("buy_timestamp") or ""),
        buy_amount_usd=_safe_float(payload.get("buy_amount_usd")) or 0.0,
        age_seconds=int(payload.get("age_seconds") or 0),
        classification=str(payload.get("classification") or "unknown"),
        tx_hash=_safe_text(payload.get("tx_hash")),
        metadata=dict(payload.get("metadata") or {}),
    )


def _trade_statistics_from_dict(payload: dict[str, Any]) -> TradeStatistics:
    return TradeStatistics(
        total_trades=int(payload.get("total_trades") or 0),
        completed_trade_count=int(payload.get("completed_trade_count") or 0),
        open_position_count=int(payload.get("open_position_count") or 0),
        matching_coverage=_safe_float(payload.get("matching_coverage")) or 0.0,
        win_rate=_safe_float(payload.get("win_rate")) or 0.0,
        avg_pnl_pct=_safe_float(payload.get("avg_pnl_pct")) or 0.0,
        profit_factor=_safe_float(payload.get("profit_factor")) or 0.0,
        expectancy_usd=_safe_float(payload.get("expectancy_usd")) or 0.0,
        avg_holding_seconds=int(payload.get("avg_holding_seconds") or 0),
        median_holding_seconds=int(payload.get("median_holding_seconds") or 0),
        holding_classification=str(payload.get("holding_classification") or "unknown"),
        max_drawdown_pct=_safe_float(payload.get("max_drawdown_pct")) or 0.0,
        avg_loss_pct=_safe_float(payload.get("avg_loss_pct")) or 0.0,
        loss_tolerance_label=str(payload.get("loss_tolerance_label") or "unknown"),
        averaging_pattern=str(payload.get("averaging_pattern") or "none"),
        avg_position_splits=_safe_float(payload.get("avg_position_splits")) or 0.0,
    )


def _token_market_context_from_dict(payload: dict[str, Any]) -> TokenMarketContext:
    return TokenMarketContext(
        symbol=str(payload.get("symbol") or "").strip(),
        token_address=_safe_text(payload.get("token_address")),
        price_now=_safe_float(payload.get("price_now")),
        price_change_1h_pct=_safe_float(payload.get("price_change_1h_pct")),
        price_change_24h_pct=_safe_float(payload.get("price_change_24h_pct")),
        momentum_label=str(payload.get("momentum_label") or "unknown"),
        volatility_regime=str(payload.get("volatility_regime") or "unknown"),
        volume_to_liquidity_ratio=_safe_float(payload.get("volume_to_liquidity_ratio")),
        liquidity_usd=_safe_float(payload.get("liquidity_usd")),
        volume_24h_usd=_safe_float(payload.get("volume_24h_usd")),
        metadata=dict(payload.get("metadata") or {}),
    )


def _macro_context_from_dict(payload: dict[str, Any]) -> MacroContext:
    return MacroContext(
        btc_24h_change_pct=_safe_float(payload.get("btc_24h_change_pct")),
        eth_24h_change_pct=_safe_float(payload.get("eth_24h_change_pct")),
        market_regime=str(payload.get("market_regime") or "unknown"),
        metadata=dict(payload.get("metadata") or {}),
    )


def _risk_filter_like(payload: dict[str, Any]) -> Any:
    return type("RiskFilterLike", (), payload)()


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
    derived_memory: list[dict[str, Any]] | None = None,
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
    derived_memory_payload = [dict(item) for item in list(derived_memory or []) if isinstance(item, dict)]
    derived_memory_summaries = [str(item.get("summary") or "").strip() for item in derived_memory_payload if str(item.get("summary") or "").strip()]
    derived_memory_style_labels = [
        str((item.get("payload") or {}).get("style_label") or "").strip()
        for item in derived_memory_payload
        if str((item.get("payload") or {}).get("style_label") or "").strip()
    ]
    derived_memory_preferred_tokens = Counter()
    derived_memory_active_windows = Counter()
    for item in derived_memory_payload:
        payload = dict(item.get("payload") or {})
        for token in payload.get("preferred_tokens") or []:
            token_text = str(token or "").strip()
            if token_text:
                derived_memory_preferred_tokens[token_text] += 1
        for window in payload.get("active_windows") or []:
            window_text = str(window or "").strip()
            if window_text:
                derived_memory_active_windows[window_text] += 1
    for token, count in derived_memory_preferred_tokens.items():
        preferred_tokens[token] += int(count)
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
        "derived_memory_summary": derived_memory_summaries[:3],
        "derived_memory_style_labels": derived_memory_style_labels[:3],
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
            "derived_memory_summary": derived_memory_summaries[:3],
            "derived_memory_style_labels": derived_memory_style_labels[:3],
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
            "derived_memory_preferred_tokens": [token for token, _count in derived_memory_preferred_tokens.most_common(4)],
            "derived_memory_active_windows": [window for window, _count in derived_memory_active_windows.most_common(4)],
            "derived_memory_summary": derived_memory_summaries[:4],
            "derived_memory_style_labels": derived_memory_style_labels[:4],
            "derived_memory_count": len(derived_memory_payload),
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
        self.ledger_store = JobLedgerStore(self.workspace_root)
        self.stage_artifact_store = StageArtifactStore()
        self.stage_cache_registry = StageCacheRegistry(self.workspace_root)
        self.derived_memory_store = DerivedMemoryStore(self.workspace_root)
        self.review_hint_store = ReviewHintStore()
        self.context_assembler = ContextAssembler()
        self.review_agent = ReviewAgent(self.context_assembler)

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

    def get_job(self, job_id: str) -> dict[str, Any]:
        resolved_job_id = str(job_id or "").strip()
        if not resolved_job_id:
            raise ValueError("job_id is required")
        job_dir = self._job_dir(resolved_job_id)
        if not job_dir.is_dir():
            raise ValueError(f"job not found: {resolved_job_id}")
        summary_path = job_dir / "summary.json"
        if summary_path.is_file():
            payload = json.loads(summary_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
        ledger = self.ledger_store.load(job_dir)
        if not ledger:
            raise ValueError(f"job not found: {resolved_job_id}")
        distill_payload = self._read_stage(job_dir, "distill_features") if self.stage_artifact_store.exists(job_dir, "distill_features") else {}
        reflection_payload = self._read_stage(job_dir, "reflection_report") if self.stage_artifact_store.exists(job_dir, "reflection_report") else {}
        build_payload = self._read_stage(job_dir, "skill_build") if self.stage_artifact_store.exists(job_dir, "skill_build") else {}
        execution_payload = self._read_stage(job_dir, "execution_outcome") if self.stage_artifact_store.exists(job_dir, "execution_outcome") else {}
        payload = {
            "job_id": resolved_job_id,
            "wallet": ledger.get("wallet"),
            "chain": ledger.get("chain"),
            "status": ledger.get("status"),
            "summary": ledger.get("summary") or {},
            "stage_statuses": ledger.get("stage_statuses") or {},
            "lineage": ledger.get("lineage") or {},
            "cache_keys": ledger.get("cache_keys") or {},
            "context_sources": ledger.get("context_sources") or [],
            "profile": build_payload.get("profile") or reflection_payload.get("profile"),
            "strategy": build_payload.get("strategy") or reflection_payload.get("strategy"),
            "execution_intent": build_payload.get("execution_intent") or reflection_payload.get("execution_intent"),
            "review": build_payload.get("review") or reflection_payload.get("review"),
            "backtest": build_payload.get("backtest"),
            "execution_readiness": execution_payload.get("execution_readiness") or build_payload.get("execution_readiness"),
            "example_readiness": execution_payload.get("example_readiness") or build_payload.get("example_readiness"),
            "strategy_quality": build_payload.get("strategy_quality"),
            "data_completeness": build_payload.get("data_completeness"),
            "distill_features": _compact_job_payload({"summary": {}, **distill_payload}) if distill_payload else None,
            "reflection": reflection_payload.get("reflection") or {},
            "candidate": build_payload.get("candidate") or {},
            "package": build_payload.get("package") or {},
            "validation": build_payload.get("validation") or {},
            "promotion": build_payload.get("promotion") or {},
            "qa": build_payload.get("qa") or {},
            "artifacts": {
                "job_ledger": str(self.ledger_store.ledger_path(job_dir).resolve()),
                "stage_distill_features": str(self.stage_artifact_store.artifact_path(job_dir, "distill_features").resolve()) if distill_payload else None,
                "stage_reflection": str(self.stage_artifact_store.artifact_path(job_dir, "reflection_report").resolve()) if reflection_payload else None,
                "stage_build": str(self.stage_artifact_store.artifact_path(job_dir, "skill_build").resolve()) if build_payload else None,
                "stage_execution": str(self.stage_artifact_store.artifact_path(job_dir, "execution_outcome").resolve()) if execution_payload else None,
            },
        }
        return payload

    def _job_dir(self, job_id: str) -> Path:
        return self.job_root / job_id

    def _create_job(
        self,
        *,
        wallet: str,
        requested_chain: str,
        target_skill_name: str,
        extractor_prompt: str,
    ) -> tuple[str, Path]:
        job_id = f"style-job-{uuid4().hex[:10]}"
        job_dir = self._job_dir(job_id)
        job_dir.mkdir(parents=True, exist_ok=True)
        self.ledger_store.create(
            job_dir,
            job_id=job_id,
            wallet=wallet,
            chain=requested_chain,
            requested_skill_name=target_skill_name,
            extractor_prompt=extractor_prompt,
            stage_order=_STAGE_ORDER,
        )
        return job_id, job_dir

    def _stage_cache_key(self, stage: str, payload: dict[str, Any]) -> str:
        return hash_payload({"stage": stage, "version": _DISTILL_STAGE_VERSION if stage == "distill_features" else _REFLECTION_STAGE_VERSION if stage == "reflection_report" else _BUILD_STAGE_VERSION if stage == "skill_build" else _EXECUTION_STAGE_VERSION, "payload": payload})

    def _stage_cache_stage_key(self, stage: str) -> str:
        return {
            "distill_features": "distill_stage_hash",
            "reflection_report": "reflection_stage_hash",
            "skill_build": "skill_build_stage_hash",
            "execution_outcome": "execution_stage_hash",
        }.get(stage, f"{stage}_stage_hash")

    def _cache_stage_payload(self, job_dir: Path, stage: str, payload: dict[str, Any], *, cache_key: str, summary: str) -> None:
        self.ledger_store.update_cache_keys(job_dir, **{self._stage_cache_stage_key(stage): cache_key})
        self.stage_cache_registry.register(stage=stage, cache_key=cache_key, job_id=str(payload.get("job_id") or job_dir.name), payload=payload, summary=summary)

    def _try_materialize_cached_stage(self, job_dir: Path, stage: str, cache_key: str) -> dict[str, Any] | None:
        materialized = self.stage_cache_registry.materialize(job_dir, stage, cache_key)
        if not materialized:
            return None
        payload, _path = materialized
        self.ledger_store.set_artifact_id(job_dir, stage=stage, artifact_id=self._stage_artifact_id(job_dir.name, stage))
        self.ledger_store.update_cache_keys(job_dir, **{self._stage_cache_stage_key(stage): cache_key})
        self.ledger_store.on_stage_success(job_dir, stage=stage, summary=str(payload.get("summary") or payload.get("review") or stage), output_artifact_ids=[self._stage_artifact_id(job_dir.name, stage)])
        return payload

    def _persist_stage_artifacts(self, stage: str, job_dir: Path, payload: dict[str, Any]) -> None:
        artifacts_dir = job_dir / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        if stage == "distill_features":
            prompt = str(payload.get("extractor_prompt") or DEFAULT_EXTRACTION_PROMPT).strip() or DEFAULT_EXTRACTION_PROMPT
            _write_text(artifacts_dir / "extractor_prompt.txt", prompt + "\n")
            _write_json(artifacts_dir / "wallet_profile.raw.json", payload.get("raw_wallet_profile") or {})
            _write_json(artifacts_dir / "wallet_profile.full_activity_history.json", payload.get("full_activity_history") or [])
            _write_json(artifacts_dir / "token_profiles.raw.json", payload.get("token_profiles") or [])
            _write_json(artifacts_dir / "token_enrichment_warnings.json", payload.get("enrich_warnings") or [])
            _write_json(artifacts_dir / "signals.raw.json", payload.get("signals") or {})
            _write_json(artifacts_dir / "wallet_profile.preprocessed.json", payload.get("preprocessed") or {})
            _write_json(artifacts_dir / "trade_pairing.json", payload.get("trade_pairing") or {})
            _write_json(artifacts_dir / "market_context.json", payload.get("market_context") or {})
            _write_json(
                artifacts_dir / "signal_filters.json",
                {
                    "entry_factors": payload.get("entry_factors") or [],
                    "risk_filters": payload.get("risk_filters") or [],
                    "signal_context": dict((payload.get("preprocessed") or {}).get("signal_context") or {}),
                },
            )
            return
        if stage == "reflection_report":
            _write_json(artifacts_dir / "reflection_job.json", payload.get("reflection_job") or {})
            _write_json(artifacts_dir / "reflection_result.json", payload.get("reflection") or {})
            _write_json(artifacts_dir / "reflection_normalized_output.json", payload.get("reflection_normalized_output") or {})
            _write_json(artifacts_dir / "reflection_raw_output.json", payload.get("reflection_raw_output") or {})
            _write_json(artifacts_dir / "style_profile.json", payload.get("profile") or {})
            _write_json(artifacts_dir / "strategy_spec.json", payload.get("strategy") or {})
            _write_json(artifacts_dir / "execution_intent.json", payload.get("execution_intent") or {})
            _write_json(artifacts_dir / "style_review.json", payload.get("review") or {})
            return
        if stage == "skill_build":
            _write_json(artifacts_dir / "style_profile.json", payload.get("profile") or {})
            _write_json(artifacts_dir / "strategy_spec.json", payload.get("strategy") or {})
            _write_json(artifacts_dir / "execution_intent.json", payload.get("execution_intent") or {})
            _write_json(artifacts_dir / "style_review.json", payload.get("review") or {})
            _write_json(artifacts_dir / "backtest_result.json", payload.get("backtest") or {})
            if payload.get("skill_smoke_output") is not None:
                _write_json(artifacts_dir / "skill_smoke_output.json", payload.get("skill_smoke_output"))
            elif dict(payload.get("qa") or {}).get("strategy_qa", {}).get("smoke_test") is not None:
                _write_json(artifacts_dir / "skill_smoke_output.json", dict(payload.get("qa") or {}).get("strategy_qa", {}).get("smoke_test"))
            if payload.get("execution_smoke_output") is not None:
                _write_json(artifacts_dir / "execution_smoke_output.json", payload.get("execution_smoke_output"))
            elif dict(payload.get("qa") or {}).get("execution_qa", {}).get("smoke_test") is not None:
                _write_json(artifacts_dir / "execution_smoke_output.json", dict(payload.get("qa") or {}).get("execution_qa", {}).get("smoke_test"))
            return

    def _stage_artifact_id(self, job_id: str, stage: str) -> str:
        return f"{job_id}:{stage}"

    def _read_stage(self, job_dir: Path, stage: str) -> dict[str, Any]:
        return self.stage_artifact_store.read(job_dir, stage)

    def _load_or_run_stage(self, job_dir: Path, stage: str, runner: Any) -> dict[str, Any]:
        if self.stage_artifact_store.exists(job_dir, stage):
            return self.stage_artifact_store.read(job_dir, stage)
        return runner()

    def _record_stage_success(
        self,
        job_dir: Path,
        *,
        stage: str,
        payload: dict[str, Any],
        summary: str,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        path = self.stage_artifact_store.artifact_path(job_dir, stage)
        if overwrite:
            path = self.stage_artifact_store.replace(job_dir, stage, payload)
        else:
            path = self.stage_artifact_store.write(job_dir, stage, payload)
        artifact_id = self._stage_artifact_id(payload.get("job_id") or job_dir.name, stage)
        self.ledger_store.set_artifact_id(job_dir, stage=stage, artifact_id=artifact_id)
        self.ledger_store.on_stage_success(job_dir, stage=stage, summary=summary, output_artifact_ids=[artifact_id])
        return {
            "artifact_id": artifact_id,
            "path": str(path.resolve()),
        }

    def _review_hints_for_distill(self, stage_payload: dict[str, Any]) -> dict[str, Any]:
        data_completeness = {
            "market_context_ready": market_context_ready(
                [_token_market_context_from_dict(item) for item in stage_payload.get("market_context", {}).get("focus_token_context") or []]
            ),
            "entry_factors_ready": bool(stage_payload.get("entry_factors")),
            "risk_filters_ready": bool(stage_payload.get("risk_filters")),
        }
        hints: list[str] = []
        retry_hints: list[str] = []
        if not data_completeness["market_context_ready"]:
            hints.append("Reflection should treat market_context as partial and rely more on completed trades.")
            retry_hints.append("Retry AVE market fetch only if real pair resolution becomes available.")
        if not data_completeness["entry_factors_ready"]:
            hints.append("Prefer conservative setup labels because entry factors are sparse.")
        if not data_completeness["risk_filters_ready"]:
            hints.append("Do not overstate risk controls when token risk filters are missing.")
        return self.review_agent.post_stage_call(
            stage="distill_features",
            summary="Distill features extracted from AVE.",
            hints=hints,
            retry_hints=retry_hints,
            context_reduction_hints=["Keep compact_input focused on observed trade statistics and wallet-specific hints."],
        ).to_dict() | {"data_completeness": data_completeness}

    def _review_hints_for_reflection(self, stage_payload: dict[str, Any]) -> dict[str, Any]:
        retry_hints: list[str] = []
        if bool(stage_payload.get("fallback_used")):
            retry_hints.append("Retry Pi reflection if compact_input changes; current result used extractor fallback.")
        return self.review_agent.post_stage_call(
            stage="reflection_report",
            summary=str(stage_payload.get("summary") or "Reflection completed."),
            hints=["Build stage should preserve strategy metadata and execution_intent as canonical stage outputs."],
            retry_hints=retry_hints,
            context_reduction_hints=["Keep injected context fenced and do not leak it into canonical artifacts."],
        ).to_dict()

    def _review_hints_for_build(self, stage_payload: dict[str, Any]) -> dict[str, Any]:
        retry_hints: list[str] = []
        if str(stage_payload.get("example_readiness") or "") == "blocked_by_missing_features":
            retry_hints.append("Retry distill/reflection if stronger market_context or risk filters become available.")
        return self.review_agent.post_stage_call(
            stage="skill_build",
            summary=str(stage_payload.get("summary") or "Skill build completed."),
            hints=["Execution stage should only consume promoted skill + trade_plan + execution_intent."],
            retry_hints=retry_hints,
            context_reduction_hints=["Do not let build-stage example artifacts bleed back into distill/reflection context."],
        ).to_dict()

    def _remember_distilled_memory(
        self,
        *,
        wallet: str,
        chain: str,
        distill_payload: dict[str, Any],
        reflection_payload: dict[str, Any],
        build_payload: dict[str, Any],
    ) -> None:
        profile = dict(reflection_payload.get("profile") or {})
        strategy = dict(reflection_payload.get("strategy") or {})
        trade_statistics = dict(distill_payload.get("trade_statistics") or {})
        summary = str(profile.get("summary") or build_payload.get("summary") or "").strip()
        if not summary:
            return
        self.derived_memory_store.remember(
            wallet=wallet,
            chain=chain,
            memory_type="wallet_style_distillation",
            summary=summary,
            payload={
                "style_label": profile.get("style_label"),
                "strategy_setup_label": strategy.get("setup_label"),
                "preferred_tokens": list((strategy.get("metadata") or {}).get("preferred_tokens") or profile.get("preferred_tokens") or []),
                "trade_statistics": {
                    "completed_trade_count": trade_statistics.get("completed_trade_count"),
                    "win_rate": trade_statistics.get("win_rate"),
                    "holding_classification": trade_statistics.get("holding_classification"),
                },
            },
        )

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

    def _distill_wallet_style_legacy(
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
        execution_intent.requires_explicit_approval = True

        backtest_result = run_backtest(
            strategy.to_dict(),
            completed_trades,
            focus_market_contexts,
            signal_context=preprocessed.get("signal_context"),
        )
        strategy_quality = _strategy_quality_label(backtest_result.to_dict())
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
            "strategy_quality": strategy_quality,
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
            "strategy_quality": strategy_quality,
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
            "strategy_quality": strategy_quality,
            "live_cap_usd": 10.0,
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
        data_completeness = _compute_data_completeness(
            focus_market_contexts=focus_market_contexts,
            entry_factors=entry_factors,
            risk_filters=risk_filters,
            backtest_result=backtest_result.to_dict(),
            execution_smoke=execution_smoke,
        )
        _write_json(artifacts_dir / "skill_smoke_output.json", smoke_result)
        _write_json(artifacts_dir / "execution_smoke_output.json", execution_smoke)
        example_artifacts = self._generate_example_artifacts(
            promoted_root,
            preprocessed,
            execution_intent_payload,
            artifacts_dir=artifacts_dir,
        )

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
        example_readiness = _example_readiness(
            data_completeness=data_completeness,
            execution_readiness=execution_readiness,
            strategy_quality=strategy_quality,
        )
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
            example_readiness=example_readiness,
            strategy_quality=strategy_quality,
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
            "example_readiness": example_readiness,
            "strategy_quality": strategy_quality,
            "data_completeness": data_completeness,
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
                **example_artifacts,
            },
            "summary": summary_record.to_dict(),
        }
        _write_json(job_dir / "summary.json", result)
        return result

    def _execution_live_test(
        self,
        promoted_root: Path,
        primary_result: dict[str, Any],
        execution_intent: dict[str, Any],
    ) -> dict[str, Any]:
        script_path = promoted_root / "scripts" / "execute.py"
        primary_output = primary_result.get("parsed_output")
        trade_plan = dict(primary_output.get("trade_plan") or {}) if isinstance(primary_output, dict) else {}
        payload = {
            "trade_plan": trade_plan,
            "execution_intent": execution_intent,
            "mode": "live",
            "approval_granted": True,
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
            "tx_hashes": parsed.get("tx_hashes") if isinstance(parsed, dict) else [],
        }

    def run_distill_features(self, job_id: str) -> dict[str, Any]:
        job_dir = self._job_dir(job_id)
        if self.stage_artifact_store.exists(job_dir, "distill_features"):
            return self._read_stage(job_dir, "distill_features")
        ledger = self.ledger_store.load(job_dir)
        wallet = str(ledger.get("wallet") or "").strip()
        requested_chain = str(ledger.get("chain") or "unknown").strip() or "unknown"
        prompt = str(ledger.get("extractor_prompt") or DEFAULT_EXTRACTION_PROMPT).strip() or DEFAULT_EXTRACTION_PROMPT
        target_skill_name = str(ledger.get("requested_skill_name") or f"wallet-style-{wallet[-6:]}").strip()
        artifacts_dir = job_dir / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.ledger_store.on_stage_start(
            job_dir,
            stage="distill_features",
            summary=f"Fetch AVE features for {wallet} on {requested_chain}",
        )
        recalled_memory = self.derived_memory_store.recall(wallet, requested_chain)
        distill_cache_key = hash_payload(
            {
                "stage": "distill_features",
                "version": _DISTILL_STAGE_VERSION,
                "wallet": wallet,
                "chain": requested_chain,
                "prompt": prompt,
                "provider": type(self.provider).__name__,
                "activity_pages": _MAX_ACTIVITY_PAGES,
                "recent_activity_limit": 20,
                "derived_memory": _memory_payload_fingerprint(recalled_memory),
            }
        )
        cached_distill = self.stage_cache_registry.lookup("distill_features", distill_cache_key)
        if cached_distill:
            stage_payload = self._try_materialize_cached_stage(job_dir, "distill_features", distill_cache_key)
            if stage_payload is not None:
                _write_text(artifacts_dir / "extractor_prompt.txt", prompt + "\n")
                self.review_hint_store.write(job_dir, "distill_features", self._review_hints_for_distill(stage_payload))
                self._persist_stage_artifacts("distill_features", job_dir, stage_payload)
                return stage_payload
        started_at = time.perf_counter()
        try:
            wallet_request = InspectWalletRequest(
                wallet=wallet,
                chain=requested_chain if requested_chain != "unknown" else None,
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
                            enrich_warnings.append({"category": category, "token_ref": detail, "error": str(exc)})
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
            preprocessed = _preprocess_wallet_data(
                wallet,
                resolved_chain,
                raw_wallet_profile,
                token_profiles,
                signals,
                focus_tokens=focus_tokens,
                enrich_warnings=enrich_warnings,
                derived_memory=recalled_memory,
                trade_statistics=trade_statistics.to_dict(),
                market_contexts=[item.to_compact() for item in focus_market_contexts],
                macro_context=macro_context.to_compact(),
                entry_factors=[item.to_dict() for item in entry_factors],
                risk_filters=[item.to_dict() for item in risk_filters],
                fetch_metadata={},
            )
            fetch_metadata.update(
                {
                    "parallel": True,
                    "token_fetch_count": len(token_profiles),
                    "activity_pages_fetched": fetch_metadata.get("activity_pages_fetched", _MAX_ACTIVITY_PAGES),
                    "latency_ms": int((time.perf_counter() - started_at) * 1000),
                }
            )
            preprocessed["fetch_metadata"] = dict(fetch_metadata)
            wallet_fetch_key = hash_payload(
                {
                    "wallet": wallet,
                    "chain": resolved_chain,
                    "activity_pages": _MAX_ACTIVITY_PAGES,
                    "provider": type(self.provider).__name__,
                }
            )
            market_fetch_key = hash_payload(
                {
                    "chain": resolved_chain,
                    "focus_tokens": focus_tokens,
                    "macro_tokens": macro_token_refs,
                }
            )
            context_sources = [
                self.context_assembler.context_source(
                    kind="job_request",
                    identifier=f"{wallet}:{requested_chain}",
                    metadata={"wallet": wallet, "chain": requested_chain, "target_skill_name": target_skill_name},
                ),
                *[
                    self.context_assembler.context_source(
                        kind="derived_memory",
                        identifier=str(item.get("memory_id") or ""),
                        metadata={"memory_type": item.get("memory_type"), "summary": item.get("summary")},
                    )
                    for item in recalled_memory
                ],
            ]
            stage_payload = {
                "job_id": job_id,
                "wallet": wallet,
                "requested_chain": requested_chain,
                "resolved_chain": resolved_chain,
                "target_skill_name": target_skill_name,
                "extractor_prompt": prompt,
                "raw_wallet_profile": raw_wallet_profile,
                "focus_tokens": focus_tokens,
                "token_profiles": token_profiles,
                "enrich_warnings": enrich_warnings,
                "signals": signals,
                "full_activity_history": full_activity_history,
                "trade_pairing": _serialize_trade_pairing(completed_trades, open_positions, trade_statistics),
                "trade_statistics": trade_statistics.to_dict(),
                "market_context": {
                    "focus_token_context": [item.to_dict() for item in focus_market_contexts],
                    "macro": macro_context.to_dict(),
                },
                "entry_factors": [item.to_dict() for item in entry_factors],
                "risk_filters": [item.to_dict() for item in risk_filters],
                "preprocessed": preprocessed,
                "fetch_metadata": fetch_metadata,
                "context_sources": context_sources,
            }
            _write_text(artifacts_dir / "extractor_prompt.txt", prompt + "\n")
            _write_json(artifacts_dir / "wallet_profile.raw.json", raw_wallet_profile)
            _write_json(artifacts_dir / "wallet_profile.full_activity_history.json", full_activity_history)
            _write_json(artifacts_dir / "token_profiles.raw.json", token_profiles)
            _write_json(artifacts_dir / "token_enrichment_warnings.json", enrich_warnings)
            _write_json(artifacts_dir / "signals.raw.json", signals)
            _write_json(artifacts_dir / "wallet_profile.preprocessed.json", preprocessed)
            _write_json(artifacts_dir / "trade_pairing.json", stage_payload["trade_pairing"])
            _write_json(artifacts_dir / "market_context.json", stage_payload["market_context"])
            _write_json(
                artifacts_dir / "signal_filters.json",
                {
                    "entry_factors": stage_payload["entry_factors"],
                    "risk_filters": stage_payload["risk_filters"],
                    "signal_context": preprocessed.get("signal_context"),
                },
            )
            self.ledger_store.update_cache_keys(
                job_dir,
                wallet_fetch_key=wallet_fetch_key,
                market_fetch_key=market_fetch_key,
                compact_input_hash=hash_payload(preprocessed),
                distill_stage_hash=distill_cache_key,
            )
            self._record_stage_success(
                job_dir,
                stage="distill_features",
                payload=stage_payload,
                summary=f"Extracted AVE distill features for {wallet} on {resolved_chain}",
            )
            self.stage_cache_registry.register(
                stage="distill_features",
                cache_key=distill_cache_key,
                job_id=job_id,
                payload=stage_payload,
                summary=f"Extracted AVE distill features for {wallet} on {resolved_chain}",
                metadata={"wallet": wallet, "chain": resolved_chain},
            )
            self.review_hint_store.write(job_dir, "distill_features", self._review_hints_for_distill(stage_payload))
            return stage_payload
        except Exception as exc:  # noqa: BLE001
            hint_payload = self.review_agent.on_stage_fail(
                stage="distill_features",
                retry_hints=("Check AVE provider reachability and wallet schema compatibility.",),
                summary=f"Distill features failed: {exc}",
            )
            self.review_hint_store.write(job_dir, "distill_features", hint_payload.to_dict())
            self.ledger_store.on_stage_fail(job_dir, stage="distill_features", summary=str(exc), retry_hints=hint_payload.retry_hints)
            raise

    def run_reflection_stage(self, job_id: str) -> dict[str, Any]:
        job_dir = self._job_dir(job_id)
        if self.stage_artifact_store.exists(job_dir, "reflection_report"):
            return self._read_stage(job_dir, "reflection_report")
        distill_payload = self.run_distill_features(job_id)
        artifacts_dir = job_dir / "artifacts"
        self.ledger_store.on_stage_start(
            job_dir,
            stage="reflection_report",
            summary=f"Run Pi reflection for {distill_payload['wallet']}",
            input_artifact_ids=[self._stage_artifact_id(job_id, "distill_features")],
        )
        try:
            wallet = str(distill_payload.get("wallet") or "")
            chain = str(distill_payload.get("resolved_chain") or distill_payload.get("requested_chain") or "unknown")
            prompt = str(distill_payload.get("extractor_prompt") or DEFAULT_EXTRACTION_PROMPT)
            preprocessed = dict(distill_payload.get("preprocessed") or {})
            derived_memories = self.derived_memory_store.recall(wallet, chain)
            review_hints = self.review_hint_store.read_all(job_dir)
            reflection_cache_key = hash_payload(
                {
                    "stage": "reflection_report",
                    "version": _REFLECTION_STAGE_VERSION,
                    "prompt": prompt,
                    "compact_input_hash": hash_payload(preprocessed),
                    "memory_fingerprint": _memory_payload_fingerprint(derived_memories),
                    "review_hint_fingerprint": hash_payload(review_hints),
                }
            )
            cached_reflection = self.stage_cache_registry.lookup("reflection_report", reflection_cache_key)
            if cached_reflection:
                cached_payload = self._try_materialize_cached_stage(job_dir, "reflection_report", reflection_cache_key)
                if cached_payload is not None:
                    self._persist_stage_artifacts("reflection_report", job_dir, cached_payload)
                    self.review_hint_store.write(job_dir, "reflection_report", self._review_hints_for_reflection(cached_payload))
                    return cached_payload
            envelope = self.context_assembler.build_reflection_envelope(
                wallet=wallet,
                chain=chain,
                derived_memories=derived_memories,
                review_hints=review_hints,
                hard_constraints=(
                    "Treat injected context as background only.",
                    "Return strict JSON only.",
                ),
            )
            reflection_spec = self._build_reflection_spec(
                wallet=wallet,
                chain=chain,
                prompt=prompt,
                preprocessed=preprocessed,
                artifacts_dir=artifacts_dir,
            )
            reflection_spec.injected_context = envelope.to_dict()
            reflection_result = self.reflection_service.run(reflection_spec)
            fallback_used = False
            review_backend = reflection_result.review_backend
            try:
                reflection_report = parse_wallet_style_review_report(
                    reflection_result.normalized_output,
                    wallet=wallet,
                    chain=chain,
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
            risk_anti_patterns = filters_to_anti_patterns(
                [_risk_filter_like(item) for item in distill_payload.get("risk_filters") or [] if isinstance(item, dict)]
            )
            if risk_anti_patterns:
                merged_anti_patterns: list[str] = []
                for item in [*profile.anti_patterns, *tuple(risk_anti_patterns)]:
                    if item and item not in merged_anti_patterns:
                        merged_anti_patterns.append(item)
                profile.anti_patterns = tuple(merged_anti_patterns)
            context_sources = _unique_context_sources(
                distill_payload.get("context_sources") or [],
                envelope.sources,
                [
                    self.context_assembler.context_source(
                        kind="stage_artifact",
                        identifier=self._stage_artifact_id(job_id, "distill_features"),
                        path=self.stage_artifact_store.artifact_path(job_dir, "distill_features"),
                    )
                ],
            )
            stage_payload = {
                "job_id": job_id,
                "wallet": wallet,
                "chain": chain,
                "summary": review.reasoning or profile.summary,
                "profile": profile.to_dict(),
                "strategy": strategy.to_dict(),
                "execution_intent": execution_intent.to_dict(),
                "review": review.to_dict(),
                "review_backend": review_backend,
                "reflection_flow_id": reflection_flow_id,
                "reflection_run_id": reflection_result.reflection_run_id,
                "reflection_session_id": reflection_result.reflection_session_id,
                "reflection_status": reflection_status,
                "fallback_used": fallback_used,
                "reflection": reflection_result.to_dict(),
                "reflection_job": reflection_spec.to_dict(),
                "reflection_result": reflection_result.to_dict(),
                "reflection_normalized_output": _json_safe(reflection_result.normalized_output),
                "reflection_raw_output": _json_safe(reflection_result.raw_output),
                "injected_context": envelope.to_dict(),
                "context_sources": context_sources,
            }
            _write_json(artifacts_dir / "reflection_job.json", reflection_spec.to_dict())
            _write_json(artifacts_dir / "reflection_result.json", reflection_result.to_dict())
            _write_json(artifacts_dir / "reflection_normalized_output.json", reflection_result.normalized_output)
            _write_json(artifacts_dir / "reflection_raw_output.json", reflection_result.raw_output)
            _write_json(artifacts_dir / "style_profile.json", stage_payload["profile"])
            _write_json(artifacts_dir / "strategy_spec.json", stage_payload["strategy"])
            _write_json(artifacts_dir / "execution_intent.json", stage_payload["execution_intent"])
            _write_json(artifacts_dir / "style_review.json", stage_payload["review"])
            self.ledger_store.update_lineage(job_dir, reflection_run_id=reflection_result.reflection_run_id)
            self.ledger_store.update_cache_keys(job_dir, reflection_stage_hash=reflection_cache_key)
            self._record_stage_success(
                job_dir,
                stage="reflection_report",
                payload=stage_payload,
                summary=stage_payload["summary"],
            )
            self.stage_cache_registry.register(
                stage="reflection_report",
                cache_key=reflection_cache_key,
                job_id=job_id,
                payload=stage_payload,
                summary=stage_payload["summary"],
                metadata={"wallet": wallet, "chain": chain},
            )
            self.review_hint_store.write(job_dir, "reflection_report", self._review_hints_for_reflection(stage_payload))
            return stage_payload
        except Exception as exc:  # noqa: BLE001
            hint_payload = self.review_agent.on_stage_fail(
                stage="reflection_report",
                retry_hints=("Retry reflection with the saved compact_input snapshot.",),
                summary=f"Reflection failed: {exc}",
            )
            self.review_hint_store.write(job_dir, "reflection_report", hint_payload.to_dict())
            self.ledger_store.on_stage_fail(job_dir, stage="reflection_report", summary=str(exc), retry_hints=hint_payload.retry_hints)
            raise

    def run_build_stage(self, job_id: str) -> dict[str, Any]:
        job_dir = self._job_dir(job_id)
        if self.stage_artifact_store.exists(job_dir, "skill_build"):
            return self._read_stage(job_dir, "skill_build")
        distill_payload = self.run_distill_features(job_id)
        reflection_payload = self.run_reflection_stage(job_id)
        artifacts_dir = job_dir / "artifacts"
        self.ledger_store.on_stage_start(
            job_dir,
            stage="skill_build",
            summary=f"Build wallet-style skill for {distill_payload['wallet']}",
            input_artifact_ids=[
                self._stage_artifact_id(job_id, "distill_features"),
                self._stage_artifact_id(job_id, "reflection_report"),
            ],
        )
        try:
            wallet = str(distill_payload.get("wallet") or "")
            chain = str(distill_payload.get("resolved_chain") or distill_payload.get("requested_chain") or "unknown")
            prompt = str(distill_payload.get("extractor_prompt") or DEFAULT_EXTRACTION_PROMPT)
            preprocessed = dict(distill_payload.get("preprocessed") or {})
            profile_payload = dict(reflection_payload.get("profile") or {})
            strategy_payload = dict(reflection_payload.get("strategy") or {})
            execution_intent_payload = dict(reflection_payload.get("execution_intent") or {})
            review_payload = dict(reflection_payload.get("review") or {})
            review_backend = str(reflection_payload.get("review_backend") or "")
            reflection_flow_id = reflection_payload.get("reflection_flow_id")
            reflection_run_id = reflection_payload.get("reflection_run_id")
            reflection_session_id = reflection_payload.get("reflection_session_id")
            reflection_status = reflection_payload.get("reflection_status")
            fallback_used = bool(reflection_payload.get("fallback_used"))
            build_cache_key = hash_payload(
                {
                    "stage": "skill_build",
                    "version": _BUILD_STAGE_VERSION,
                    "wallet": wallet,
                    "chain": chain,
                    "target_skill_name": str(distill_payload.get("target_skill_name") or f"wallet-style-{wallet[-6:]}").strip(),
                    "reflection_signature": hash_payload(
                        {
                            "profile": _strip_volatile_fields(profile_payload),
                            "strategy": _strip_volatile_fields(strategy_payload),
                            "execution_intent": _strip_volatile_fields(execution_intent_payload),
                            "review": _strip_volatile_fields(review_payload),
                        }
                    ),
                    "strategy": _strip_volatile_fields(strategy_payload),
                    "execution_intent": _strip_volatile_fields(execution_intent_payload),
                }
            )
            cached_build = self.stage_cache_registry.lookup("skill_build", build_cache_key)
            if cached_build:
                cached_payload = self._try_materialize_cached_stage(job_dir, "skill_build", build_cache_key)
                if cached_payload is not None:
                    self._persist_stage_artifacts("skill_build", job_dir, cached_payload)
                    self.review_hint_store.write(job_dir, "skill_build", self._review_hints_for_build(cached_payload))
                    return cached_payload

            trade_pairing = dict(distill_payload.get("trade_pairing") or {})
            completed_trades = [
                _completed_trade_from_dict(item)
                for item in trade_pairing.get("completed_trades") or []
                if isinstance(item, dict)
            ]
            trade_statistics = _trade_statistics_from_dict(dict(distill_payload.get("trade_statistics") or {}))
            focus_market_contexts = [
                _token_market_context_from_dict(item)
                for item in dict(distill_payload.get("market_context") or {}).get("focus_token_context") or []
                if isinstance(item, dict)
            ]
            risk_filters = [item for item in distill_payload.get("risk_filters") or [] if isinstance(item, dict)]
            entry_factors = [item for item in distill_payload.get("entry_factors") or [] if isinstance(item, dict)]
            build_context_sources = _unique_context_sources(
                distill_payload.get("context_sources") or [],
                reflection_payload.get("context_sources") or [],
                [
                    self.context_assembler.context_source(
                        kind="stage_artifact",
                        identifier=self._stage_artifact_id(job_id, "distill_features"),
                        path=self.stage_artifact_store.artifact_path(job_dir, "distill_features"),
                    ),
                    self.context_assembler.context_source(
                        kind="stage_artifact",
                        identifier=self._stage_artifact_id(job_id, "reflection_report"),
                        path=self.stage_artifact_store.artifact_path(job_dir, "reflection_report"),
                    ),
                ],
            )

            strategy_metadata = {
                **dict(strategy_payload.get("metadata") or {}),
                "entry_factors": entry_factors,
                "risk_filters": risk_filters,
                "preferred_tokens": list(preprocessed.get("derived_stats", {}).get("preferred_tokens") or []),
                "market_context": preprocessed.get("market_context"),
                "signal_context": preprocessed.get("signal_context"),
                "trade_statistics": trade_statistics.to_dict(),
                "context_sources": build_context_sources,
            }
            strategy_payload["metadata"] = strategy_metadata
            execution_intent_payload["metadata"] = {
                **dict(execution_intent_payload.get("metadata") or {}),
                "chain": chain,
                "entry_factors": entry_factors,
                "risk_filters": risk_filters,
                "market_context": preprocessed.get("market_context"),
                **dict(_CHAIN_DEFAULT_SOURCE.get(chain.lower(), {})),
            }
            execution_intent_payload["requires_explicit_approval"] = True

            backtest_result = run_backtest(
                strategy_payload,
                completed_trades,
                focus_market_contexts,
                signal_context=preprocessed.get("signal_context"),
            )
            strategy_quality = _strategy_quality_label(backtest_result.to_dict())
            profile_payload["metadata"] = {
                **dict(profile_payload.get("metadata") or {}),
                "review_backend": review_backend,
                "reflection_flow_id": reflection_flow_id,
                "reflection_run_id": reflection_run_id,
                "reflection_session_id": reflection_session_id,
                "reflection_status": reflection_status,
                "fallback_used": fallback_used,
                "reflection_confidence": profile_payload.get("confidence"),
                "backtest_confidence_label": backtest_result.confidence_label,
                "strategy_quality": strategy_quality,
            }
            profile_payload["confidence"] = backtest_result.confidence_score
            strategy_payload["metadata"] = {
                **dict(strategy_payload.get("metadata") or {}),
                "review_backend": review_backend,
                "reflection_flow_id": reflection_flow_id,
                "reflection_run_id": reflection_run_id,
                "reflection_session_id": reflection_session_id,
                "reflection_status": reflection_status,
                "fallback_used": fallback_used,
                "backtest": backtest_result.to_dict(),
                "strategy_quality": strategy_quality,
            }
            execution_intent_payload["metadata"] = {
                **dict(execution_intent_payload.get("metadata") or {}),
                "review_backend": review_backend,
                "reflection_flow_id": reflection_flow_id,
                "reflection_run_id": reflection_run_id,
                "reflection_session_id": reflection_session_id,
                "reflection_status": reflection_status,
                "fallback_used": fallback_used,
                "chain": chain,
                "context_sources": build_context_sources,
                "backtest_confidence_label": backtest_result.confidence_label,
                "strategy_quality": strategy_quality,
                "live_cap_usd": 10.0,
            }
            review_payload["metadata"] = {
                **dict(review_payload.get("metadata") or {}),
                "review_backend": review_backend,
                "reflection_flow_id": reflection_flow_id,
                "reflection_run_id": reflection_run_id,
                "reflection_session_id": reflection_session_id,
                "reflection_status": reflection_status,
                "fallback_used": fallback_used,
                "backtest": backtest_result.to_dict(),
            }
            target_skill_name = str(distill_payload.get("target_skill_name") or f"wallet-style-{wallet[-6:]}").strip()
            style_generation_spec = {
                "wallet_style_profile": profile_payload,
                "strategy_spec": strategy_payload,
                "execution_intent": execution_intent_payload,
                "style_review": review_payload,
                "backtest": backtest_result.to_dict(),
                "fetch_metadata": distill_payload.get("fetch_metadata") or {},
                "source_wallet": wallet,
                "source_chain": chain,
                "preprocessed_wallet": preprocessed,
                "extractor_prompt": prompt,
                "review_backend": review_backend,
                "reflection_flow_id": reflection_flow_id,
                "reflection_run_id": reflection_run_id,
                "reflection_session_id": reflection_session_id,
                "reflection_status": reflection_status,
                "fallback_used": fallback_used,
            }
            run_payload = {
                "run_id": f"style-distill-run-{job_id}",
                "runtime_id": "style-distillation",
                "runtime_session_id": f"style-session-{job_id}",
                "subject_kind": "wallet_style",
                "subject_id": wallet,
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
                "summary": profile_payload.get("summary"),
                "candidate_type": "script",
                "target_skill_name": target_skill_name,
                "target_skill_kind": "wallet_style",
                "events": [
                    {
                        "event_id": f"{job_id}-fetch-wallet",
                        "event_type": "provider.inspect_wallet",
                        "status": "succeeded",
                        "summary": f"fetched wallet profile for {wallet}",
                    },
                    {
                        "event_id": f"{job_id}-extract-style",
                        "event_type": "llm.style_extract",
                        "status": "succeeded",
                        "summary": review_payload.get("reasoning"),
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
                    "task_match_summary": review_payload.get("reasoning"),
                    "suggested_action": "generate wallet style skill package",
                    "llm_review_hook": review_payload,
                    "candidate_generation_spec": style_generation_spec,
                    "candidate_metadata": {
                        "skill_family": "wallet_style",
                        "wallet_address": wallet,
                        "chain": chain,
                        "style_summary": profile_payload.get("summary"),
                        "style_confidence": backtest_result.confidence_score,
                        "extractor_prompt": prompt,
                        "job_id": job_id,
                        "focus_token_count": len(distill_payload.get("focus_tokens") or []),
                        "token_profile_count": len(distill_payload.get("token_profiles") or []),
                        "enrich_warning_count": len(distill_payload.get("enrich_warnings") or []),
                        "fetch_metadata": distill_payload.get("fetch_metadata") or {},
                        "backtest": backtest_result.to_dict(),
                        "review_backend": review_backend,
                        "reflection_flow_id": reflection_flow_id,
                        "reflection_run_id": reflection_run_id,
                        "reflection_session_id": reflection_session_id,
                        "reflection_status": reflection_status,
                        "fallback_used": fallback_used,
                    },
                    "change_summary": profile_payload.get("summary"),
                    "review_backend": review_backend,
                    "reflection_flow_id": reflection_flow_id,
                    "reflection_run_id": reflection_run_id,
                    "reflection_session_id": reflection_session_id,
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
                    "change_summary": profile_payload.get("summary"),
                    "generation_spec": {**dict(candidate_payload.get("generation_spec") or {}), **style_generation_spec},
                    "metadata": {
                        **dict(candidate_payload.get("metadata") or {}),
                        "skill_family": "wallet_style",
                        "wallet_address": wallet,
                        "chain": chain,
                        "style_summary": profile_payload.get("summary"),
                        "style_confidence": backtest_result.confidence_score,
                        "extractor_prompt": prompt,
                        "job_id": job_id,
                        "focus_token_count": len(distill_payload.get("focus_tokens") or []),
                        "token_profile_count": len(distill_payload.get("token_profiles") or []),
                        "enrich_warning_count": len(distill_payload.get("enrich_warnings") or []),
                        "fetch_metadata": distill_payload.get("fetch_metadata") or {},
                        "backtest": backtest_result.to_dict(),
                        "review_backend": review_backend,
                        "reflection_flow_id": reflection_flow_id,
                        "reflection_run_id": reflection_run_id,
                        "reflection_session_id": reflection_session_id,
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
            adoption_ok = any(summary.skill_name == promoted_root.name for summary in self.bridge.discover_local_skill_packages())
            smoke_result = self._smoke_test_skill(promoted_root, preprocessed)
            execution_smoke = self._execution_smoke_test(promoted_root, smoke_result, execution_intent_payload)
            data_completeness = _compute_data_completeness(
                focus_market_contexts=focus_market_contexts,
                entry_factors=entry_factors,
                risk_filters=risk_filters,
                backtest_result=backtest_result.to_dict(),
                execution_smoke=execution_smoke,
            )
            _write_json(artifacts_dir / "style_profile.json", profile_payload)
            _write_json(artifacts_dir / "strategy_spec.json", strategy_payload)
            _write_json(artifacts_dir / "execution_intent.json", execution_intent_payload)
            _write_json(artifacts_dir / "style_review.json", review_payload)
            _write_json(artifacts_dir / "backtest_result.json", backtest_result.to_dict())
            _write_json(artifacts_dir / "skill_smoke_output.json", smoke_result)
            _write_json(artifacts_dir / "execution_smoke_output.json", execution_smoke)
            example_artifacts = self._generate_example_artifacts(
                promoted_root,
                preprocessed,
                execution_intent_payload,
                artifacts_dir=artifacts_dir,
            )
            strategy_qa_checks = [
                {"check": "candidate_generated", "passed": bool(candidate_payload.get("candidate_id")), "detail": candidate_payload.get("candidate_id")},
                {"check": "skill_auto_adopted", "passed": adoption_ok, "detail": promoted_root.name},
                {"check": "skill_runnable", "passed": bool(smoke_result.get("ok")), "detail": smoke_result.get("summary") or smoke_result.get("stderr")},
                {"check": "strategy_spec_generated", "passed": bool(strategy_payload.get("entry_conditions")), "detail": strategy_payload.get("summary")},
                {"check": "backtest_scored", "passed": backtest_result.confidence_score >= 0.1, "detail": backtest_result.to_dict()},
            ]
            execution_qa_checks = [
                {"check": "execute_action_generated", "passed": (promoted_root / "scripts" / "execute.py").is_file(), "detail": str((promoted_root / "scripts" / "execute.py").resolve())},
                {"check": "dry_run_ready", "passed": bool(execution_smoke.get("ok")), "detail": execution_smoke.get("summary") or execution_smoke.get("stderr")},
            ]
            execution_readiness = str(execution_smoke.get("execution_readiness") or "blocked_by_risk")
            example_readiness = _example_readiness(
                data_completeness=data_completeness,
                execution_readiness=execution_readiness,
                strategy_quality=strategy_quality,
            )
            qa_status = "passed" if all(item["passed"] for item in strategy_qa_checks + execution_qa_checks) else "failed"
            context_sources = build_context_sources
            stage_payload = {
                "job_id": job_id,
                "wallet": wallet,
                "chain": chain,
                "summary": profile_payload.get("summary"),
                "profile": profile_payload,
                "strategy": strategy_payload,
                "execution_intent": execution_intent_payload,
                "review": review_payload,
                "backtest": backtest_result.to_dict(),
                "fetch_metadata": distill_payload.get("fetch_metadata") or {},
                "execution_readiness": execution_readiness,
                "example_readiness": example_readiness,
                "strategy_quality": strategy_quality,
                "data_completeness": data_completeness,
                "run": pipeline_result.summary_dict(),
                "candidate": compile_result["candidate"],
                "package": compile_result["package"],
                "validation": validate_result["validation_report"],
                "promotion": promote_result["promotion"],
                "skill_smoke_output": smoke_result,
                "execution_smoke_output": execution_smoke,
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
                    **example_artifacts,
                },
                "context_sources": context_sources,
            }
            self.ledger_store.update_lineage(
                job_dir,
                distill_run_id=pipeline_result.run.run_id,
                build_candidate_id=candidate_payload.get("candidate_id"),
                promotion_id=promote_result["promotion"]["promotion_id"],
            )
            self.ledger_store.update_cache_keys(job_dir, strategy_hash=hash_payload(strategy_payload))
            self.ledger_store.update_cache_keys(job_dir, skill_build_stage_hash=build_cache_key)
            self._record_stage_success(
                job_dir,
                stage="skill_build",
                payload=stage_payload,
                summary=profile_payload.get("summary") or "Skill build completed.",
            )
            self.stage_cache_registry.register(
                stage="skill_build",
                cache_key=build_cache_key,
                job_id=job_id,
                payload=stage_payload,
                summary=profile_payload.get("summary") or "Skill build completed.",
                metadata={"wallet": wallet, "chain": chain},
            )
            self.review_hint_store.write(job_dir, "skill_build", self._review_hints_for_build(stage_payload))
            return stage_payload
        except Exception as exc:  # noqa: BLE001
            hint_payload = self.review_agent.on_stage_fail(
                stage="skill_build",
                retry_hints=("Retry build from saved reflection_report after fixing compiler or candidate issues.",),
                summary=f"Build failed: {exc}",
            )
            self.review_hint_store.write(job_dir, "skill_build", hint_payload.to_dict())
            self.ledger_store.on_stage_fail(job_dir, stage="skill_build", summary=str(exc), retry_hints=hint_payload.retry_hints)
            raise

    def run_execution_stage(self, job_id: str, *, live_execute: bool = False, approval_granted: bool = False) -> dict[str, Any]:
        job_dir = self._job_dir(job_id)
        build_payload = self.run_build_stage(job_id)
        ledger = self.ledger_store.load(job_dir)
        existing = self._read_stage(job_dir, "execution_outcome") if self.stage_artifact_store.exists(job_dir, "execution_outcome") else {}
        if existing and not live_execute:
            return existing
        artifacts_dir = job_dir / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        promoted_root = Path(build_payload.get("promotion", {}).get("package_root") or "").expanduser().resolve()
        primary_result = dict(build_payload.get("qa", {}).get("strategy_qa", {}).get("smoke_test") or {})
        execution_intent = dict(build_payload.get("execution_intent") or {})
        if not existing:
            self.ledger_store.on_stage_start(
                job_dir,
                stage="execution_outcome",
                summary=f"Run execution stage for {build_payload.get('wallet')}",
                input_artifact_ids=[self._stage_artifact_id(job_id, "skill_build")],
            )
        try:
            dry_run_result = dict(build_payload.get("qa", {}).get("execution_qa", {}).get("smoke_test") or {})
            live_result: dict[str, Any] | None = None
            if live_execute and approval_granted:
                live_result = self._execution_live_test(promoted_root, primary_result, execution_intent)
                _write_json(artifacts_dir / "example_execute_live.json", live_result)
            execution_readiness = str((live_result or {}).get("execution_readiness") or dry_run_result.get("execution_readiness") or build_payload.get("execution_readiness") or "blocked_by_risk")
            example_readiness = (
                "live_executed"
                if live_result and live_result.get("ok") and list(live_result.get("tx_hashes") or [])
                else "live_ready"
                if live_result and live_result.get("execution_readiness") == "live_ready"
                else build_payload.get("example_readiness")
            )
            context_sources = _unique_context_sources(
                build_payload.get("context_sources") or [],
                [
                    self.context_assembler.context_source(
                        kind="stage_artifact",
                        identifier=self._stage_artifact_id(job_id, "skill_build"),
                        path=self.stage_artifact_store.artifact_path(job_dir, "skill_build"),
                    )
                ],
            )
            stage_payload = {
                "job_id": job_id,
                "wallet": build_payload.get("wallet"),
                "chain": build_payload.get("chain"),
                "summary": str((live_result or {}).get("summary") or dry_run_result.get("summary") or "Execution stage completed."),
                "execution_readiness": execution_readiness,
                "example_readiness": example_readiness,
                "dry_run": dry_run_result,
                "live": live_result,
                "live_cap_usd": dict(execution_intent.get("metadata") or {}).get("live_cap_usd", 10.0),
                "context_sources": context_sources,
                "lineage": dict(ledger.get("lineage") or {}),
            }
            if live_result and list(live_result.get("tx_hashes") or []):
                updated_ledger = self.ledger_store.update_lineage(job_dir, execution_run_id=",".join(live_result.get("tx_hashes") or []))
                stage_payload["lineage"] = dict(updated_ledger.get("lineage") or {})
            if not existing:
                self._record_stage_success(
                    job_dir,
                    stage="execution_outcome",
                    payload=stage_payload,
                    summary=stage_payload["summary"],
                )
            elif live_result is not None:
                self._record_stage_success(
                    job_dir,
                    stage="execution_outcome",
                    payload=stage_payload,
                    summary=stage_payload["summary"],
                    overwrite=True,
                )
            return stage_payload
        except Exception as exc:  # noqa: BLE001
            hint_payload = self.review_agent.on_stage_fail(
                stage="execution_outcome",
                summary=f"Execution failed: {exc}",
                retry_hints=("Retry execution from promoted skill with explicit approval only when ready.",),
            )
            self.review_hint_store.write(job_dir, "execution_outcome", hint_payload.to_dict())
            self.ledger_store.on_stage_fail(job_dir, stage="execution_outcome", summary=str(exc), retry_hints=hint_payload.retry_hints)
            raise

    def _finalize_job_result(self, job_id: str, *, execution_payload: dict[str, Any]) -> dict[str, Any]:
        job_dir = self._job_dir(job_id)
        distill_payload = self.run_distill_features(job_id)
        reflection_payload = self.run_reflection_stage(job_id)
        build_payload = self.run_build_stage(job_id)
        ledger = self.ledger_store.load(job_dir)
        summary_record = StyleDistillationSummary(
            job_id=job_id,
            wallet=str(build_payload.get("wallet") or distill_payload.get("wallet") or ""),
            chain=str(build_payload.get("chain") or distill_payload.get("resolved_chain") or distill_payload.get("requested_chain") or ""),
            target_skill_name=str(build_payload.get("candidate", {}).get("target_skill_name") or ledger.get("requested_skill_name") or ""),
            candidate_id=build_payload.get("candidate", {}).get("candidate_id"),
            promotion_id=build_payload.get("promotion", {}).get("promotion_id"),
            summary=str(build_payload.get("summary") or reflection_payload.get("summary") or ""),
            confidence=float(build_payload.get("backtest", {}).get("confidence_score") or 0.0),
            qa_status=str(build_payload.get("qa", {}).get("status") or "failed"),
            execution_readiness=str(execution_payload.get("execution_readiness") or build_payload.get("execution_readiness") or "blocked_by_risk"),
            example_readiness=str(execution_payload.get("example_readiness") or build_payload.get("example_readiness") or "blocked_by_missing_features"),
            strategy_quality=str(build_payload.get("strategy_quality") or "insufficient_data"),
            review_backend=str(reflection_payload.get("review_backend") or ""),
            reflection_flow_id=reflection_payload.get("reflection_flow_id"),
            reflection_run_id=reflection_payload.get("reflection_run_id"),
            reflection_session_id=reflection_payload.get("reflection_session_id"),
            reflection_status=reflection_payload.get("reflection_status"),
            fallback_used=bool(reflection_payload.get("fallback_used")),
            stage_statuses=dict(ledger.get("stage_statuses") or {}),
            lineage=dict(ledger.get("lineage") or {}),
            cache_keys=dict(ledger.get("cache_keys") or {}),
            context_sources=_unique_context_sources(
                distill_payload.get("context_sources") or [],
                reflection_payload.get("context_sources") or [],
                build_payload.get("context_sources") or [],
                execution_payload.get("context_sources") or [],
            ),
        )
        result = {
            "status": build_payload.get("qa", {}).get("status") or "failed",
            "job_id": job_id,
            "wallet": summary_record.wallet,
            "chain": summary_record.chain,
            "created_at": summary_record.created_at,
            "extractor_prompt": ledger.get("extractor_prompt"),
            "review_backend": summary_record.review_backend,
            "reflection_flow_id": summary_record.reflection_flow_id,
            "reflection_run_id": summary_record.reflection_run_id,
            "reflection_session_id": summary_record.reflection_session_id,
            "reflection_status": summary_record.reflection_status,
            "fallback_used": summary_record.fallback_used,
            "profile": build_payload.get("profile"),
            "strategy": build_payload.get("strategy"),
            "execution_intent": build_payload.get("execution_intent"),
            "review": build_payload.get("review"),
            "backtest": build_payload.get("backtest"),
            "fetch_metadata": build_payload.get("fetch_metadata"),
            "execution_readiness": summary_record.execution_readiness,
            "example_readiness": summary_record.example_readiness,
            "strategy_quality": summary_record.strategy_quality,
            "data_completeness": build_payload.get("data_completeness"),
            "reflection": reflection_payload.get("reflection"),
            "run": build_payload.get("run"),
            "candidate": build_payload.get("candidate"),
            "package": build_payload.get("package"),
            "validation": build_payload.get("validation"),
            "promotion": build_payload.get("promotion"),
            "qa": build_payload.get("qa"),
            "artifacts": {
                **dict(build_payload.get("artifacts") or {}),
                "job_ledger": str(self.ledger_store.ledger_path(job_dir).resolve()),
                "stage_distill_features": str(self.stage_artifact_store.artifact_path(job_dir, "distill_features").resolve()),
                "stage_reflection": str(self.stage_artifact_store.artifact_path(job_dir, "reflection_report").resolve()),
                "stage_build": str(self.stage_artifact_store.artifact_path(job_dir, "skill_build").resolve()),
                "stage_execution": str(self.stage_artifact_store.artifact_path(job_dir, "execution_outcome").resolve())
                if self.stage_artifact_store.exists(job_dir, "execution_outcome")
                else None,
            },
            "summary": summary_record.to_dict(),
            "stage_statuses": dict(ledger.get("stage_statuses") or {}),
            "lineage": dict(ledger.get("lineage") or {}),
            "cache_keys": dict(ledger.get("cache_keys") or {}),
            "context_sources": list(summary_record.context_sources),
        }
        self.ledger_store.finalize(
            job_dir,
            status=str(result.get("status") or "failed"),
            summary=result["summary"],
            context_sources=result["context_sources"],
        )
        job_end_review = self.review_agent.on_job_end(
            stage="job_end",
            summary=summary_record.summary,
            context_sources=result["context_sources"],
        )
        self.review_hint_store.write(job_dir, "job_end", job_end_review.to_dict())
        _write_json(job_dir / "summary.json", result)
        self._remember_distilled_memory(
            wallet=summary_record.wallet,
            chain=summary_record.chain,
            distill_payload=distill_payload,
            reflection_payload=reflection_payload,
            build_payload=build_payload,
        )
        return result

    def resume_job(self, job_id: str, *, live_execute: bool = False, approval_granted: bool = False) -> dict[str, Any]:
        self.run_distill_features(job_id)
        self.run_reflection_stage(job_id)
        self.run_build_stage(job_id)
        execution_payload = self.run_execution_stage(job_id, live_execute=live_execute, approval_granted=approval_granted)
        return self._finalize_job_result(job_id, execution_payload=execution_payload)

    def distill_wallet_style(
        self,
        *,
        wallet: str,
        chain: str | None = None,
        skill_name: str | None = None,
        extractor_prompt: str | None = None,
        live_execute: bool = False,
        approval_granted: bool = False,
    ) -> dict[str, Any]:
        resolved_wallet = str(wallet or "").strip()
        if not resolved_wallet:
            raise ValueError("wallet is required")
        requested_chain = str(chain or "").strip() or "unknown"
        prompt = str(extractor_prompt or DEFAULT_EXTRACTION_PROMPT).strip() or DEFAULT_EXTRACTION_PROMPT
        target_skill_name = str(skill_name or f"wallet-style-{resolved_wallet[-6:]}").strip()
        job_id, _job_dir = self._create_job(
            wallet=resolved_wallet,
            requested_chain=requested_chain,
            target_skill_name=target_skill_name,
            extractor_prompt=prompt,
        )
        return self.resume_job(job_id, live_execute=live_execute, approval_granted=approval_granted)

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

    def _run_primary_context(self, promoted_root: Path, context: dict[str, Any]) -> dict[str, Any]:
        script_path = promoted_root / "scripts" / "primary.py"
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

    def _generate_example_artifacts(
        self,
        promoted_root: Path,
        preprocessed: dict[str, Any],
        execution_intent: dict[str, Any],
        *,
        artifacts_dir: Path,
    ) -> dict[str, str]:
        preferred_tokens = list(preprocessed.get("derived_stats", {}).get("preferred_tokens") or [])
        top_routes = list(preprocessed.get("derived_stats", {}).get("top_quote_tokens") or [])
        bullish_context = {
            "market_bias": "bullish",
            "wallet_activity_count": preprocessed.get("derived_stats", {}).get("activity_count"),
            "preferred_tokens": preferred_tokens,
            "candidate_tokens": preferred_tokens,
            "available_routes": top_routes,
            "burst_profile": preprocessed.get("derived_stats", {}).get("burst_profile"),
            "desired_notional_usd": preprocessed.get("derived_stats", {}).get("avg_activity_usd") or 150.0,
            "market_context": preprocessed.get("market_context"),
            "signal_context": preprocessed.get("signal_context"),
        }
        risk_off_context = {
            **bullish_context,
            "market_bias": "bearish",
            "signal_context": {
                **dict(preprocessed.get("signal_context") or {}),
                "hard_blocks": list(dict(preprocessed.get("signal_context") or {}).get("hard_blocks") or []) + ["macro_risk_off"],
            },
            "market_context": {
                **dict(preprocessed.get("market_context") or {}),
                "macro": {
                    **dict((preprocessed.get("market_context") or {}).get("macro") or {}),
                    "regime": "risk_off",
                },
            },
        }
        bullish_output = self._run_primary_context(promoted_root, bullish_context)
        risk_off_output = self._run_primary_context(promoted_root, risk_off_context)
        dry_run_output = self._execution_smoke_test(promoted_root, bullish_output, execution_intent)
        _write_json(artifacts_dir / "example_input_bullish.json", bullish_context)
        _write_json(artifacts_dir / "example_input_risk_off.json", risk_off_context)
        _write_json(artifacts_dir / "example_primary_output_bullish.json", bullish_output)
        _write_json(artifacts_dir / "example_primary_output_risk_off.json", risk_off_output)
        _write_json(artifacts_dir / "example_execute_dry_run.json", dry_run_output)
        return {
            "example_input_bullish": str((artifacts_dir / "example_input_bullish.json").resolve()),
            "example_input_risk_off": str((artifacts_dir / "example_input_risk_off.json").resolve()),
            "example_primary_output_bullish": str((artifacts_dir / "example_primary_output_bullish.json").resolve()),
            "example_primary_output_risk_off": str((artifacts_dir / "example_primary_output_risk_off.json").resolve()),
            "example_execute_dry_run": str((artifacts_dir / "example_execute_dry_run.json").resolve()),
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
