from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable


_CHAIN_INDEX = {
    "ethereum": "1",
    "eth": "1",
    "bsc": "56",
    "base": "8453",
    "polygon": "137",
    "arbitrum": "42161",
    "optimism": "10",
    "solana": "501",
}
_NATIVE_TOKEN_ADDRESS = {
    "1": "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
    "10": "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
    "56": "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
    "137": "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
    "8453": "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
    "42161": "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
}
_STABLE_SYMBOLS = {"USDT", "USDC", "DAI", "FDUSD", "TUSD"}
_PRICE_HINTS_USD = {
    "bsc": {
        "USDT": 1.0,
        "USDC": 1.0,
        "WBNB": 600.0,
        "BNB": 600.0,
    }
}


def _project_root(project_root: Path | None) -> Path:
    if project_root is not None:
        return Path(project_root).expanduser().resolve()
    return Path(__file__).resolve().parents[3]


def _cli_manifest(project_root: Path | None = None) -> Path:
    root = _project_root(project_root)
    return root / "vendor" / "onchainos_cli" / "upstream" / "cli" / "Cargo.toml"


def _cli_binary_candidates(project_root: Path | None = None) -> list[str]:
    root = _project_root(project_root)
    return [
        str(root / ".ot-workspace" / "onchainos" / "bin" / "onchainos"),
        str(root / "vendor" / "onchainos_cli" / "upstream" / "cli" / "target" / "release" / "onchainos"),
        str(root / "vendor" / "onchainos_cli" / "upstream" / "cli" / "target" / "debug" / "onchainos"),
    ]


def _resolve_cli_invocation(project_root: Path | None = None) -> tuple[list[str], dict[str, Any]]:
    explicit_bin = str(os.environ.get("OT_ONCHAINOS_CLI_BIN") or "").strip()
    if explicit_bin:
        explicit_path = Path(explicit_bin).expanduser()
        if explicit_path.is_file() and os.access(explicit_path, os.X_OK):
            return [str(explicit_path)], {"resolved": True, "source": "OT_ONCHAINOS_CLI_BIN", "path": str(explicit_path)}
        return [explicit_bin], {"resolved": False, "source": "OT_ONCHAINOS_CLI_BIN", "path": explicit_bin}
    for candidate in _cli_binary_candidates(project_root):
        if Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return [candidate], {"resolved": True, "source": "vendored_binary", "path": candidate}
    cargo = shutil.which("cargo")
    if cargo:
        return (
            [cargo, "run", "--quiet", "--manifest-path", str(_cli_manifest(project_root)), "--"],
            {"resolved": True, "source": "cargo", "path": cargo},
        )
    return (
        ["cargo", "run", "--quiet", "--manifest-path", str(_cli_manifest(project_root)), "--"],
        {
            "resolved": False,
            "source": "missing",
            "path": "",
            "searched_binaries": _cli_binary_candidates(project_root),
        },
    )


def _cli_command(project_root: Path | None = None) -> list[str]:
    command, _ = _resolve_cli_invocation(project_root)
    return command


def _chain_index(chain: str) -> str:
    return _CHAIN_INDEX.get(str(chain or "").strip().lower(), str(chain or "").strip())


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 1) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = _safe_text(value).lower()
    return text in {"1", "true", "yes", "y", "on"}


def _is_evm_address(value: Any) -> bool:
    text = _safe_text(value)
    return len(text) == 42 and text.startswith("0x") and all(char in "0123456789abcdefABCDEF" for char in text[2:])


def _onchainos_home(project_root: Path | None = None) -> str:
    explicit = _safe_text(os.environ.get("ONCHAINOS_HOME"))
    if explicit:
        return explicit
    root = _project_root(project_root)
    return str((root / ".ot-workspace" / "onchainos").resolve())


def _execution_env(project_root: Path | None = None, env: dict[str, str] | None = None) -> tuple[dict[str, str], list[str]]:
    merged = dict(os.environ)
    if env:
        merged.update(env)
    merged.setdefault("ONCHAINOS_HOME", _onchainos_home(project_root))
    missing = [
        key
        for key in ("OKX_API_KEY", "OKX_SECRET_KEY", "OKX_PASSPHRASE")
        if not _safe_text(merged.get(key))
    ]
    return merged, missing


def _resolved_price_hint_usd(chain: str, symbol: str, explicit_price: Any) -> float:
    parsed = _safe_float(explicit_price, default=0.0)
    if parsed > 0:
        return parsed
    return _safe_float(_PRICE_HINTS_USD.get(chain, {}).get(symbol.upper()), default=0.0)


def _unwrap_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        if isinstance(value.get("data"), list) and value["data"]:
            first = value["data"][0]
            if isinstance(first, dict):
                return first
        if isinstance(value.get("data"), dict):
            return dict(value["data"])
        return value
    return {}


def _build_gateway_simulate_command(prepared: dict[str, Any], swap_payload: dict[str, Any], *, project_root: Path | None = None) -> list[str] | None:
    tx = dict(_unwrap_payload(swap_payload).get("tx") or {})
    to_address = _safe_text(tx.get("to"))
    input_data = _safe_text(tx.get("data"))
    amount = _safe_text(tx.get("value")) or "0"
    if not to_address or not input_data:
        return None
    cli_prefix = _cli_command(project_root)
    return cli_prefix + [
        "gateway",
        "simulate",
        "--from",
        _safe_text(prepared.get("wallet_address")),
        "--to",
        to_address,
        "--amount",
        amount,
        "--data",
        input_data,
        "--chain",
        _safe_text(prepared.get("chain")),
    ]


def _build_check_approvals_command(prepared: dict[str, Any], *, project_root: Path | None = None) -> list[str]:
    cli_prefix = _cli_command(project_root)
    return cli_prefix + [
        "swap",
        "check-approvals",
        "--chain",
        _safe_text(prepared.get("chain")),
        "--address",
        _safe_text(prepared.get("wallet_address")),
        "--token",
        _safe_text(prepared.get("execution_source_address")),
    ]


def _build_approve_command(prepared: dict[str, Any], raw_amount: str, *, project_root: Path | None = None) -> list[str]:
    cli_prefix = _cli_command(project_root)
    return cli_prefix + [
        "swap",
        "approve",
        "--token",
        _safe_text(prepared.get("execution_source_address")),
        "--amount",
        raw_amount,
        "--chain",
        _safe_text(prepared.get("chain")),
    ]


def _build_approve_simulate_command(prepared: dict[str, Any], approve_payload: dict[str, Any], *, project_root: Path | None = None) -> list[str] | None:
    to_address = _safe_text(prepared.get("execution_source_address"))
    input_data = _safe_text(approve_payload.get("data"))
    if not to_address or not input_data:
        return None
    cli_prefix = _cli_command(project_root)
    return cli_prefix + [
        "gateway",
        "simulate",
        "--from",
        _safe_text(prepared.get("wallet_address")),
        "--to",
        to_address,
        "--amount",
        "0",
        "--data",
        input_data,
        "--chain",
        _safe_text(prepared.get("chain")),
    ]


def _simulation_failed(result: dict[str, Any]) -> tuple[bool, str]:
    parsed = result.get("parsed_output")
    payload = parsed if isinstance(parsed, dict) else {}
    data = payload.get("data")
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            fail_reason = _safe_text(item.get("failReason"))
            if fail_reason:
                return True, fail_reason
            risks = item.get("risks")
            if isinstance(risks, list) and risks:
                return True, json.dumps(risks, ensure_ascii=False)
    if isinstance(data, dict):
        fail_reason = _safe_text(data.get("failReason"))
        if fail_reason:
            return True, fail_reason
    if not data and isinstance(payload, dict):
        fail_reason = _safe_text(payload.get("failReason"))
        if fail_reason:
            return True, fail_reason
    return False, ""


def _is_approval_prereq_failure(reason: str) -> bool:
    normalized = _safe_text(reason).lower()
    return any(
        marker in normalized
        for marker in (
            "safeerc20",
            "allowance",
            "approve",
            "transfer amount exceeds allowance",
            "insufficient allowance",
        )
    )


def _is_native_source(prepared: dict[str, Any]) -> bool:
    chain_index = _chain_index(_safe_text(prepared.get("chain")))
    native = _safe_text(_NATIVE_TOKEN_ADDRESS.get(chain_index))
    source_address = _safe_text(prepared.get("execution_source_address"))
    return bool(native) and source_address.lower() == native.lower()


def _extract_quote_raw_amount(quote_result: dict[str, Any]) -> str:
    payload = _unwrap_payload(quote_result.get("parsed_output"))
    router_result = payload.get("routerResult") if isinstance(payload, dict) else None
    if isinstance(router_result, dict):
        raw = _safe_text(router_result.get("fromTokenAmount"))
        if raw:
            return raw
    return ""


def _extract_spendable(check_result: dict[str, Any]) -> str:
    payload = _unwrap_payload(check_result.get("parsed_output"))
    tokens = payload.get("tokens") if isinstance(payload, dict) else None
    if isinstance(tokens, list) and tokens:
        first = tokens[0]
        if isinstance(first, dict):
            return _safe_text(first.get("spendable"))
    return ""


def _is_allowance_insufficient(spendable: str, amount: str) -> bool:
    if not spendable:
        return True
    if len(spendable) > 38:
        return False
    try:
        spendable_value = int(spendable)
    except ValueError:
        spendable_value = 0
    try:
        amount_value = int(amount)
    except ValueError:
        return True
    return spendable_value < amount_value


def prepare_execution(
    trade_plan: dict[str, Any],
    execution_intent: dict[str, Any],
    *,
    project_root: Path | None = None,
) -> dict[str, Any]:
    chain = _safe_text(trade_plan.get("chain") or execution_intent.get("metadata", {}).get("chain"))
    target_token = _safe_text(trade_plan.get("target_token"))
    target_token_address = _safe_text(trade_plan.get("target_token_address"))
    source_symbol = _safe_text(trade_plan.get("execution_source_symbol"))
    source_address = _safe_text(trade_plan.get("execution_source_address"))
    preferred_workflow = _safe_text(execution_intent.get("preferred_workflow")) or "swap_execute"
    leg_count = max(1, _safe_int(execution_intent.get("leg_count") or trade_plan.get("leg_count"), default=1))
    per_leg_usd = round(_safe_float(trade_plan.get("per_leg_usd"), default=_safe_float(trade_plan.get("desired_notional_usd"), default=0.0)), 4)
    source_readable_amount = _safe_float(trade_plan.get("execution_source_readable_amount"), default=0.0)
    source_price_hint_usd = _resolved_price_hint_usd(chain, source_symbol, trade_plan.get("execution_source_unit_price_usd"))
    if source_readable_amount > 0:
        readable_amount = round(source_readable_amount, 8)
    elif source_symbol.upper() in _STABLE_SYMBOLS:
        readable_amount = round(per_leg_usd, 4)
    elif source_price_hint_usd > 0:
        readable_amount = round(per_leg_usd / source_price_hint_usd, 8)
    else:
        readable_amount = None
    if not target_token_address or not _is_evm_address(target_token_address):
        raise ValueError("trade_plan.target_token_address must be a valid EVM address")
    if not source_address or not _is_evm_address(source_address):
        raise ValueError("trade_plan.execution_source_address must be a valid EVM address")
    if not chain:
        raise ValueError("trade_plan.chain is required")
    if readable_amount is None or readable_amount <= 0:
        raise ValueError("execution source must resolve to a readable amount")

    token_scan_arg = f"{_chain_index(chain)}:{target_token_address}"
    cli_prefix, cli_resolution = _resolve_cli_invocation(project_root)
    command_groups = {
        "wallet_login": cli_prefix + ["wallet", "login", "--force"],
        "wallet_status": cli_prefix + ["wallet", "status"],
        "security_token_scan": cli_prefix + ["security", "token-scan", "--tokens", token_scan_arg],
        "swap_swap": cli_prefix
        + [
            "swap",
            "swap",
            "--from",
            source_address,
            "--to",
            target_token_address,
            "--readable-amount",
            f"{readable_amount:.4f}",
            "--chain",
            chain,
            "--wallet",
            _safe_text(trade_plan.get("wallet_address")),
        ],
        "swap_execute": cli_prefix
        + [
            "swap",
            "execute",
            "--from",
            source_address,
            "--to",
            target_token_address,
            "--readable-amount",
            f"{readable_amount:.4f}",
            "--chain",
            chain,
            "--wallet",
            _safe_text(trade_plan.get("wallet_address")),
        ],
    }
    return {
        "adapter": "onchainos_cli",
        "chain": chain,
        "wallet_address": _safe_text(trade_plan.get("wallet_address")),
        "target_token": target_token,
        "target_token_address": target_token_address,
        "execution_source_symbol": source_symbol,
        "execution_source_address": source_address,
        "leg_count": leg_count,
        "per_leg_usd": per_leg_usd,
        "execution_source_unit_price_usd": source_price_hint_usd or None,
        "execution_source_readable_amount": readable_amount,
        "preferred_workflow": preferred_workflow,
        "preflight_checks": list(execution_intent.get("preflight_checks") or ()),
        "route_preferences": list(execution_intent.get("route_preferences") or ()),
        "requires_explicit_approval": bool(execution_intent.get("requires_explicit_approval", True)),
        "command_groups": command_groups,
        "cli_manifest": str(_cli_manifest(project_root)),
        "cli_resolution": cli_resolution,
    }


def _run_command(
    command: list[str],
    *,
    env: dict[str, str] | None = None,
    executor: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    completed = executor(
        command,
        text=True,
        capture_output=True,
        check=False,
        env=env,
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
        "command": command,
        "stdout": stdout,
        "stderr": stderr,
        "parsed_output": parsed,
    }


def run_dry_run(
    trade_plan: dict[str, Any],
    execution_intent: dict[str, Any],
    *,
    project_root: Path | None = None,
    env: dict[str, str] | None = None,
    executor: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    prepared = prepare_execution(trade_plan, execution_intent, project_root=project_root)
    runtime_env, missing = _execution_env(project_root, env)
    cli_resolution = dict(prepared.get("cli_resolution") or {})
    if not bool(cli_resolution.get("resolved")):
        return collect_execution_result(
            prepared,
            mode="dry_run",
            checks=[],
            execution={},
            readiness="blocked_by_config",
            metadata={"cli_resolution": cli_resolution},
        )
    if missing:
        return collect_execution_result(
            prepared,
            mode="dry_run",
            checks=[],
            execution={},
            readiness="blocked_by_config",
            metadata={"missing_env": missing},
        )
    checks: list[dict[str, Any]] = []
    login = _run_command(prepared["command_groups"]["wallet_login"], env=runtime_env, executor=executor)
    checks.append(login)
    if not login.get("ok"):
        return collect_execution_result(
            prepared,
            mode="dry_run",
            checks=checks,
            execution={},
            readiness="blocked_by_config",
        )
    status = _run_command(prepared["command_groups"]["wallet_status"], env=runtime_env, executor=executor)
    checks.append(status)
    status_payload = _unwrap_payload(status.get("parsed_output"))
    if not status.get("ok") or not bool(status_payload.get("loggedIn")):
        return collect_execution_result(
            prepared,
            mode="dry_run",
            checks=checks,
            execution={},
            readiness="blocked_by_config",
            metadata={"wallet_status": status_payload},
        )
    for check in prepared["preflight_checks"]:
        if check == "security_token_scan":
            checks.append(_run_command(prepared["command_groups"]["security_token_scan"], env=runtime_env, executor=executor))
    quote = _run_command(prepared["command_groups"]["swap_swap"], env=runtime_env, executor=executor)
    checks.append(quote)
    if not quote.get("ok"):
        return collect_execution_result(
            prepared,
            mode="dry_run",
            checks=checks,
            execution=quote,
            readiness="blocked_by_risk",
        )
    raw_amount = _extract_quote_raw_amount(quote)
    if not raw_amount:
        return collect_execution_result(
            prepared,
            mode="dry_run",
            checks=checks,
            execution=quote,
            readiness="blocked_by_risk",
            metadata={"quote_error": "missing routerResult.fromTokenAmount"},
        )
    if not _is_native_source(prepared):
        approvals = _run_command(_build_check_approvals_command(prepared, project_root=project_root), env=runtime_env, executor=executor)
        checks.append(approvals)
        if not approvals.get("ok"):
            return collect_execution_result(
                prepared,
                mode="dry_run",
                checks=checks,
                execution=approvals,
                readiness="blocked_by_risk",
            )
        spendable = _extract_spendable(approvals)
        if _is_allowance_insufficient(spendable, raw_amount):
            approve = _run_command(_build_approve_command(prepared, raw_amount, project_root=project_root), env=runtime_env, executor=executor)
            checks.append(approve)
            if not approve.get("ok"):
                return collect_execution_result(
                    prepared,
                    mode="dry_run",
                    checks=checks,
                    execution=approve,
                    readiness="blocked_by_risk",
                )
            approve_payload = _unwrap_payload(approve.get("parsed_output"))
            approve_simulate_command = _build_approve_simulate_command(prepared, approve_payload, project_root=project_root)
            if approve_simulate_command is None:
                return collect_execution_result(
                    prepared,
                    mode="dry_run",
                    checks=checks,
                    execution=approve,
                    readiness="blocked_by_risk",
                    metadata={"simulate_error": "approve payload missing calldata"},
                )
            approve_execution = _run_command(approve_simulate_command, env=runtime_env, executor=executor)
            simulation_failed, fail_reason = _simulation_failed(approve_execution)
            metadata = {
                "approval_required": True,
                "approval_spendable": spendable or "0",
                "approval_required_amount": raw_amount,
                "swap_simulation_skipped": True,
            }
            if simulation_failed or not approve_execution.get("ok"):
                if fail_reason:
                    metadata["simulate_fail_reason"] = fail_reason
                return collect_execution_result(
                    prepared,
                    mode="dry_run",
                    checks=checks,
                    execution=approve_execution,
                    readiness="blocked_by_risk",
                    metadata=metadata,
                )
            return collect_execution_result(
                prepared,
                mode="dry_run",
                checks=checks,
                execution=approve_execution,
                readiness="dry_run_ready",
                metadata=metadata,
            )
    simulate_command = _build_gateway_simulate_command(prepared, quote.get("parsed_output"), project_root=project_root)
    if simulate_command is None:
        return collect_execution_result(
            prepared,
            mode="dry_run",
            checks=checks,
            execution=quote,
            readiness="blocked_by_risk",
            metadata={"simulate_error": "swap payload missing tx.to or tx.data"},
        )
    execution = _run_command(simulate_command, env=runtime_env, executor=executor)
    simulation_failed, fail_reason = _simulation_failed(execution)
    metadata: dict[str, Any] | None = None
    readiness = "dry_run_ready" if execution.get("ok") and not simulation_failed else "blocked_by_risk"
    if simulation_failed:
        metadata = {"simulate_fail_reason": fail_reason}
    return collect_execution_result(
        prepared,
        mode="dry_run",
        checks=checks,
        execution=execution,
        readiness=readiness,
        metadata=metadata,
    )


def run_live(
    trade_plan: dict[str, Any],
    execution_intent: dict[str, Any],
    *,
    project_root: Path | None = None,
    env: dict[str, str] | None = None,
    executor: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    prepared = prepare_execution(trade_plan, execution_intent, project_root=project_root)
    runtime_env, missing = _execution_env(project_root, env)
    cli_resolution = dict(prepared.get("cli_resolution") or {})
    if prepared["requires_explicit_approval"]:
        raise ValueError("live execution requires explicit approval")
    if not bool(cli_resolution.get("resolved")):
        return collect_execution_result(
            prepared,
            mode="live",
            checks=[],
            execution={},
            readiness="blocked_by_config",
            metadata={"cli_resolution": cli_resolution},
        )
    if missing:
        return collect_execution_result(
            prepared,
            mode="live",
            checks=[],
            execution={},
            readiness="blocked_by_config",
            metadata={"missing_env": missing},
        )
    checks = [
        _run_command(prepared["command_groups"]["wallet_login"], env=runtime_env, executor=executor),
        _run_command(prepared["command_groups"]["wallet_status"], env=runtime_env, executor=executor),
    ]
    status_payload = _unwrap_payload(checks[-1].get("parsed_output"))
    if not all(item.get("ok") for item in checks) or not bool(status_payload.get("loggedIn")):
        return collect_execution_result(
            prepared,
            mode="live",
            checks=checks,
            execution={},
            readiness="blocked_by_config",
            metadata={"wallet_status": status_payload},
        )
    if "security_token_scan" in prepared["preflight_checks"]:
        checks.append(_run_command(prepared["command_groups"]["security_token_scan"], env=runtime_env, executor=executor))
    if any(not item.get("ok") for item in checks):
        return collect_execution_result(
            prepared,
            mode="live",
            checks=checks,
            execution={},
            readiness="blocked_by_risk",
        )
    execution = _run_command(prepared["command_groups"]["swap_execute"], env=runtime_env, executor=executor)
    simulation_failed, fail_reason = _simulation_failed(execution)
    return collect_execution_result(
        prepared,
        mode="live",
        checks=checks,
        execution=execution,
        readiness="live_ready" if execution.get("ok") and not simulation_failed else "blocked_by_risk",
        metadata={"simulate_fail_reason": fail_reason} if simulation_failed else None,
    )


def collect_execution_result(
    prepared_execution: dict[str, Any],
    *,
    mode: str,
    checks: list[dict[str, Any]] | None = None,
    execution: dict[str, Any] | None = None,
    readiness: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    check_results = list(checks or [])
    execution_result = dict(execution or {})
    blocked = any(not item.get("ok") for item in check_results)
    resolved_readiness = readiness or ("blocked_by_risk" if blocked else "dry_run_ready" if mode == "dry_run" else "live_ready")
    return {
        "ok": resolved_readiness in {"dry_run_ready", "live_ready"} and not blocked and (execution_result.get("ok") if execution_result else True),
        "mode": mode,
        "execution_readiness": resolved_readiness,
        "prepared_execution": prepared_execution,
        "checks": check_results,
        "execution": execution_result,
        "metadata": dict(metadata or {}),
    }
