from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
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
_WALLET_CHAIN_NAMES = {
    "bsc": {"bsc", "bnb"},
    "ethereum": {"ethereum", "eth"},
    "base": {"base", "base_eth"},
    "polygon": {"polygon", "matic"},
    "arbitrum": {"arbitrum", "arb_eth"},
    "optimism": {"optimism", "op_eth"},
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
_DEFAULT_LIVE_CAP_USD = 10.0
_MIN_EXECUTION_LEG_USD = 5.0


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


def _configured_live_cap_usd() -> float:
    return max(0.0, _safe_float(os.environ.get("OT_ONCHAINOS_LIVE_CAP_USD"), default=_DEFAULT_LIVE_CAP_USD))


def _configured_min_leg_usd() -> float:
    return max(0.0, _safe_float(os.environ.get("OT_ONCHAINOS_MIN_LEG_USD"), default=_MIN_EXECUTION_LEG_USD))


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


def _build_wallet_addresses_command(*, project_root: Path | None = None) -> list[str]:
    cli_prefix = _cli_command(project_root)
    return cli_prefix + ["wallet", "addresses"]


def _build_wallet_balance_command(prepared: dict[str, Any], *, project_root: Path | None = None) -> list[str]:
    cli_prefix = _cli_command(project_root)
    return cli_prefix + ["wallet", "balance", "--chain", _safe_text(prepared.get("chain"))]


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


def _build_wallet_contract_call_command(
    prepared: dict[str, Any],
    approve_payload: dict[str, Any],
    *,
    project_root: Path | None = None,
) -> list[str] | None:
    to_address = _safe_text(prepared.get("execution_source_address"))
    input_data = _safe_text(approve_payload.get("data"))
    gas_limit = _safe_text(approve_payload.get("gasLimit"))
    if not to_address or not input_data:
        return None
    cli_prefix = _cli_command(project_root)
    command = cli_prefix + [
        "wallet",
        "contract-call",
        "--to",
        to_address,
        "--chain",
        _safe_text(prepared.get("chain")),
        "--amt",
        "0",
        "--input-data",
        input_data,
        "--from",
        _safe_text(prepared.get("wallet_address")),
        "--force",
    ]
    if gas_limit:
        command.extend(["--gas-limit", gas_limit])
    return command


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


def _wallet_command_address(prepared: dict[str, Any]) -> str:
    return _safe_text(prepared.get("execution_wallet_address")) or _safe_text(prepared.get("wallet_address"))


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


def _extract_execution_wallet_address(addresses_result: dict[str, Any], chain: str) -> str:
    parsed = addresses_result.get("parsed_output")
    payload = parsed if isinstance(parsed, dict) else {}
    data = payload.get("data")
    chain_index = _chain_index(chain)
    accepted_names = _WALLET_CHAIN_NAMES.get(_safe_text(chain).lower(), {_safe_text(chain).lower()})
    if isinstance(data, dict):
        for value in data.values():
            if not isinstance(value, list):
                continue
            for item in value:
                if not isinstance(item, dict):
                    continue
                item_index = _safe_text(item.get("chainIndex"))
                item_name = _safe_text(item.get("chainName")).lower()
                address = _safe_text(item.get("address"))
                if not _is_evm_address(address):
                    continue
                if item_index == chain_index or item_name in accepted_names:
                    return address
    return ""


def _extract_wallet_total_usd(balance_result: dict[str, Any]) -> float:
    parsed = balance_result.get("parsed_output")
    payload = parsed if isinstance(parsed, dict) else {}
    data = payload.get("data")
    if isinstance(data, dict):
        total = _safe_float(data.get("totalValueUsd"), default=0.0)
        if total > 0:
            return total
    return 0.0


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


def _desired_notional_usd(trade_plan: dict[str, Any], requested_leg_count: int) -> float:
    desired = _safe_float(trade_plan.get("desired_notional_usd"), default=0.0)
    if desired > 0:
        return desired
    per_leg = _safe_float(trade_plan.get("per_leg_usd"), default=0.0)
    if per_leg > 0:
        return per_leg * max(1, requested_leg_count)
    readable_amount = _safe_float(trade_plan.get("execution_source_readable_amount"), default=0.0)
    source_price = _safe_float(trade_plan.get("execution_source_unit_price_usd"), default=0.0)
    if readable_amount > 0 and source_price > 0:
        return readable_amount * source_price * max(1, requested_leg_count)
    return 0.0


def _compress_leg_plan(requested_total: float, requested_leg_count: int, live_cap_usd: float) -> tuple[float, int, float, bool]:
    capped_total = min(requested_total, live_cap_usd) if live_cap_usd > 0 else requested_total
    capped_total = round(max(0.0, capped_total), 4)
    leg_count = max(1, requested_leg_count)
    while leg_count > 1 and capped_total / leg_count < _MIN_EXECUTION_LEG_USD:
        leg_count -= 1
    per_leg = round(capped_total / leg_count, 4) if capped_total > 0 else 0.0
    return capped_total, leg_count, per_leg, per_leg < _MIN_EXECUTION_LEG_USD


def _extract_tx_hashes(result: dict[str, Any]) -> list[str]:
    payload = _unwrap_payload(result.get("parsed_output"))
    values: list[str] = []
    for key in ("approveTxHash", "swapTxHash", "txHash"):
        text = _safe_text(payload.get(key))
        if text and text not in values:
            values.append(text)
    data = payload.get("data")
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            for key in ("approveTxHash", "swapTxHash", "txHash"):
                text = _safe_text(item.get(key))
                if text and text not in values:
                    values.append(text)
    return values


def _build_command_groups(prepared: dict[str, Any], *, project_root: Path | None = None) -> dict[str, list[str]]:
    cli_prefix, _ = _resolve_cli_invocation(project_root)
    chain = _safe_text(prepared.get("chain"))
    wallet_address = _wallet_command_address(prepared)
    source_address = _safe_text(prepared.get("execution_source_address"))
    target_token_address = _safe_text(prepared.get("target_token_address"))
    readable_amount = _safe_float(prepared.get("execution_source_readable_amount"), default=0.0)
    token_scan_arg = f"{_chain_index(chain)}:{target_token_address}"
    return {
        "wallet_login": cli_prefix + ["wallet", "login", "--force"],
        "wallet_status": cli_prefix + ["wallet", "status"],
        "wallet_addresses": cli_prefix + ["wallet", "addresses"],
        "wallet_balance": cli_prefix + ["wallet", "balance", "--chain", chain],
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
            wallet_address,
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
            wallet_address,
        ],
    }


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
    requested_leg_count = max(1, _safe_int(execution_intent.get("leg_count") or trade_plan.get("leg_count"), default=1))
    requested_notional_usd = _desired_notional_usd(trade_plan, requested_leg_count)
    live_cap_usd = _safe_float((execution_intent.get("metadata") or {}).get("live_cap_usd"), default=_configured_live_cap_usd())
    effective_notional_usd, leg_count, per_leg_usd, routing_amount_too_small = _compress_leg_plan(
        requested_notional_usd,
        requested_leg_count,
        live_cap_usd,
    )
    source_readable_amount = _safe_float(trade_plan.get("execution_source_readable_amount"), default=0.0)
    source_price_hint_usd = _resolved_price_hint_usd(chain, source_symbol, trade_plan.get("execution_source_unit_price_usd"))
    if source_readable_amount > 0:
        requested_per_leg = requested_notional_usd / requested_leg_count if requested_leg_count and requested_notional_usd > 0 else per_leg_usd
        ratio = per_leg_usd / requested_per_leg if requested_per_leg > 0 else 1.0
        readable_amount = round(source_readable_amount * ratio, 8)
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

    cli_prefix, cli_resolution = _resolve_cli_invocation(project_root)
    prepared = {
        "adapter": "onchainos_cli",
        "chain": chain,
        "wallet_address": _safe_text(trade_plan.get("wallet_address")),
        "execution_wallet_address": "",
        "target_token": target_token,
        "target_token_address": target_token_address,
        "execution_source_symbol": source_symbol,
        "execution_source_address": source_address,
        "requested_notional_usd": round(requested_notional_usd, 4),
        "effective_notional_usd": round(effective_notional_usd, 4),
        "requested_leg_count": requested_leg_count,
        "leg_count": leg_count,
        "per_leg_usd": per_leg_usd,
        "execution_source_unit_price_usd": source_price_hint_usd or None,
        "execution_source_readable_amount": readable_amount,
        "preferred_workflow": preferred_workflow,
        "preflight_checks": list(execution_intent.get("preflight_checks") or ()),
        "route_preferences": list(execution_intent.get("route_preferences") or ()),
        "requires_explicit_approval": bool(execution_intent.get("requires_explicit_approval", True)),
        "live_cap_usd": live_cap_usd,
        "min_leg_usd": _configured_min_leg_usd(),
        "routing_amount_too_small": routing_amount_too_small,
        "cli_manifest": str(_cli_manifest(project_root)),
        "cli_resolution": cli_resolution,
    }
    prepared["command_groups"] = _build_command_groups(prepared, project_root=project_root)
    return prepared


def _approval_wait_config() -> tuple[int, float]:
    retries = max(1, _safe_int(os.environ.get("OT_ONCHAINOS_APPROVAL_WAIT_RETRIES"), default=6))
    delay = max(0.0, _safe_float(os.environ.get("OT_ONCHAINOS_APPROVAL_WAIT_SECONDS"), default=2.0))
    return retries, delay


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


def _broadcast_approval_and_wait(
    prepared: dict[str, Any],
    approval_result: dict[str, Any],
    raw_amount: str,
    *,
    project_root: Path | None = None,
    env: dict[str, str] | None = None,
    executor: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    runtime_env, _ = _execution_env(project_root, env)
    approve_payload = _unwrap_payload((approval_result.get("approve") or {}).get("parsed_output"))
    contract_call = _build_wallet_contract_call_command(prepared, approve_payload, project_root=project_root)
    if contract_call is None:
        return {
            "ok": False,
            "broadcast": {},
            "allowance_checks": [],
            "allowance_ready": False,
            "tx_hashes": [],
            "metadata": {"approval_broadcast_error": "approve payload missing calldata"},
        }
    broadcast = _run_command(contract_call, env=runtime_env, executor=executor)
    tx_hashes = _extract_tx_hashes(broadcast)
    if not broadcast.get("ok"):
        return {
            "ok": False,
            "broadcast": broadcast,
            "allowance_checks": [],
            "allowance_ready": False,
            "tx_hashes": tx_hashes,
            "metadata": {"approval_broadcast_error": "wallet contract-call failed"},
        }
    retries, delay = _approval_wait_config()
    allowance_checks: list[dict[str, Any]] = []
    for attempt in range(retries):
        check = _run_command(_build_check_approvals_command(prepared, project_root=project_root), env=runtime_env, executor=executor)
        allowance_checks.append(check)
        spendable = _extract_spendable(check)
        if check.get("ok") and not _is_allowance_insufficient(spendable, raw_amount):
            return {
                "ok": True,
                "broadcast": broadcast,
                "allowance_checks": allowance_checks,
                "allowance_ready": True,
                "tx_hashes": tx_hashes,
                "metadata": {
                    "approval_broadcast_retries": attempt + 1,
                    "approval_spendable_after_broadcast": spendable or "0",
                },
            }
        if attempt < retries - 1 and delay > 0:
            time.sleep(delay)
    spendable = _extract_spendable(allowance_checks[-1]) if allowance_checks else ""
    return {
        "ok": False,
        "broadcast": broadcast,
        "allowance_checks": allowance_checks,
        "allowance_ready": False,
        "tx_hashes": tx_hashes,
        "metadata": {
            "approval_broadcast_error": "allowance not updated after approval broadcast",
            "approval_spendable_after_broadcast": spendable or "0",
        },
    }


def _preflight_execution(
    prepared: dict[str, Any],
    *,
    project_root: Path | None = None,
    env: dict[str, str] | None = None,
    executor: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    runtime_env, missing = _execution_env(project_root, env)
    cli_resolution = dict(prepared.get("cli_resolution") or {})
    if not bool(cli_resolution.get("resolved")):
        return {
            "prepared": prepared,
            "checks": [],
            "execution": {},
            "approval_required": False,
            "approval_result": {},
            "simulation_result": {},
            "readiness": "blocked_by_config",
            "metadata": {"cli_resolution": cli_resolution},
        }
    if missing:
        return {
            "prepared": prepared,
            "checks": [],
            "execution": {},
            "approval_required": False,
            "approval_result": {},
            "simulation_result": {},
            "readiness": "blocked_by_config",
            "metadata": {"missing_env": missing},
        }
    if prepared.get("routing_amount_too_small"):
        return {
            "prepared": prepared,
            "checks": [],
            "execution": {},
            "approval_required": False,
            "approval_result": {},
            "simulation_result": {},
            "readiness": "blocked_by_risk",
            "metadata": {
                "routing_error": "per_leg_usd_below_minimum",
                "min_leg_usd": prepared.get("min_leg_usd"),
                "per_leg_usd": prepared.get("per_leg_usd"),
            },
        }
    checks: list[dict[str, Any]] = []
    login = _run_command(prepared["command_groups"]["wallet_login"], env=runtime_env, executor=executor)
    checks.append(login)
    if not login.get("ok"):
        return {
            "prepared": prepared,
            "checks": checks,
            "execution": {},
            "approval_required": False,
            "approval_result": {},
            "simulation_result": {},
            "readiness": "blocked_by_config",
            "metadata": {},
        }
    status = _run_command(prepared["command_groups"]["wallet_status"], env=runtime_env, executor=executor)
    checks.append(status)
    status_payload = _unwrap_payload(status.get("parsed_output"))
    if not status.get("ok") or not bool(status_payload.get("loggedIn")):
        return {
            "prepared": prepared,
            "checks": checks,
            "execution": {},
            "approval_required": False,
            "approval_result": {},
            "simulation_result": {},
            "readiness": "blocked_by_config",
            "metadata": {"wallet_status": status_payload},
        }
    addresses = _run_command(prepared["command_groups"]["wallet_addresses"], env=runtime_env, executor=executor)
    checks.append(addresses)
    execution_wallet_address = _extract_execution_wallet_address(addresses, _safe_text(prepared.get("chain")))
    if not execution_wallet_address:
        return {
            "prepared": prepared,
            "checks": checks,
            "execution": addresses,
            "approval_required": False,
            "approval_result": {},
            "simulation_result": {},
            "readiness": "blocked_by_config",
            "metadata": {"wallet_error": "no execution wallet address for requested chain"},
        }
    prepared["execution_wallet_address"] = execution_wallet_address
    prepared["command_groups"] = _build_command_groups(prepared, project_root=project_root)
    balance = _run_command(prepared["command_groups"]["wallet_balance"], env=runtime_env, executor=executor)
    checks.append(balance)
    for check in prepared["preflight_checks"]:
        if check == "security_token_scan":
            checks.append(_run_command(prepared["command_groups"]["security_token_scan"], env=runtime_env, executor=executor))
    if any(not item.get("ok") for item in checks):
        return {
            "prepared": prepared,
            "checks": checks,
            "execution": {},
            "approval_required": False,
            "approval_result": {},
            "simulation_result": {},
            "readiness": "blocked_by_risk",
            "metadata": {},
        }
    quote = _run_command(prepared["command_groups"]["swap_swap"], env=runtime_env, executor=executor)
    checks.append(quote)
    if not quote.get("ok"):
        return {
            "prepared": prepared,
            "checks": checks,
            "execution": quote,
            "approval_required": False,
            "approval_result": {},
            "simulation_result": {},
            "readiness": "blocked_by_risk",
            "metadata": {},
        }
    raw_amount = _extract_quote_raw_amount(quote)
    if not raw_amount:
        return {
            "prepared": prepared,
            "checks": checks,
            "execution": quote,
            "approval_required": False,
            "approval_result": {},
            "simulation_result": {},
            "readiness": "blocked_by_risk",
            "metadata": {"quote_error": "missing routerResult.fromTokenAmount"},
        }

    approval_required = False
    approval_result: dict[str, Any] = {}
    simulation_result: dict[str, Any] = {}
    metadata: dict[str, Any] = {
        "quote_raw_amount": raw_amount,
        "style_wallet_address": _safe_text(prepared.get("wallet_address")),
        "execution_wallet_address": execution_wallet_address,
        "execution_wallet_total_usd": _extract_wallet_total_usd(balance),
    }

    if not _is_native_source(prepared):
        approvals = _run_command(_build_check_approvals_command(prepared, project_root=project_root), env=runtime_env, executor=executor)
        checks.append(approvals)
        approval_result["check"] = approvals
        if not approvals.get("ok"):
            return {
                "prepared": prepared,
                "checks": checks,
                "execution": approvals,
                "approval_required": False,
                "approval_result": approval_result,
                "simulation_result": simulation_result,
                "readiness": "blocked_by_risk",
                "metadata": metadata,
            }
        spendable = _extract_spendable(approvals)
        metadata["approval_spendable"] = spendable or "0"
        metadata["approval_required_amount"] = raw_amount
        if _is_allowance_insufficient(spendable, raw_amount):
            approval_required = True
            approve = _run_command(_build_approve_command(prepared, raw_amount, project_root=project_root), env=runtime_env, executor=executor)
            checks.append(approve)
            approval_result["approve"] = approve
            if not approve.get("ok"):
                return {
                    "prepared": prepared,
                    "checks": checks,
                    "execution": approve,
                    "approval_required": True,
                    "approval_result": approval_result,
                    "simulation_result": simulation_result,
                    "readiness": "blocked_by_risk",
                    "metadata": metadata,
                }
            approve_payload = _unwrap_payload(approve.get("parsed_output"))
            approve_simulate_command = _build_approve_simulate_command(prepared, approve_payload, project_root=project_root)
            if approve_simulate_command is None:
                return {
                    "prepared": prepared,
                    "checks": checks,
                    "execution": approve,
                    "approval_required": True,
                    "approval_result": approval_result,
                    "simulation_result": simulation_result,
                    "readiness": "blocked_by_risk",
                    "metadata": {**metadata, "simulate_error": "approve payload missing calldata"},
                }
            approve_simulation = _run_command(approve_simulate_command, env=runtime_env, executor=executor)
            approval_result["simulate"] = approve_simulation
            simulation_failed, fail_reason = _simulation_failed(approve_simulation)
            simulation_result = {
                "kind": "approval",
                "ok": approve_simulation.get("ok") and not simulation_failed,
                "swap_skipped": True,
                "result": approve_simulation,
            }
            if simulation_failed or not approve_simulation.get("ok"):
                if fail_reason:
                    metadata["simulate_fail_reason"] = fail_reason
                return {
                    "prepared": prepared,
                    "checks": checks,
                    "execution": approve_simulation,
                    "approval_required": True,
                    "approval_result": approval_result,
                    "simulation_result": simulation_result,
                    "readiness": "blocked_by_risk",
                    "metadata": metadata,
                }
            metadata["swap_simulation_skipped"] = True
            return {
                "prepared": prepared,
                "checks": checks,
                "execution": approve_simulation,
                "approval_required": True,
                "approval_result": approval_result,
                "simulation_result": simulation_result,
                "readiness": "dry_run_ready",
                "metadata": metadata,
            }

    simulate_command = _build_gateway_simulate_command(prepared, quote.get("parsed_output"), project_root=project_root)
    if simulate_command is None:
        return {
            "prepared": prepared,
            "checks": checks,
            "execution": quote,
            "approval_required": approval_required,
            "approval_result": approval_result,
            "simulation_result": simulation_result,
            "readiness": "blocked_by_risk",
            "metadata": {**metadata, "simulate_error": "swap payload missing tx.to or tx.data"},
        }
    execution = _run_command(simulate_command, env=runtime_env, executor=executor)
    simulation_failed, fail_reason = _simulation_failed(execution)
    if fail_reason:
        metadata["simulate_fail_reason"] = fail_reason
    simulation_result = {
        "kind": "swap",
        "ok": execution.get("ok") and not simulation_failed,
        "swap_skipped": False,
        "result": execution,
    }
    readiness = "dry_run_ready" if execution.get("ok") and not simulation_failed else "blocked_by_risk"
    return {
        "prepared": prepared,
        "checks": checks,
        "execution": execution,
        "approval_required": approval_required,
        "approval_result": approval_result,
        "simulation_result": simulation_result,
        "readiness": readiness,
        "metadata": metadata,
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
    preflight = _preflight_execution(
        prepared,
        project_root=project_root,
        env=env,
        executor=executor,
    )
    return collect_execution_result(
        preflight["prepared"],
        mode="dry_run",
        checks=preflight["checks"],
        execution=preflight["execution"],
        readiness=preflight["readiness"],
        metadata=preflight["metadata"],
        approval_required=preflight["approval_required"],
        approval_result=preflight["approval_result"],
        simulation_result=preflight["simulation_result"],
        live_cap_usd=preflight["prepared"].get("live_cap_usd"),
        executed_leg_count=0,
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
    if prepared["requires_explicit_approval"]:
        raise ValueError("live execution requires explicit approval")
    preflight = _preflight_execution(
        prepared,
        project_root=project_root,
        env=env,
        executor=executor,
    )
    if preflight["readiness"] != "dry_run_ready":
        return collect_execution_result(
            preflight["prepared"],
            mode="live",
            checks=preflight["checks"],
            execution=preflight["execution"],
            readiness=preflight["readiness"],
            metadata=preflight["metadata"],
            approval_required=preflight["approval_required"],
            approval_result=preflight["approval_result"],
            simulation_result=preflight["simulation_result"],
            live_cap_usd=preflight["prepared"].get("live_cap_usd"),
            executed_leg_count=0,
        )
    if _safe_float((preflight.get("metadata") or {}).get("execution_wallet_total_usd"), default=0.0) <= 0:
        return collect_execution_result(
            preflight["prepared"],
            mode="live",
            checks=preflight["checks"],
            execution={},
            readiness="blocked_by_config",
            metadata={**(preflight["metadata"] or {}), "wallet_error": "execution wallet has no funded balance on chain"},
            approval_required=preflight["approval_required"],
            approval_result=preflight["approval_result"],
            simulation_result=preflight["simulation_result"],
            live_cap_usd=preflight["prepared"].get("live_cap_usd"),
            executed_leg_count=0,
        )
    runtime_env, _ = _execution_env(project_root, env)
    broadcast_results: list[dict[str, Any]] = []
    tx_hashes: list[str] = []
    executed_leg_count = 0
    checks = list(preflight["checks"])
    metadata = dict(preflight["metadata"] or {})
    approval_result = dict(preflight["approval_result"] or {})
    if preflight["approval_required"]:
        approval_broadcast = _broadcast_approval_and_wait(
            preflight["prepared"],
            approval_result,
            _safe_text(metadata.get("quote_raw_amount")),
            project_root=project_root,
            env=env,
            executor=executor,
        )
        approval_result["broadcast"] = approval_broadcast.get("broadcast")
        approval_result["allowance_checks"] = approval_broadcast.get("allowance_checks") or []
        if approval_broadcast.get("broadcast"):
            checks.append(approval_broadcast["broadcast"])
        checks.extend(approval_broadcast.get("allowance_checks") or [])
        for tx_hash in approval_broadcast.get("tx_hashes") or []:
            if tx_hash not in tx_hashes:
                tx_hashes.append(tx_hash)
        metadata.update(approval_broadcast.get("metadata") or {})
        if not approval_broadcast.get("ok"):
            return collect_execution_result(
                preflight["prepared"],
                mode="live",
                checks=checks,
                execution=approval_broadcast.get("broadcast") or {},
                readiness="blocked_by_risk",
                metadata=metadata,
                approval_required=preflight["approval_required"],
                approval_result=approval_result,
                simulation_result=preflight["simulation_result"],
                broadcast_results=[],
                tx_hashes=tx_hashes,
                live_cap_usd=preflight["prepared"].get("live_cap_usd"),
                executed_leg_count=0,
            )
    for _ in range(max(1, _safe_int(prepared.get("leg_count"), default=1))):
        execution = _run_command(prepared["command_groups"]["swap_execute"], env=runtime_env, executor=executor)
        broadcast_results.append(execution)
        if execution.get("ok"):
            executed_leg_count += 1
            for tx_hash in _extract_tx_hashes(execution):
                if tx_hash not in tx_hashes:
                    tx_hashes.append(tx_hash)
    final_execution = broadcast_results[-1] if broadcast_results else {}
    simulation_failed, fail_reason = _simulation_failed(final_execution)
    readiness = "live_ready" if broadcast_results and all(item.get("ok") for item in broadcast_results) and not simulation_failed else "blocked_by_risk"
    return collect_execution_result(
        preflight["prepared"],
        mode="live",
        checks=checks,
        execution=final_execution,
        readiness=readiness,
        metadata={**metadata, **({"simulate_fail_reason": fail_reason} if simulation_failed else {})},
        approval_required=preflight["approval_required"],
        approval_result=approval_result,
        simulation_result=preflight["simulation_result"],
        broadcast_results=broadcast_results,
        tx_hashes=tx_hashes,
        live_cap_usd=preflight["prepared"].get("live_cap_usd"),
        executed_leg_count=executed_leg_count,
    )


def collect_execution_result(
    prepared_execution: dict[str, Any],
    *,
    mode: str,
    checks: list[dict[str, Any]] | None = None,
    execution: dict[str, Any] | None = None,
    readiness: str | None = None,
    metadata: dict[str, Any] | None = None,
    approval_required: bool | None = None,
    approval_result: dict[str, Any] | None = None,
    simulation_result: dict[str, Any] | None = None,
    broadcast_results: list[dict[str, Any]] | None = None,
    tx_hashes: list[str] | None = None,
    live_cap_usd: float | None = None,
    executed_leg_count: int | None = None,
) -> dict[str, Any]:
    check_results = list(checks or [])
    execution_result = dict(execution or {})
    approval_payload = dict(approval_result or {})
    simulation_payload = dict(simulation_result or {})
    broadcasts = list(broadcast_results or [])
    blocked = any(not item.get("ok") for item in check_results)
    if prepared_execution.get("routing_amount_too_small"):
        blocked = True
    default_readiness = "blocked_by_risk" if blocked else "dry_run_ready" if mode == "dry_run" else "live_ready"
    resolved_readiness = readiness or default_readiness
    execution_ok = execution_result.get("ok") if execution_result else True
    if broadcasts:
        execution_ok = execution_ok and all(item.get("ok") for item in broadcasts)
    return {
        "ok": resolved_readiness in {"dry_run_ready", "live_ready"} and not blocked and execution_ok,
        "mode": mode,
        "execution_readiness": resolved_readiness,
        "prepared_execution": prepared_execution,
        "checks": check_results,
        "execution": execution_result,
        "approval_required": bool(approval_required),
        "approval_result": approval_payload,
        "simulation_result": simulation_payload,
        "broadcast_results": broadcasts,
        "tx_hashes": list(tx_hashes or []),
        "live_cap_usd": live_cap_usd if live_cap_usd is not None else prepared_execution.get("live_cap_usd"),
        "executed_leg_count": executed_leg_count if executed_leg_count is not None else 0,
        "metadata": dict(metadata or {}),
    }
