from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Mapping
from uuid import uuid4

from ot_skill_enterprise.control_plane.candidates import CandidateSurfaceService, build_candidate_surface_service
from ot_skill_enterprise.chain_assets import chain_benchmark_defaults, chain_quote_symbols
from ot_skill_enterprise.enterprise_bridge import EnterpriseBridge
from ot_skill_enterprise.nextgen.provider_compat import build_provider_compat
from ot_skill_enterprise.reflection.models import ReflectionJobResult, ReflectionJobSpec
from ot_skill_enterprise.reflection.service import (
    PiReflectionService,
    ReflectionQualityError,
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
from .archetype import classify_archetype
from .extractors import DEFAULT_EXTRACTION_PROMPT, WalletStyleExtractor
from .models import ExecutionIntent, StrategyCondition, StrategySpec, StyleDistillationSummary
from .reflection_builders import (
    build_fallback_execution_intent as _build_fallback_execution_intent,
    build_fallback_strategy_spec as _build_fallback_strategy_spec,
)
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
_QUOTE_SYMBOLS = _STABLE_SYMBOLS | chain_quote_symbols()
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
_GENERIC_MEMORY_MARKERS = (
    "balanced risk profile with moderate conviction",
    "standard entry and exit strategy",
)
_MAX_ACTIVITY_PAGES = 5
_MIN_LIVE_LEG_USD = 5.0
_DISTILL_STAGE_VERSION = "2"
_REFLECTION_STAGE_VERSION = "3"
_BUILD_STAGE_VERSION = "2"
_EXECUTION_STAGE_VERSION = "2"
_DEFAULT_STYLE_DISTILLATION_SCRIPT_TIMEOUT_SECONDS = 120.0


class WalletStyleDistillationAttemptsExceeded(RuntimeError):
    def __init__(self, report: dict[str, Any]) -> None:
        self.report = _json_safe(report)
        super().__init__(str(self.report.get("summary") or "wallet style distillation failed"))


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _configured_script_timeout_seconds() -> float:
    raw = os.environ.get("OT_STYLE_DISTILLATION_SCRIPT_TIMEOUT_SECONDS")
    try:
        timeout = float(raw)
    except (TypeError, ValueError):
        return _DEFAULT_STYLE_DISTILLATION_SCRIPT_TIMEOUT_SECONDS
    return timeout if timeout > 0 else _DEFAULT_STYLE_DISTILLATION_SCRIPT_TIMEOUT_SECONDS


def _chain_defaults(chain: Any) -> dict[str, Any]:
    return dict(chain_benchmark_defaults(chain))


def _execution_chain_defaults(chain: Any) -> dict[str, Any]:
    defaults = _chain_defaults(chain)
    return {
        key: defaults.get(key)
        for key in (
            "default_source_token",
            "default_source_token_address",
            "default_source_unit_price_usd",
        )
        if defaults.get(key) is not None
    }


def _run_script_process(script_path: Path, payload: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
    command = [sys.executable, str(script_path)]
    resolved_timeout = timeout if timeout is not None else _configured_script_timeout_seconds()
    try:
        completed = subprocess.run(
            command,
            input=json.dumps(payload, ensure_ascii=False),
            text=True,
            capture_output=True,
            check=False,
            timeout=resolved_timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "returncode": -9,
            "stdout": str(exc.stdout or ""),
            "stderr": f"script timed out after {resolved_timeout:.0f}s: {exc}",
            "parsed_output": None,
        }
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
    }


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


_LOW_SIGNAL_NAME_TOKENS = {
    "wallet",
    "style",
    "trader",
    "trading",
    "position",
    "profile",
    "agent",
}
_PLACEHOLDER_NAME_LABELS = {
    "",
    "unknown",
    "generic",
    "default",
    "wallet-style",
    "wallet style",
    "no_stable_archetype",
    "no stable archetype",
    "no-stable-archetype",
}


def _chain_slug(value: Any) -> str:
    return re.sub(r"[^0-9a-zA-Z]+", "-", str(value or "unknown").strip().lower()).strip("-") or "unknown"


def _wallet_name_suffix(wallet: Any, *, length: int = 6) -> str:
    normalized = re.sub(r"[^0-9a-zA-Z]+", "", str(wallet or "")).lower()
    if normalized.startswith("0x"):
        normalized = normalized[2:]
    if not normalized:
        return "wallet"
    return normalized[-length:]


def _chain_display_name(value: Any) -> str:
    text = _safe_text(value) or "unknown"
    if len(text) <= 5:
        return text.upper()
    return text.replace("-", " ").replace("_", " ").title()


def _humanize_name_slug(value: str) -> str:
    return " ".join(part.capitalize() for part in str(value or "").split("-") if part).strip()


def _condense_name_label(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    slug = re.sub(r"[^0-9a-zA-Z]+", "-", raw.lower()).strip("-")
    if not slug or slug in _PLACEHOLDER_NAME_LABELS:
        return ""
    tokens = [token for token in slug.split("-") if token and token not in _LOW_SIGNAL_NAME_TOKENS]
    if not tokens:
        return ""
    if len(tokens) > 3:
        tokens = tokens[-3:]
    return "-".join(tokens).strip("-")


def _auto_wallet_style_label(style_profile: Mapping[str, Any] | None = None) -> str:
    profile = dict(style_profile or {})
    metadata = dict(profile.get("metadata") or {}) if isinstance(profile.get("metadata"), Mapping) else {}
    nested_archetype = dict(metadata.get("archetype") or {}) if isinstance(metadata.get("archetype"), Mapping) else {}
    direct_archetype = dict(profile.get("archetype") or {}) if isinstance(profile.get("archetype"), Mapping) else {}
    for candidate in (
        direct_archetype.get("primary_archetype"),
        metadata.get("primary_archetype"),
        nested_archetype.get("primary_archetype"),
        profile.get("primary_archetype"),
        profile.get("style_label"),
        metadata.get("style_label"),
    ):
        condensed = _condense_name_label(candidate)
        if condensed:
            return condensed
    return "distill"


def _default_wallet_style_slug(wallet: Any, chain: Any, *, style_profile: Mapping[str, Any] | None = None) -> str:
    label = _auto_wallet_style_label(style_profile)
    return f"{label}-{_chain_slug(chain)}-{_wallet_name_suffix(wallet)}"


def _default_wallet_style_display_name(wallet: Any, chain: Any, *, style_profile: Mapping[str, Any] | None = None) -> str:
    label = _auto_wallet_style_label(style_profile)
    chain_name = _chain_display_name(chain)
    wallet_suffix = _wallet_name_suffix(wallet)
    if label == "distill":
        return f"Wallet Distill {chain_name} {wallet_suffix}"
    return f"{_humanize_name_slug(label)} {chain_name} {wallet_suffix}"


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
_REVIEW_STATUS_ALIASES = {
    "low_confidence": "generate_with_low_confidence",
    "manual-review": "needs_manual_review",
    "manual_review": "needs_manual_review",
    "no_pattern": "no_pattern_detected",
    "runtime_error": "runtime_failed",
}
_GENERATING_REVIEW_STATUSES = {"generate", "generate_with_low_confidence"}
_NON_GENERATING_REVIEW_STATUSES = {
    "insufficient_signal",
    "needs_manual_review",
    "no_pattern_detected",
    "runtime_failed",
}


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


def _truncate_text(value: Any, *, max_chars: int) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _is_generic_memory_summary(value: Any) -> bool:
    lowered = str(value or "").strip().lower()
    return any(marker in lowered for marker in _GENERIC_MEMORY_MARKERS)


def _normalize_review_status_value(status: Any, *, should_generate_candidate: Any = None) -> str:
    normalized = str(status or "").strip().lower()
    normalized = _REVIEW_STATUS_ALIASES.get(normalized, normalized)
    if normalized in _GENERATING_REVIEW_STATUSES | _NON_GENERATING_REVIEW_STATUSES:
        return normalized
    return "generate" if bool(should_generate_candidate) else "needs_manual_review"


def _review_generation_decision(review_payload: Mapping[str, Any]) -> dict[str, Any]:
    status = _normalize_review_status_value(
        review_payload.get("status"),
        should_generate_candidate=review_payload.get("should_generate_candidate"),
    )
    should_generate_candidate = status in _GENERATING_REVIEW_STATUSES
    return {
        "status": status,
        "should_generate_candidate": should_generate_candidate,
        "skip_generation": not should_generate_candidate,
    }


def _skipped_candidate_artifacts(*, target_skill_name: str, review_status: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, str]]:
    summary = f"Candidate generation skipped because review.status={review_status}."
    candidate = {
        "candidate_id": None,
        "candidate_type": "script",
        "target_skill_name": target_skill_name,
        "target_skill_kind": "wallet_style",
        "status": "skipped",
        "summary": summary,
    }
    package = {
        "status": "skipped",
        "summary": summary,
    }
    validation = {
        "status": "skipped",
        "summary": summary,
        "checks": [],
        "issues": [],
    }
    promotion = {
        "promotion_id": None,
        "package_root": None,
        "status": "skipped",
        "summary": summary,
    }
    smoke = {
        "ok": False,
        "skipped": True,
        "returncode": 0,
        "stdout": "",
        "stderr": "",
        "parsed_output": {
            "summary": summary,
            "execution_readiness": "blocked_by_review_status",
            "metadata": {
                "review_status": review_status,
                "skip_reason": "candidate_generation_disabled",
            },
        },
        "summary": summary,
        "execution_readiness": "blocked_by_review_status",
    }
    return candidate, package, validation, promotion, smoke, {}


def _trim_focus_market_context(items: list[Any], *, limit: int, minimal: bool = False) -> list[dict[str, Any]]:
    trimmed: list[dict[str, Any]] = []
    for raw in list(items)[:limit]:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        if minimal:
            item = {
                "symbol": item.get("symbol"),
                "price_change_1h_pct": item.get("price_change_1h_pct"),
                "price_change_24h_pct": item.get("price_change_24h_pct"),
                "momentum_label": item.get("momentum_label"),
                "volatility_regime": item.get("volatility_regime"),
                "volume_to_liquidity_ratio": item.get("volume_to_liquidity_ratio"),
                "liquidity_usd": item.get("liquidity_usd"),
            }
        for key, value in list(item.items()):
            item[key] = _truncate_text(value, max_chars=120)
        trimmed.append(item)
    return trimmed


def _trim_compact_payload(payload: dict[str, Any], *, list_limit: int, minimal_market: bool = False) -> dict[str, Any]:
    compact = _json_safe(payload)
    for key in ("recent_trade_samples", "recent_activity", "signals", "holdings", "token_snapshots", "focus_tokens"):
        if isinstance(compact.get(key), list):
            compact[key] = compact[key][:list_limit]
    market_context = dict(compact.get("market_context") or {})
    if isinstance(market_context.get("focus_token_context"), list):
        market_context["focus_token_context"] = _trim_focus_market_context(
            list(market_context.get("focus_token_context") or []),
            limit=max(1, min(list_limit, 3)),
            minimal=minimal_market,
        )
    compact["market_context"] = market_context
    signal_context = dict(compact.get("signal_context") or {})
    if isinstance(signal_context.get("top_entry_factors"), list):
        signal_context["top_entry_factors"] = [
            {
                "factor_type": item.get("factor_type"),
                "frequency": item.get("frequency"),
                "confidence": item.get("confidence"),
            }
            for item in signal_context.get("top_entry_factors", [])[: max(1, min(list_limit, 3))]
            if isinstance(item, dict)
        ]
    for key in ("derived_memory_summary", "derived_memory_style_labels", "hard_blocks", "warnings"):
        if isinstance(signal_context.get(key), list):
            signal_context[key] = signal_context[key][: max(1, min(list_limit, 3))]
    compact["signal_context"] = signal_context
    derived_stats = dict(compact.get("derived_stats") or {})
    for key in (
        "dominant_actions",
        "preferred_tokens",
        "top_quote_tokens",
        "secondary_archetypes",
        "behavioral_patterns",
        "archetype_token_preference",
        "archetype_evidence_summary",
        "derived_memory_preferred_tokens",
        "derived_memory_active_windows",
        "derived_memory_summary",
        "derived_memory_style_labels",
        "active_windows",
    ):
        if isinstance(derived_stats.get(key), list):
            derived_stats[key] = [_truncate_text(item, max_chars=80) for item in derived_stats[key][: max(1, min(list_limit, 3))]]
    if isinstance(derived_stats.get("burst_profile"), str):
        derived_stats["burst_profile"] = _truncate_text(derived_stats.get("burst_profile"), max_chars=80)
    compact["derived_stats"] = derived_stats
    if isinstance(compact.get("behavioral_patterns"), list):
        compact["behavioral_patterns"] = [
            {
                "pattern_label": item.get("pattern_label"),
                "strength": item.get("strength"),
                "evidence": list(item.get("evidence") or [])[:2],
            }
            for item in compact.get("behavioral_patterns", [])[: max(1, min(list_limit, 3))]
            if isinstance(item, dict)
        ]
    archetype = dict(compact.get("archetype") or {})
    if archetype:
        if isinstance(archetype.get("secondary_archetypes"), list):
            archetype["secondary_archetypes"] = archetype["secondary_archetypes"][: max(1, min(list_limit, 3))]
        if isinstance(archetype.get("evidence"), list):
            archetype["evidence"] = [_truncate_text(item, max_chars=80) for item in archetype["evidence"][: max(1, min(list_limit, 3))]]
        if isinstance(archetype.get("token_preference"), list):
            archetype["token_preference"] = archetype["token_preference"][: max(1, min(list_limit, 3))]
        if isinstance(archetype.get("behavioral_patterns"), list):
            archetype["behavioral_patterns"] = [
                {
                    "pattern_label": item.get("pattern_label"),
                    "strength": item.get("strength"),
                    "evidence": list(item.get("evidence") or [])[:2],
                }
                for item in archetype.get("behavioral_patterns", [])[: max(1, min(list_limit, 3))]
                if isinstance(item, dict)
            ]
        compact["archetype"] = archetype
    wallet_summary = dict(compact.get("wallet_summary") or {})
    for key, value in list(wallet_summary.items()):
        wallet_summary[key] = _truncate_text(value, max_chars=80)
    compact["wallet_summary"] = wallet_summary
    enrichment = dict(compact.get("enrichment") or {})
    if isinstance(enrichment.get("warnings"), list):
        enrichment["warnings"] = enrichment["warnings"][: max(1, min(list_limit, 2))]
    compact["enrichment"] = enrichment
    return compact


def _minimal_compact_payload(payload: dict[str, Any]) -> dict[str, Any]:
    compact = _json_safe(payload)
    market_context = dict(compact.get("market_context") or {})
    signal_context = dict(compact.get("signal_context") or {})
    derived_stats = dict(compact.get("derived_stats") or {})
    archetype = dict(compact.get("archetype") or {})
    return {
        "wallet": compact.get("wallet"),
        "chain": compact.get("chain"),
        "wallet_summary": {
            "wallet_address": dict(compact.get("wallet_summary") or {}).get("wallet_address"),
            "chain": dict(compact.get("wallet_summary") or {}).get("chain"),
            "total_balance_usd": dict(compact.get("wallet_summary") or {}).get("total_balance_usd"),
            "total_profit_ratio": dict(compact.get("wallet_summary") or {}).get("total_profit_ratio"),
            "total_win_ratio": dict(compact.get("wallet_summary") or {}).get("total_win_ratio"),
        },
        "focus_tokens": list(compact.get("focus_tokens") or [])[:2],
        "market_context": {
            "macro": dict(market_context.get("macro") or {}),
            "focus_token_context": _trim_focus_market_context(list(market_context.get("focus_token_context") or []), limit=2, minimal=True),
        },
        "signal_context": {
            "top_entry_factors": [
                {
                    "factor_type": item.get("factor_type"),
                    "frequency": item.get("frequency"),
                    "confidence": item.get("confidence"),
                }
                for item in list(signal_context.get("top_entry_factors") or [])[:2]
                if isinstance(item, dict)
            ],
            "hard_blocks": list(signal_context.get("hard_blocks") or [])[:3],
            "warnings": list(signal_context.get("warnings") or [])[:3],
            "active_signals": signal_context.get("active_signals"),
            "high_severity_count": signal_context.get("high_severity_count"),
        },
        "behavioral_patterns": [
            {
                "pattern_label": item.get("pattern_label"),
                "strength": item.get("strength"),
                "evidence": list(item.get("evidence") or [])[:2],
            }
            for item in list(compact.get("behavioral_patterns") or [])[:3]
            if isinstance(item, dict)
        ],
        "archetype": {
            "primary_label": archetype.get("primary_label"),
            "secondary_archetypes": list(archetype.get("secondary_archetypes") or [])[:3],
            "confidence": archetype.get("confidence"),
            "evidence": list(archetype.get("evidence") or [])[:3],
            "token_preference": list(archetype.get("token_preference") or [])[:3],
            "behavioral_patterns": [
                {
                    "pattern_label": item.get("pattern_label"),
                    "strength": item.get("strength"),
                }
                for item in list(archetype.get("behavioral_patterns") or [])[:3]
                if isinstance(item, dict)
            ],
        },
        "derived_stats": {
            "activity_count": derived_stats.get("activity_count"),
            "buy_count": derived_stats.get("buy_count"),
            "sell_count": derived_stats.get("sell_count"),
            "preferred_tokens": list(derived_stats.get("preferred_tokens") or [])[:3],
            "top_quote_tokens": list(derived_stats.get("top_quote_tokens") or [])[:3],
            "primary_archetype": derived_stats.get("primary_archetype"),
            "secondary_archetypes": list(derived_stats.get("secondary_archetypes") or [])[:3],
            "behavioral_patterns": list(derived_stats.get("behavioral_patterns") or [])[:3],
            "archetype_confidence": derived_stats.get("archetype_confidence"),
            "archetype_evidence_summary": list(derived_stats.get("archetype_evidence_summary") or [])[:3],
            "avg_activity_usd": derived_stats.get("avg_activity_usd"),
            "largest_activity_usd": derived_stats.get("largest_activity_usd"),
            "top_holding_symbol": derived_stats.get("top_holding_symbol"),
            "top_holding_allocation_pct": derived_stats.get("top_holding_allocation_pct"),
            "stablecoin_allocation_pct": derived_stats.get("stablecoin_allocation_pct"),
            "completed_trade_count": derived_stats.get("completed_trade_count"),
            "win_rate": derived_stats.get("win_rate"),
            "profit_factor": derived_stats.get("profit_factor"),
            "avg_holding_seconds": derived_stats.get("avg_holding_seconds"),
            "holding_classification": derived_stats.get("holding_classification"),
            "max_drawdown_pct": derived_stats.get("max_drawdown_pct"),
            "loss_tolerance_label": derived_stats.get("loss_tolerance_label"),
            "averaging_pattern": derived_stats.get("averaging_pattern"),
            "avg_position_splits": derived_stats.get("avg_position_splits"),
            "trades_per_day": derived_stats.get("trades_per_day"),
            "open_position_ratio": derived_stats.get("open_position_ratio"),
            "pnl_multiplier_max": derived_stats.get("pnl_multiplier_max"),
            "pnl_multiplier_median": derived_stats.get("pnl_multiplier_median"),
            "small_cap_trade_ratio": derived_stats.get("small_cap_trade_ratio"),
            "profit_add_ratio": derived_stats.get("profit_add_ratio"),
            "burst_profile": derived_stats.get("burst_profile"),
            "active_windows": list(derived_stats.get("active_windows") or [])[:3],
        },
        "fetch_metadata": dict(compact.get("fetch_metadata") or {}),
    }


def _shrink_compact_payload(payload: dict[str, Any], *, max_bytes: int = _MAX_COMPACT_BYTES) -> dict[str, Any]:
    compact = _json_safe(payload)
    if _compact_size_bytes(compact) <= max_bytes:
        compact["compact_size_bytes"] = _compact_size_bytes(compact)
        return compact
    for list_limit, minimal_market in ((4, False), (3, False), (2, True), (1, True)):
        trimmed = _trim_compact_payload(compact, list_limit=list_limit, minimal_market=minimal_market)
        if _compact_size_bytes(trimmed) <= max_bytes:
            trimmed["compact_size_bytes"] = _compact_size_bytes(trimmed)
            return trimmed
        compact = trimmed

    aggressive = dict(compact)
    for key in ("signals", "recent_activity", "recent_trade_samples", "holdings", "token_snapshots"):
        aggressive.pop(key, None)
        if _compact_size_bytes(aggressive) <= max_bytes:
            aggressive["compact_size_bytes"] = _compact_size_bytes(aggressive)
            return aggressive

    minimal = _minimal_compact_payload(payload)
    if _compact_size_bytes(minimal) > max_bytes:
        # Keep hard blocks and essential numeric summaries even under pathological payload sizes.
        minimal["market_context"] = {"macro": dict(minimal.get("market_context", {}).get("macro") or {})}
        minimal["signal_context"] = {
            "hard_blocks": list(dict(minimal.get("signal_context") or {}).get("hard_blocks") or [])[:3],
            "warnings": list(dict(minimal.get("signal_context") or {}).get("warnings") or [])[:3],
            "active_signals": dict(minimal.get("signal_context") or {}).get("active_signals"),
        }
    minimal["compact_size_bytes"] = _compact_size_bytes(minimal)
    return minimal


def _extract_embedded_json_texts(raw_text: str) -> tuple[str, ...]:
    text = str(raw_text).strip()
    if not text:
        return ()

    matches: list[str] = []
    fenced_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, flags=re.IGNORECASE)
    if fenced_match:
        matches.append(fenced_match.group(1).strip())

    first_brace = text.find("{")
    if first_brace != -1:
        matches.append(text[first_brace:].strip())
        last_brace = text.rfind("}")
        if last_brace > first_brace:
            matches.append(text[first_brace : last_brace + 1].strip())

    deduped = []
    seen: set[str] = set()
    for item in matches:
        if not item or item in seen:
            continue
        deduped.append(item)
        seen.add(item)
    return tuple(deduped)


def _close_trailing_json_structures(text: str) -> str:
    candidate = text.rstrip()
    if not candidate:
        return candidate

    stack: list[str] = []
    in_string = False
    escaped = False
    matching = {"{": "}", "[": "]"}
    for char in candidate:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char in matching:
            stack.append(char)
            continue
        if char == "}" and stack and stack[-1] == "{":
            stack.pop()
            continue
        if char == "]" and stack and stack[-1] == "[":
            stack.pop()
    if stack:
        candidate += "".join(matching[item] for item in reversed(stack))
    return candidate


def _strip_json_trailing_commas(text: str) -> str:
    return re.sub(r",\s*([}\]])", r"\1", text, flags=re.MULTILINE)


def _parse_repairable_json(candidate: str) -> dict[str, Any] | None:
    for prepared in {
        candidate,
        candidate.rstrip(),
        candidate.rstrip().rstrip(","),
        _strip_json_trailing_commas(candidate),
        _close_trailing_json_structures(_strip_json_trailing_commas(candidate)),
    }:
        if not prepared:
            continue
        try:
            parsed = json.loads(prepared)
        except json.JSONDecodeError:
            try:
                decoder = json.JSONDecoder()
                parsed, _ = decoder.raw_decode(prepared.strip())
            except (json.JSONDecodeError, TypeError):
                continue
        if isinstance(parsed, dict):
            return dict(parsed)
    return None


def _try_salvage_from_raw_text(raw_output: Mapping[str, Any]) -> dict[str, Any] | None:
    if not isinstance(raw_output, Mapping):
        return None
    text = str(raw_output.get("raw_text") or raw_output.get("text") or raw_output.get("output") or "").strip()
    for candidate in _extract_embedded_json_texts(text):
        if parsed := _parse_repairable_json(candidate):
            return parsed
    return None


def _serialize_trade_pairing(
    completed_trades: list[Any],
    open_positions: list[Any],
    statistics: Any,
    *,
    archetype: Any | None = None,
) -> dict[str, Any]:
    payload = {
        "completed_trades": [item.to_dict() for item in completed_trades],
        "open_positions": [item.to_dict() for item in open_positions],
        "statistics": statistics.to_dict(),
    }
    if archetype is not None:
        payload["archetype"] = archetype.to_dict() if hasattr(archetype, "to_dict") else _json_safe(archetype)
    return payload


def _archetype_metadata_fields(payload: dict[str, Any] | None) -> dict[str, Any]:
    archetype = dict(payload or {})
    primary_archetype = str(archetype.get("primary_label") or archetype.get("trading_archetype") or "").strip()
    secondary_archetypes = [
        str(item).strip()
        for item in list(archetype.get("secondary_archetypes") or [])
        if str(item).strip()
    ][:3]
    behavioral_patterns: list[str] = []
    for item in list(archetype.get("behavioral_patterns") or [])[:4]:
        label = str(item.get("pattern_label") or "").strip() if isinstance(item, dict) else str(item or "").strip()
        if label:
            behavioral_patterns.append(label)
    evidence = [
        str(item).strip()
        for item in list(archetype.get("evidence") or [])
        if str(item).strip()
    ][:5]
    token_preference = [
        str(item).strip()
        for item in list(archetype.get("token_preference") or [])
        if str(item).strip()
    ][:4]
    return {
        "primary_archetype": primary_archetype,
        "secondary_archetypes": secondary_archetypes,
        "behavioral_patterns": behavioral_patterns,
        "archetype_confidence": archetype.get("confidence", 0.0),
        "archetype_evidence_summary": evidence,
        "archetype_token_preference": token_preference,
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
    archetype: dict[str, Any] | None = None,
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
    archetype_payload = dict(archetype or {})
    raw_behavioral_patterns = [dict(item) for item in list(archetype_payload.get("behavioral_patterns") or []) if isinstance(item, dict)]
    behavioral_pattern_labels = [
        str(item.get("pattern_label") or "").strip()
        for item in raw_behavioral_patterns
        if str(item.get("pattern_label") or "").strip()
    ]
    primary_archetype = str(archetype_payload.get("primary_label") or archetype_payload.get("trading_archetype") or "").strip()
    secondary_archetypes = [
        str(item).strip()
        for item in list(archetype_payload.get("secondary_archetypes") or [])
        if str(item).strip()
    ]
    archetype_evidence = [
        str(item).strip()
        for item in list(archetype_payload.get("evidence") or [])
        if str(item).strip()
    ]
    archetype_token_preference = [
        str(item).strip()
        for item in list(archetype_payload.get("token_preference") or [])
        if str(item).strip()
    ]
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
        "behavioral_patterns": raw_behavioral_patterns[:4],
        "archetype": {
            "trading_archetype": archetype_payload.get("trading_archetype") or primary_archetype,
            "primary_label": primary_archetype,
            "secondary_archetypes": secondary_archetypes[:3],
            "behavioral_patterns": raw_behavioral_patterns[:4],
            "confidence": archetype_payload.get("confidence", 0.0),
            "evidence": archetype_evidence[:5],
            "token_preference": archetype_token_preference[:4],
            "trades_per_day": archetype_payload.get("trades_per_day", trade_stats.get("trades_per_day", 0.0)),
            "open_position_ratio": archetype_payload.get("open_position_ratio", trade_stats.get("open_position_ratio", 0.0)),
            "pnl_multiplier_max": archetype_payload.get("pnl_multiplier_max", trade_stats.get("pnl_multiplier_max", 0.0)),
            "pnl_multiplier_median": archetype_payload.get("pnl_multiplier_median", trade_stats.get("pnl_multiplier_median", 0.0)),
        },
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
            "trades_per_day": trade_stats.get("trades_per_day", 0.0),
            "open_position_ratio": trade_stats.get("open_position_ratio", 0.0),
            "pnl_multiplier_max": trade_stats.get("pnl_multiplier_max", 0.0),
            "pnl_multiplier_median": trade_stats.get("pnl_multiplier_median", 0.0),
            "profitable_avg_holding_seconds": trade_stats.get("profitable_avg_holding_seconds", 0.0),
            "losing_avg_holding_seconds": trade_stats.get("losing_avg_holding_seconds", 0.0),
            "profit_reinvestment_rate": trade_stats.get("profit_reinvestment_rate", 0.0),
            "first_buy_avg_mcap_usd": trade_stats.get("first_buy_avg_mcap_usd", 0.0),
            "small_cap_trade_ratio": trade_stats.get("small_cap_trade_ratio", 0.0),
            "profit_add_ratio": trade_stats.get("profit_add_ratio", 0.0),
            "primary_archetype": primary_archetype,
            "secondary_archetypes": secondary_archetypes[:3],
            "behavioral_patterns": behavioral_pattern_labels[:4],
            "archetype_confidence": archetype_payload.get("confidence", 0.0),
            "archetype_evidence_summary": archetype_evidence[:5],
            "archetype_token_preference": archetype_token_preference[:4],
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


def _mock_behavioral_pattern_labels(preprocessed: Mapping[str, Any]) -> list[str]:
    labels: list[str] = []
    for item in list(preprocessed.get("behavioral_patterns") or []):
        if isinstance(item, dict):
            label = str(item.get("pattern_label") or "").strip()
        else:
            label = str(item or "").strip()
        if label:
            labels.append(label)
    if labels:
        return labels
    archetype_payload = dict(preprocessed.get("archetype") or {})
    for item in list(archetype_payload.get("behavioral_patterns") or []):
        if not isinstance(item, dict):
            continue
        label = str(item.get("pattern_label") or "").strip()
        if label:
            labels.append(label)
    return labels


def _build_mock_minimal_reflection_response(
    *,
    wallet: str,
    chain: str,
    preprocessed: Mapping[str, Any],
    prompt: str,
) -> dict[str, Any]:
    extractor = WalletStyleExtractor()
    mock_profile, mock_review = extractor.extract(dict(preprocessed), system_prompt=prompt)
    mock_strategy = _fallback_strategy_spec(dict(preprocessed), mock_profile.to_dict())
    archetype_payload = dict(preprocessed.get("archetype") or {})
    profile_archetype = dict(mock_profile.metadata.get("archetype") or {})
    primary_archetype = (
        str(archetype_payload.get("primary_label") or "").strip()
        or str(profile_archetype.get("primary_archetype") or "").strip()
        or str(mock_profile.style_label)
    )
    secondary_archetypes = list(archetype_payload.get("secondary_archetypes") or profile_archetype.get("secondary_archetypes") or [])
    behavioral_patterns = _mock_behavioral_pattern_labels(preprocessed)
    return {
        "wallet": wallet,
        "chain": chain,
        "style_label": str(mock_profile.style_label),
        "summary": str(mock_profile.summary),
        "primary_archetype": primary_archetype,
        "secondary_archetypes": secondary_archetypes,
        "behavioral_patterns": behavioral_patterns,
        "archetype_confidence": archetype_payload.get("confidence") or profile_archetype.get("archetype_confidence"),
        "archetype_evidence_summary": list(archetype_payload.get("evidence") or profile_archetype.get("archetype_evidence_summary") or []),
        "dominant_actions": list(mock_profile.dominant_actions),
        "preferred_tokens": list(mock_profile.preferred_tokens),
        "active_windows": list(mock_profile.active_windows),
        "risk_flags": list(mock_strategy.risk_controls),
        "setup_label": str(mock_strategy.setup_label),
        "setup_summary": str(mock_strategy.summary),
        "entry_signals": [str(item.condition) for item in tuple(mock_strategy.entry_conditions)[:2]],
        "prompt_focus": list(mock_profile.prompt_focus),
        "review_status": str(mock_review.status),
        "should_generate_candidate": bool(mock_review.should_generate_candidate),
        "reasoning": str(mock_review.reasoning),
        "nudge_prompt": str(mock_review.nudge_prompt),
        "metadata": {"source_contract": "minimal_mock"},
    }


def _fallback_strategy_spec(preprocessed: dict[str, Any], profile_payload: dict[str, Any]) -> StrategySpec:
    return _build_fallback_strategy_spec(preprocessed, profile_payload)


def _fallback_execution_intent(preprocessed: dict[str, Any], strategy: StrategySpec) -> ExecutionIntent:
    return _build_fallback_execution_intent(preprocessed, strategy)


def _configured_data_source_adapter_id(explicit: str | None = None) -> str | None:
    candidate = str(
        explicit
        or os.environ.get("OT_NEXTGEN_DATA_SOURCE_ADAPTER")
        or os.environ.get("OT_DATA_SOURCE_ADAPTER")
        or ""
    ).strip()
    return candidate or None


class WalletStyleDistillationService:
    def __init__(
        self,
        *,
        project_root: Path | None = None,
        workspace_root: Path | None = None,
        provider: Any | None = None,
        reflection_service: PiReflectionService | None = None,
        adapter_registry: Any | None = None,
        data_source_adapter_id: str | None = None,
        require_explicit_data_source_adapter: bool = False,
        allow_builtin_adapter_registry_fallback: bool = True,
    ) -> None:
        self.project_root = Path(project_root).expanduser().resolve() if project_root is not None else resolve_project_root()
        self.workspace_root = Path(workspace_root).expanduser().resolve() if workspace_root is not None else (self.project_root / ".ot-workspace").resolve()
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        selected_adapter_id = _configured_data_source_adapter_id(data_source_adapter_id)
        if selected_adapter_id is not None and adapter_registry is None:
            raise ValueError("explicit data_source_adapter_id requires adapter_registry injection")
        resolved_provider = provider
        if resolved_provider is None and selected_adapter_id is None and require_explicit_data_source_adapter:
            raise ValueError("nextgen distillation requires an explicit data_source_adapter_id or provider")
        if resolved_provider is None and selected_adapter_id is None:
            resolved_provider = build_ave_provider()
        self.provider = build_provider_compat(
            workspace_dir=self.workspace_root,
            provider=resolved_provider,
            adapter_registry=adapter_registry,
            adapter_id=selected_adapter_id,
            allow_builtin_registry_fallback=allow_builtin_adapter_registry_fallback,
        )
        self.data_source_adapter_id = selected_adapter_id
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

    @property
    def failure_report_root(self) -> Path:
        path = self.workspace_root / "style-distillation-reports"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _run_script_process(
        self,
        script_path: Path,
        payload: dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        return _run_script_process(script_path, payload, timeout=timeout)

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

    def _attempt_failure_record(self, *, attempt: int, job_id: str, error: Exception) -> dict[str, Any]:
        job_dir = self._job_dir(job_id)
        ledger = self.ledger_store.load(job_dir)
        summary_path = job_dir / "summary.json"
        stage_statuses = _json_safe(ledger.get("stage_statuses") or {})
        failed_stage = str(ledger.get("current_stage") or "").strip()
        if not failed_stage:
            for stage_name, payload in list(stage_statuses.items()):
                if isinstance(payload, dict) and str(payload.get("status") or "").strip().lower() == "failed":
                    failed_stage = str(stage_name)
                    break
        return {
            "attempt": attempt,
            "job_id": job_id,
            "status": "failed",
            "failed_stage": failed_stage or None,
            "error_type": type(error).__name__,
            "error": str(error),
            "job_dir": str(job_dir.resolve()),
            "summary_path": str(summary_path.resolve()) if summary_path.is_file() else None,
            "stage_statuses": stage_statuses,
        }

    def _attempt_success_record(self, *, attempt: int, result: dict[str, Any]) -> dict[str, Any]:
        return {
            "attempt": attempt,
            "job_id": result.get("job_id"),
            "status": "succeeded",
            "failed_stage": None,
            "error_type": None,
            "error": None,
            "job_dir": str(self._job_dir(str(result.get("job_id") or "")).resolve()) if result.get("job_id") else None,
            "summary_path": str((self._job_dir(str(result.get("job_id") or "")) / "summary.json").resolve()) if result.get("job_id") else None,
            "stage_statuses": _json_safe(result.get("stage_statuses") or {}),
        }

    def _write_attempt_failure_report(
        self,
        *,
        wallet: str,
        chain: str,
        max_attempts: int,
        attempts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        wallet_suffix = re.sub(r"[^0-9a-zA-Z]+", "", str(wallet or ""))[-8:] or "wallet"
        chain_slug = re.sub(r"[^0-9a-zA-Z]+", "-", str(chain or "unknown")).strip("-") or "unknown"
        report_path = self.failure_report_root / f"{chain_slug}-{wallet_suffix}-{timestamp}.json"
        final_error = attempts[-1] if attempts else {}
        report = {
            "status": "failed",
            "summary": f"wallet style distillation failed after {max_attempts} attempts",
            "wallet": wallet,
            "chain": chain,
            "max_attempts": max_attempts,
            "attempt_count": len(attempts),
            "attempts": _json_safe(attempts),
            "final_error": _json_safe(final_error),
        }
        _write_json(report_path, report)
        report["report_path"] = str(report_path.resolve())
        return report

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

    def _reflection_hard_constraints(self, *, wallet: str, chain: str, retry: bool = False) -> tuple[str, ...]:
        constraints = [
            "Treat injected context as background only.",
            "Return strict JSON only.",
            f"Use wallet exactly {wallet}.",
            f"Use chain exactly {chain}.",
            "Produce only the minimal distill contract; Python will assemble the final profile, strategy, and execution intent.",
            "Use derived_stats.primary_archetype, secondary_archetypes, behavioral_patterns, archetype_confidence, and archetype_evidence_summary as the primary taxonomy when present.",
            "Legal review_status values are generate, generate_with_low_confidence, insufficient_signal, no_pattern_detected, needs_manual_review, and runtime_failed.",
            "insufficient_signal, no_pattern_detected, and needs_manual_review are successful outcomes and should not fabricate a strong setup.",
            "If you are unsure, keep optional fields empty and still return valid wallet-specific JSON.",
        ]
        if retry:
            constraints.append("The previous attempt failed. Keep the response shorter and preserve only wallet-specific evidence.")
        return tuple(constraints)

    def _reflection_retry_hint_payload(self, *, error: Exception, attempt: int) -> dict[str, Any]:
        return {
            "stage": "reflection_report",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "next_stage_hints": [],
            "retry_hints": [
                f"Reflection retry {attempt}: previous output was rejected. Fix this issue directly: {error}",
                "Use the compact_input as source of truth and produce wallet-specific strategy fields.",
            ],
            "context_reduction_hints": ["Prefer compact_input facts over generic template text."],
        }

    def _resolve_reflection_report(
        self,
        *,
        wallet: str,
        chain: str,
        prompt: str,
        preprocessed: dict[str, Any],
        artifacts_dir: Path,
        derived_memories: list[dict[str, Any]] | None = None,
        review_hints: list[dict[str, Any]] | None = None,
    ) -> tuple[Any, StrategySpec, ExecutionIntent, Any, ReflectionJobResult, ReflectionJobSpec, Any, bool, str]:
        extractor = WalletStyleExtractor()
        base_review_hints = [dict(item) for item in list(review_hints or []) if isinstance(item, dict)]
        derived_memories = [dict(item) for item in list(derived_memories or []) if isinstance(item, dict)]
        last_error: Exception | None = None
        last_result: ReflectionJobResult | None = None
        last_spec = None
        last_envelope = None
        for attempt in range(3):
            attempt_review_hints = list(base_review_hints)
            retry_reason = None
            if attempt > 0 and last_error is not None:
                attempt_review_hints.append(self._reflection_retry_hint_payload(error=last_error, attempt=attempt + 1))
                retry_reason = str(last_error)
            envelope = self.context_assembler.build_reflection_envelope(
                wallet=wallet,
                chain=chain,
                derived_memories=derived_memories,
                review_hints=attempt_review_hints,
                retry_reason=retry_reason,
                hard_constraints=self._reflection_hard_constraints(wallet=wallet, chain=chain, retry=attempt > 0),
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
            last_result = reflection_result
            last_spec = reflection_spec
            last_envelope = envelope
            candidate_outputs: list[tuple[dict[str, Any], bool]] = []
            if isinstance(reflection_result.normalized_output, dict):
                candidate_outputs.append((dict(reflection_result.normalized_output), False))
            salvaged_output = _try_salvage_from_raw_text(reflection_result.raw_output)
            if salvaged_output is not None and salvaged_output != reflection_result.normalized_output:
                candidate_outputs.append((salvaged_output, True))
            for candidate_output, salvaged in candidate_outputs:
                try:
                    reflection_report = parse_wallet_style_review_report(
                        candidate_output,
                        wallet=wallet,
                        chain=chain,
                        preprocessed=preprocessed,
                        prompt=prompt,
                    )
                    reflection_result.normalized_output = reflection_report.normalized_output
                    if salvaged:
                        reflection_result.metadata = {
                            **dict(reflection_result.metadata or {}),
                            "raw_text_salvaged": True,
                        }
                    return (
                        reflection_report.profile,
                        reflection_report.strategy,
                        reflection_report.execution_intent,
                        reflection_report.review,
                        reflection_result,
                        reflection_spec,
                        envelope,
                        False,
                        reflection_result.review_backend,
                    )
                except (ReflectionQualityError, ValueError) as exc:
                    last_error = exc
                    continue
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    break

        profile, review = extractor.extract(preprocessed, system_prompt=prompt)
        strategy = _fallback_strategy_spec(preprocessed, profile.to_dict())
        execution_intent = _fallback_execution_intent(preprocessed, strategy)
        if last_result is not None:
            last_result.fallback_used = True
            last_result.metadata = {
                **dict(last_result.metadata or {}),
                "fallback_error": str(last_error) if last_error is not None else "unknown reflection failure",
                "attempted_review_backend": last_result.review_backend,
            }
            last_result.normalized_output = {
                "profile": profile.to_dict(),
                "strategy": strategy.to_dict(),
                "execution_intent": execution_intent.to_dict(),
                "review": review.to_dict(),
                "fallback_reason": str(last_error) if last_error is not None else "unknown reflection failure",
            }
        if last_spec is None or last_envelope is None:
            last_envelope = self.context_assembler.build_reflection_envelope(
                wallet=wallet,
                chain=chain,
                hard_constraints=self._reflection_hard_constraints(wallet=wallet, chain=chain),
            )
            last_spec = self._build_reflection_spec(
                wallet=wallet,
                chain=chain,
                prompt=prompt,
                preprocessed=preprocessed,
                artifacts_dir=artifacts_dir,
            )
            last_spec.injected_context = last_envelope.to_dict()
        if last_result is None:
            last_result = ReflectionJobResult(
                review_backend="pi-reflection-runtime",
                reflection_run_id=None,
                reflection_session_id=None,
                status="failed",
                raw_output={"error": str(last_error) if last_error is not None else "reflection did not return a result"},
                normalized_output={},
                fallback_used=True,
                metadata={"fallback_error": str(last_error) if last_error is not None else "reflection did not return a result"},
            )
        return (
            profile,
            strategy,
            execution_intent,
            review,
            last_result,
            last_spec,
            last_envelope,
            True,
            "wallet-style-extractor-fallback",
        )

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

    def _select_derived_memories(self, wallet: str, chain: str, *, limit: int = 1) -> list[dict[str, Any]]:
        recalled = self.derived_memory_store.recall(wallet, chain, limit=10)
        recalled.sort(
            key=lambda item: (
                float(item.get("memory_weight") or 0.0),
                str(item.get("created_at") or ""),
            ),
            reverse=True,
        )
        selected: list[dict[str, Any]] = []
        seen_summaries: set[str] = set()
        for item in recalled:
            if not isinstance(item, dict):
                continue
            summary = str(item.get("summary") or "").strip()
            if not summary or _is_generic_memory_summary(summary):
                continue
            payload = dict(item.get("payload") or {})
            style_label = str(payload.get("style_label") or "").strip().lower()
            primary_archetype = str(payload.get("primary_archetype") or "").strip().lower()
            review_status = _normalize_review_status_value(
                item.get("review_status") or payload.get("review_status") or payload.get("status")
            )
            label_marker = primary_archetype or style_label
            if label_marker in {"balanced", "default", "generic", "neutral"}:
                continue
            if bool(payload.get("fallback_used")):
                continue
            if review_status == "runtime_failed":
                continue
            if (
                str(payload.get("strategy_quality") or "").strip().lower() in {"", "low", "insufficient_data"}
                and review_status not in _NON_GENERATING_REVIEW_STATUSES
            ):
                continue
            marker = summary.lower()
            if marker in seen_summaries:
                continue
            seen_summaries.add(marker)
            selected.append(item)
            if len(selected) >= limit:
                break
        return selected

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
        review_status = _normalize_review_status_value(dict(reflection_payload.get("review") or {}).get("status"))
        low_signal_memory = review_status in _NON_GENERATING_REVIEW_STATUSES - {"runtime_failed"}
        if (
            not summary
            or _is_generic_memory_summary(summary)
            or bool(reflection_payload.get("fallback_used"))
            or (
                str(build_payload.get("strategy_quality") or "").strip().lower() in {"", "low", "insufficient_data"}
                and not low_signal_memory
            )
        ):
            return
        self.derived_memory_store.remember(
            wallet=wallet,
            chain=chain,
            memory_type="wallet_style_distillation",
            summary=summary,
            payload={
                "style_label": profile.get("style_label"),
                "strategy_setup_label": strategy.get("setup_label"),
                "primary_archetype": profile.get("metadata", {}).get("primary_archetype"),
                "secondary_archetypes": list(profile.get("metadata", {}).get("secondary_archetypes") or []),
                "fallback_used": bool(reflection_payload.get("fallback_used")),
                "strategy_quality": build_payload.get("strategy_quality"),
                "example_readiness": build_payload.get("example_readiness"),
                "review_status": review_status,
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
            mock_response = _build_mock_minimal_reflection_response(
                wallet=wallet,
                chain=chain,
                preprocessed=preprocessed,
                prompt=prompt,
            )
        return ReflectionJobSpec(
            subject_kind="wallet_style_reflection",
            subject_id=wallet,
            flow_id="wallet_style_reflection_review",
            system_prompt=prompt,
            compact_input=_minimal_compact_payload(preprocessed),
            expected_output_schema=build_wallet_style_output_schema(),
            artifact_root=artifacts_dir,
            prompt=(
                f"Produce a minimal wallet-style distill for {wallet} on {chain}. "
                "Return wallet, chain, summary, review_status, reasoning, and wallet-specific archetype or behavior evidence when present. "
                "Optional fields such as dominant_actions, preferred_tokens, setup_label, setup_summary, active_windows, prompt_focus, and risk_flags are helpful but not required. "
                "Do not generate the final profile or execution intent; Python will assemble them."
            ),
            metadata={
                "schema_mode": "wallet_style_minimal_distill",
                "chain": chain,
                "wallet": wallet,
                "review_status_contract": [
                    "generate",
                    "generate_with_low_confidence",
                    "insufficient_signal",
                    "no_pattern_detected",
                    "needs_manual_review",
                    "runtime_failed",
                ],
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
        archetype = classify_archetype(trade_statistics, completed_trades, open_positions)
        archetype_payload = archetype.to_dict()
        archetype_metadata = _archetype_metadata_fields(archetype_payload)
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
            archetype=archetype_payload,
            market_contexts=[item.to_compact() for item in focus_market_contexts],
            macro_context=macro_context.to_compact(),
            entry_factors=[item.to_dict() for item in entry_factors],
            risk_filters=[item.to_dict() for item in risk_filters],
            fetch_metadata=fetch_metadata,
        )
        (
            profile,
            strategy,
            execution_intent,
            review,
            reflection_result,
            reflection_spec,
            _reflection_envelope,
            fallback_used,
            review_backend,
        ) = self._resolve_reflection_report(
            wallet=resolved_wallet,
            chain=resolved_chain,
            prompt=prompt,
            preprocessed=preprocessed,
            artifacts_dir=artifacts_dir,
        )

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
            **archetype_metadata,
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
            **archetype_metadata,
            **_execution_chain_defaults(resolved_chain),
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
            **archetype_metadata,
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
            **archetype_metadata,
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
            **archetype_metadata,
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
            "review_status": review_payload.get("status"),
            **archetype_metadata,
        }
        review_generation = _review_generation_decision(review_payload)
        review_payload["status"] = review_generation["status"]
        review_payload["should_generate_candidate"] = review_generation["should_generate_candidate"]
        review_payload["metadata"] = {
            **dict(review_payload.get("metadata") or {}),
            "review_status": review_generation["status"],
            "candidate_generation_skipped": review_generation["skip_generation"],
        }

        target_skill_name = str(skill_name or _default_wallet_style_display_name(resolved_wallet, requested_chain)).strip()
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
        _write_json(artifacts_dir / "trade_pairing.json", _serialize_trade_pairing(completed_trades, open_positions, trade_statistics, archetype=archetype))
        _write_json(artifacts_dir / "archetype.json", archetype_payload)
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
                {
                    "artifact_id": f"{job_id}-archetype",
                    "kind": "wallet.archetype.json",
                    "uri": str((artifacts_dir / "archetype.json").resolve()),
                    "label": "Derived wallet trading archetype",
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
                "suggested_action": (
                    "generate wallet style skill package"
                    if not review_generation["skip_generation"]
                    else None
                ),
                "disable_candidate_generation": review_generation["skip_generation"],
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
                    **archetype_metadata,
                },
                "change_summary": profile.summary,
                "review_backend": review_backend,
                "reflection_flow_id": reflection_flow_id,
                "reflection_run_id": reflection_result.reflection_run_id,
                "reflection_session_id": reflection_result.reflection_session_id,
                "reflection_status": reflection_status,
                "fallback_used": fallback_used,
                **archetype_metadata,
            },
        }

        pipeline_result = RunIngestionPipeline(self.registry_root).record(run_payload)
        lifecycle = dict(pipeline_result.lifecycle or {})
        candidate_payload = dict(lifecycle.get("candidate") or {})
        if review_generation["skip_generation"]:
            candidate_stub, package_stub, validation_stub, promotion_stub, smoke_result, example_artifacts = _skipped_candidate_artifacts(
                target_skill_name=target_skill_name,
                review_status=review_generation["status"],
            )
            compile_result = {"candidate": candidate_stub, "package": package_stub}
            validate_result = {"status": "skipped", "validation_report": validation_stub}
            promote_result = {"promotion": promotion_stub}
            adoption_ok = False
            execution_smoke = dict(smoke_result)
            data_completeness = _compute_data_completeness(
                focus_market_contexts=focus_market_contexts,
                entry_factors=entry_factors,
                risk_filters=risk_filters,
                backtest_result=backtest_result.to_dict(),
                execution_smoke=execution_smoke,
            )
            _write_json(artifacts_dir / "skill_smoke_output.json", smoke_result)
            _write_json(artifacts_dir / "execution_smoke_output.json", execution_smoke)
            strategy_qa_checks = [
                {
                    "check": "candidate_generation_policy",
                    "passed": True,
                    "detail": f"skipped:{review_generation['status']}",
                },
                {
                    "check": "strategy_spec_generated",
                    "passed": bool(strategy_payload.get("entry_conditions")),
                    "detail": strategy_payload.get("summary"),
                },
                {
                    "check": "backtest_scored",
                    "passed": backtest_result.confidence_score >= 0.05,
                    "detail": backtest_result.to_dict(),
                },
            ]
            execution_qa_checks = [
                {
                    "check": "execute_action_generated",
                    "passed": True,
                    "detail": "skipped_by_review_status",
                },
                {
                    "check": "execution_contract_prepared",
                    "passed": True,
                    "detail": "skipped_by_review_status",
                },
            ]
            execution_readiness = str(execution_smoke.get("execution_readiness") or "blocked_by_review_status")
            example_readiness = review_generation["status"]
            qa_status = "warn"
        else:
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
                        **archetype_metadata,
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
                    "check": "execution_contract_prepared",
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
                "archetype": str((artifacts_dir / "archetype.json").resolve()),
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
        result = self._run_script_process(script_path, payload)
        parsed = result.get("parsed_output")
        return {
            "ok": result["ok"],
            "returncode": result["returncode"],
            "stdout": result["stdout"],
            "stderr": result["stderr"],
            "parsed_output": parsed,
            "summary": parsed.get("summary") if isinstance(parsed, dict) else result["stdout"],
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
        target_skill_name = str(ledger.get("requested_skill_name") or _default_wallet_style_display_name(wallet, requested_chain)).strip()
        artifacts_dir = job_dir / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.ledger_store.on_stage_start(
            job_dir,
            stage="distill_features",
            summary=f"Fetch AVE features for {wallet} on {requested_chain}",
        )
        recalled_memory = self._select_derived_memories(wallet, requested_chain, limit=1)
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
            archetype = classify_archetype(trade_statistics, completed_trades, open_positions)
            archetype_payload = archetype.to_dict()
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
                archetype=archetype_payload,
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
                "trade_pairing": _serialize_trade_pairing(completed_trades, open_positions, trade_statistics, archetype=archetype),
                "trade_statistics": trade_statistics.to_dict(),
                "archetype": archetype_payload,
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
            _write_json(artifacts_dir / "archetype.json", archetype_payload)
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
            derived_memories = self._select_derived_memories(wallet, chain, limit=1)
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
            (
                profile,
                strategy,
                execution_intent,
                review,
                reflection_result,
                reflection_spec,
                envelope,
                fallback_used,
                review_backend,
            ) = self._resolve_reflection_report(
                wallet=wallet,
                chain=chain,
                prompt=prompt,
                preprocessed=preprocessed,
                artifacts_dir=artifacts_dir,
                derived_memories=derived_memories,
                review_hints=review_hints,
            )

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
                    "target_skill_name": str(
                        distill_payload.get("target_skill_name") or _default_wallet_style_display_name(wallet, chain)
                    ).strip(),
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
            archetype_payload = dict(distill_payload.get("archetype") or preprocessed.get("archetype") or {})
            archetype_metadata = _archetype_metadata_fields(archetype_payload)
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
                **archetype_metadata,
            }
            strategy_payload["metadata"] = strategy_metadata
            execution_intent_payload["metadata"] = {
                **dict(execution_intent_payload.get("metadata") or {}),
                "chain": chain,
                "entry_factors": entry_factors,
                "risk_filters": risk_filters,
                "market_context": preprocessed.get("market_context"),
                **archetype_metadata,
                **_execution_chain_defaults(chain),
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
                **archetype_metadata,
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
                **archetype_metadata,
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
                **archetype_metadata,
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
                "review_status": review_payload.get("status"),
                **archetype_metadata,
            }
            review_generation = _review_generation_decision(review_payload)
            review_payload["status"] = review_generation["status"]
            review_payload["should_generate_candidate"] = review_generation["should_generate_candidate"]
            review_payload["metadata"] = {
                **dict(review_payload.get("metadata") or {}),
                "review_status": review_generation["status"],
                "candidate_generation_skipped": review_generation["skip_generation"],
            }
            requested_skill_name = str(distill_payload.get("target_skill_name") or _default_wallet_style_display_name(wallet, chain)).strip()
            auto_requested_name = requested_skill_name == _default_wallet_style_display_name(wallet, chain)
            target_skill_name = (
                _default_wallet_style_display_name(wallet, chain, style_profile=profile_payload)
                if auto_requested_name
                else requested_skill_name
            )
            candidate_slug = (
                _default_wallet_style_slug(wallet, chain, style_profile=profile_payload)
                if auto_requested_name
                else re.sub(r"[^0-9a-zA-Z]+", "-", target_skill_name.strip().lower()).strip("-") or "candidate"
            )
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
                    {
                        "artifact_id": f"{job_id}-archetype",
                        "kind": "wallet.archetype.json",
                        "uri": str((artifacts_dir / "archetype.json").resolve()),
                        "label": "Derived wallet trading archetype",
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
                    "suggested_action": (
                        "generate wallet style skill package"
                        if not review_generation["skip_generation"]
                        else None
                    ),
                    "disable_candidate_generation": review_generation["skip_generation"],
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
                        **archetype_metadata,
                    },
                    "change_summary": profile_payload.get("summary"),
                    "review_backend": review_backend,
                    "reflection_flow_id": reflection_flow_id,
                    "reflection_run_id": reflection_run_id,
                    "reflection_session_id": reflection_session_id,
                    "reflection_status": reflection_status,
                    "fallback_used": fallback_used,
                    **archetype_metadata,
                },
            }
            pipeline_result = RunIngestionPipeline(self.registry_root).record(run_payload)
            lifecycle = dict(pipeline_result.lifecycle or {})
            candidate_payload = dict(lifecycle.get("candidate") or {})
            if review_generation["skip_generation"]:
                candidate_stub, package_stub, validation_stub, promotion_stub, smoke_result, example_artifacts = _skipped_candidate_artifacts(
                    target_skill_name=target_skill_name,
                    review_status=review_generation["status"],
                )
                compile_result = {"candidate": candidate_stub, "package": package_stub}
                validate_result = {"status": "skipped", "validation_report": validation_stub}
                promote_result = {"promotion": promotion_stub}
                adoption_ok = False
                execution_smoke = dict(smoke_result)
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
                strategy_qa_checks = [
                    {"check": "candidate_generation_policy", "passed": True, "detail": f"skipped:{review_generation['status']}"},
                    {"check": "strategy_spec_generated", "passed": bool(strategy_payload.get("entry_conditions")), "detail": strategy_payload.get("summary")},
                    {"check": "backtest_scored", "passed": backtest_result.confidence_score >= 0.05, "detail": backtest_result.to_dict()},
                ]
                execution_qa_checks = [
                    {"check": "execute_action_generated", "passed": True, "detail": "skipped_by_review_status"},
                    {"check": "execution_contract_prepared", "passed": True, "detail": "skipped_by_review_status"},
                ]
                execution_readiness = str(execution_smoke.get("execution_readiness") or "blocked_by_review_status")
                example_readiness = review_generation["status"]
                qa_status = "warn"
            else:
                if not candidate_payload:
                    raise RuntimeError("style distillation did not produce a candidate")
                candidate_payload.update(
                    {
                        "candidate_slug": candidate_slug,
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
                            **archetype_metadata,
                        },
                    }
                )
                compile_result = self.candidate_service.compile_candidate(candidate_payload, package_kind="script")
                validate_result = self.candidate_service.validate_candidate(candidate_payload["candidate_id"])
                validate_status = str(validate_result.get("status") or "")
                if validate_status not in {"validated", "passed"}:
                    report = dict(validate_result.get("validation_report") or {})
                    issues = [item for item in list(report.get("issues") or []) if isinstance(item, dict)]
                    issue_summary = "; ".join(
                        f"{item.get('code')}: {item.get('message')}" for item in issues[:3]
                    ) or "candidate validation failed"
                    raise RuntimeError(issue_summary)
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
                    {"check": "execution_contract_prepared", "passed": bool(execution_smoke.get("ok")), "detail": execution_smoke.get("summary") or execution_smoke.get("stderr")},
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
                    "archetype": str((artifacts_dir / "archetype.json").resolve()),
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
        promotion_root = str(build_payload.get("promotion", {}).get("package_root") or "").strip()
        promoted_root = Path(promotion_root).expanduser().resolve() if promotion_root else None
        primary_result = dict(build_payload.get("qa", {}).get("strategy_qa", {}).get("smoke_test") or {})
        execution_intent = dict(build_payload.get("execution_intent") or {})
        review_status = _normalize_review_status_value(dict(build_payload.get("review") or {}).get("status"))
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
            if live_execute and approval_granted and promoted_root is not None:
                live_result = self._execution_live_test(promoted_root, primary_result, execution_intent)
                _write_json(artifacts_dir / "example_execute_live.json", live_result)
            elif promoted_root is None and not dry_run_result:
                dry_run_result = {
                    "ok": False,
                    "skipped": True,
                    "summary": f"Execution skipped because review.status={review_status}.",
                    "execution_readiness": "blocked_by_review_status",
                    "parsed_output": {
                        "summary": f"Execution skipped because review.status={review_status}.",
                        "execution_readiness": "blocked_by_review_status",
                        "metadata": {
                            "review_status": review_status,
                            "skip_reason": "candidate_generation_disabled",
                        },
                    },
                }
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
        max_attempts: int = 3,
    ) -> dict[str, Any]:
        resolved_wallet = str(wallet or "").strip()
        if not resolved_wallet:
            raise ValueError("wallet is required")
        requested_chain = str(chain or "").strip() or "unknown"
        prompt = str(extractor_prompt or DEFAULT_EXTRACTION_PROMPT).strip() or DEFAULT_EXTRACTION_PROMPT
        target_skill_name = str(skill_name or _default_wallet_style_display_name(resolved_wallet, requested_chain)).strip()
        attempt_limit = min(3, max(1, int(max_attempts or 3)))
        attempt_records: list[dict[str, Any]] = []
        last_error: Exception | None = None
        for attempt in range(1, attempt_limit + 1):
            job_id, _job_dir = self._create_job(
                wallet=resolved_wallet,
                requested_chain=requested_chain,
                target_skill_name=target_skill_name,
                extractor_prompt=prompt,
            )
            try:
                result = self.resume_job(job_id, live_execute=live_execute, approval_granted=approval_granted)
            except ValueError:
                raise
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                attempt_records.append(self._attempt_failure_record(attempt=attempt, job_id=job_id, error=exc))
                if attempt < attempt_limit:
                    continue
                raise WalletStyleDistillationAttemptsExceeded(
                    self._write_attempt_failure_report(
                        wallet=resolved_wallet,
                        chain=requested_chain,
                        max_attempts=attempt_limit,
                        attempts=attempt_records,
                    )
                ) from exc
            attempt_records.append(self._attempt_success_record(attempt=attempt, result=result))
            result["attempt_report"] = {
                "wallet": resolved_wallet,
                "chain": requested_chain,
                "max_attempts": attempt_limit,
                "attempt_count": attempt,
                "attempts": _json_safe(attempt_records),
            }
            return result
        if last_error is not None:
            raise last_error
        raise RuntimeError("wallet style distillation did not start")

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
        result = self._run_script_process(script_path, context)
        parsed = result.get("parsed_output")
        return {
            "ok": result["ok"],
            "returncode": result["returncode"],
            "stdout": result["stdout"],
            "stderr": result["stderr"],
            "parsed_output": parsed,
            "summary": parsed.get("summary") if isinstance(parsed, dict) else result["stdout"],
        }

    def _run_primary_context(self, promoted_root: Path, context: dict[str, Any]) -> dict[str, Any]:
        script_path = promoted_root / "scripts" / "primary.py"
        result = self._run_script_process(script_path, context)
        parsed = result.get("parsed_output")
        return {
            "ok": result["ok"],
            "returncode": result["returncode"],
            "stdout": result["stdout"],
            "stderr": result["stderr"],
            "parsed_output": parsed,
            "summary": parsed.get("summary") if isinstance(parsed, dict) else result["stdout"],
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

    def _run_execution_smoke_payload(
        self,
        script_path: Path,
        trade_plan: dict[str, Any],
        execution_intent: dict[str, Any],
    ) -> dict[str, Any]:
        payload = {
            "trade_plan": dict(trade_plan or {}),
            "execution_intent": execution_intent,
            "mode": (
                "dry_run"
                if all(str(os.environ.get(key) or "").strip() for key in ("OKX_API_KEY", "OKX_SECRET_KEY", "OKX_PASSPHRASE"))
                else "prepare_only"
            ),
            "approval_granted": False,
        }
        result = self._run_script_process(script_path, payload)
        parsed = result.get("parsed_output")
        return {
            "ok": result["ok"],
            "returncode": result["returncode"],
            "stdout": result["stdout"],
            "stderr": result["stderr"],
            "parsed_output": parsed,
            "summary": parsed.get("summary") if isinstance(parsed, dict) else result["stdout"],
            "execution_readiness": parsed.get("execution_readiness") if isinstance(parsed, dict) else "blocked_by_risk",
        }

    def _should_retry_execution_smoke_target(self, execution_result: dict[str, Any]) -> bool:
        parsed = dict(execution_result.get("parsed_output") or {})
        metadata = dict(parsed.get("metadata") or {})
        readiness_reason = str(metadata.get("readiness_reason") or "").strip().lower()
        readiness_detail = str(metadata.get("readiness_detail") or "").strip().lower()
        if readiness_reason == "missing_target_token_address":
            return True
        return "target_token_address" in readiness_detail or "no_market_candidate" in readiness_detail

    def _execution_smoke_fallback_targets(self, primary_output: dict[str, Any]) -> list[str]:
        trade_plan = dict(primary_output.get("trade_plan") or {})
        input_context = dict(primary_output.get("input_context") or {})
        current_target = str(_safe_text(trade_plan.get("target_token")) or "").upper()
        excluded = {
            current_target,
            str(_safe_text(trade_plan.get("execution_source_symbol")) or "").upper(),
        }
        candidates: list[str] = []
        seen: set[str] = set(excluded)
        for source in (
            trade_plan.get("candidate_tokens") or [],
            trade_plan.get("historical_tokens") or [],
            input_context.get("candidate_tokens") or [],
            input_context.get("preferred_tokens") or [],
        ):
            for raw in list(source or []):
                token = _safe_text(raw)
                if token is None:
                    continue
                upper = token.upper()
                if upper in seen:
                    continue
                seen.add(upper)
                candidates.append(token)
                if len(candidates) >= 2:
                    return candidates
        return candidates

    def _execution_smoke_attempt_record(
        self,
        *,
        target_token: str | None,
        execution_result: dict[str, Any],
        fallback_used: bool,
    ) -> dict[str, Any]:
        parsed = dict(execution_result.get("parsed_output") or {})
        metadata = dict(parsed.get("metadata") or {})
        return {
            "target_token": _safe_text(target_token),
            "fallback_used": fallback_used,
            "ok": bool(execution_result.get("ok")),
            "execution_readiness": execution_result.get("execution_readiness"),
            "readiness_reason": metadata.get("readiness_reason"),
            "summary": execution_result.get("summary") or execution_result.get("stderr"),
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
        first_result = self._run_execution_smoke_payload(script_path, trade_plan, execution_intent)
        attempts = [
            self._execution_smoke_attempt_record(
                target_token=trade_plan.get("target_token"),
                execution_result=first_result,
                fallback_used=False,
            )
        ]
        final_result = dict(first_result)
        if isinstance(primary_output, dict) and self._should_retry_execution_smoke_target(first_result):
            for fallback_target in self._execution_smoke_fallback_targets(primary_output):
                fallback_context = dict(primary_output.get("input_context") or {})
                fallback_context["target_token"] = fallback_target
                fallback_primary = self._run_primary_context(promoted_root, fallback_context)
                fallback_output = fallback_primary.get("parsed_output") if isinstance(fallback_primary, dict) else {}
                fallback_trade_plan = dict(fallback_output.get("trade_plan") or {})
                if not fallback_trade_plan:
                    continue
                fallback_result = self._run_execution_smoke_payload(script_path, fallback_trade_plan, execution_intent)
                attempts.append(
                    self._execution_smoke_attempt_record(
                        target_token=fallback_trade_plan.get("target_token"),
                        execution_result=fallback_result,
                        fallback_used=True,
                    )
                )
                final_result = dict(fallback_result)
                if fallback_result.get("execution_readiness") in {"dry_run_ready", "live_ready"} and fallback_result.get("ok"):
                    break
        parsed = dict(final_result.get("parsed_output") or {})
        metadata = dict(parsed.get("metadata") or {})
        metadata["smoke_attempt_count"] = len(attempts)
        metadata["smoke_attempts"] = attempts
        metadata["smoke_fallback_used"] = len(attempts) > 1
        if attempts:
            metadata["smoke_initial_target"] = attempts[0].get("target_token")
            metadata["smoke_effective_target"] = attempts[-1].get("target_token")
        if metadata["smoke_fallback_used"]:
            initial_target = attempts[0].get("target_token") or "default target"
            effective_target = attempts[-1].get("target_token") or "fallback target"
            if final_result.get("ok") and final_result.get("execution_readiness") in {"dry_run_ready", "live_ready"}:
                final_result["summary"] = (
                    f"execution smoke fallback succeeded: {initial_target} unresolved, switched to {effective_target}"
                )
            else:
                final_result["summary"] = (
                    f"execution smoke failed after {len(attempts)} target attempts; started from {initial_target}"
                )
        parsed["metadata"] = metadata
        final_result["parsed_output"] = parsed
        return final_result


def build_wallet_style_distillation_service(
    *,
    project_root: Path | None = None,
    workspace_root: Path | None = None,
    provider: Any | None = None,
    reflection_service: PiReflectionService | None = None,
    adapter_registry: Any | None = None,
    data_source_adapter_id: str | None = None,
    require_explicit_data_source_adapter: bool = False,
    allow_builtin_adapter_registry_fallback: bool = True,
) -> WalletStyleDistillationService:
    return WalletStyleDistillationService(
        project_root=project_root,
        workspace_root=workspace_root,
        provider=provider,
        reflection_service=reflection_service,
        adapter_registry=adapter_registry,
        data_source_adapter_id=data_source_adapter_id,
        require_explicit_data_source_adapter=require_explicit_data_source_adapter,
        allow_builtin_adapter_registry_fallback=allow_builtin_adapter_registry_fallback,
    )
