from __future__ import annotations

from pathlib import Path

import pytest

from ot_skill_enterprise.analysis import plan_data_needs, synthesize_evidence, write_report
from ot_skill_enterprise.gateway import run_action
from ot_skill_enterprise.workflows import WorkflowRuntime


class FakeAveDataClient:
    def discover_tokens(self, request):
        payload = request.model_dump(mode="json")
        query = payload.get("query") or payload.get("chain") or "market"
        chain = payload.get("chain") or "eth"
        return {
            "ok": True,
            "operation": "discover_tokens",
            "request_id": f"discover-{query}",
            "data": {
                "token_refs": [
                    {
                        "identifier": f"{chain}:alpha",
                        "chain": chain,
                        "symbol": "ALPHA",
                        "rank": 1,
                        "score": 0.91,
                    },
                    {
                        "identifier": f"{chain}:beta",
                        "chain": chain,
                        "symbol": "BETA",
                        "rank": 2,
                        "score": 0.72,
                    },
                ],
                "ranking_context": {"title": str(query), "window": "24h", "source": "integration"},
                "source_meta": {"provider": "ave", "request_id": f"discover-{query}"},
            },
            "meta": {"provider": "ave", "request_id": f"discover-{query}"},
            "error": None,
        }

    def inspect_token(self, request):
        payload = request.model_dump(mode="json")
        token_ref = payload["token_ref"]
        identifier = token_ref["identifier"]
        chain = token_ref.get("chain") or "eth"
        return {
            "ok": True,
            "operation": "inspect_token",
            "request_id": f"token-{identifier}",
            "data": {
                "identity": {
                    "identifier": identifier,
                    "chain": chain,
                    "symbol": identifier.split(":")[-1].upper(),
                    "name": f"Token {identifier}",
                },
                "market_snapshot": {
                    "price_usd": 1.23,
                    "market_cap_usd": 123456.0,
                    "liquidity_usd": 54321.0,
                    "volume_24h_usd": 7777.0,
                    "status": "available",
                },
                "risk_snapshot": {
                    "risk_level": "medium",
                    "flags": ["liquidity-concentration"],
                    "honeypot": False,
                    "buy_tax_bps": 50,
                    "sell_tax_bps": 60,
                    "status": "available",
                },
                "holder_snapshot": {
                    "holder_count": 12,
                    "top_holder_share_pct": 33.0,
                    "holders": [
                        {"holder_address": "0x111", "share_pct": 33.0, "label": "treasury"},
                        {"holder_address": "0x222", "share_pct": 17.5, "label": "market maker"},
                    ],
                    "status": "available",
                },
                "main_pair_ref": {
                    "identifier": f"{identifier}-pair",
                    "chain": chain,
                    "pair_address": f"{identifier}-pair-address",
                    "dex": "uniswap",
                },
                "source_meta": {"provider": "ave", "request_id": f"token-{identifier}"},
            },
            "meta": {"provider": "ave", "request_id": f"token-{identifier}"},
            "error": None,
        }

    def inspect_market(self, request):
        payload = request.model_dump(mode="json")
        token_ref = payload["token_ref"]
        identifier = token_ref["identifier"]
        return {
            "ok": True,
            "operation": "inspect_market",
            "request_id": f"market-{identifier}",
            "data": {
                "selected_pair": {
                    "identifier": f"{identifier}-pair",
                    "chain": token_ref.get("chain") or "eth",
                    "pair_address": f"{identifier}-pair-address",
                    "dex": "uniswap",
                },
                "recent_swaps": [
                    {
                        "tx_hash": "0xswap1",
                        "timestamp": "2026-04-09T00:00:00Z",
                        "side": "buy",
                        "amount_base": 120.0,
                        "amount_quote": 144.0,
                        "trader": "0xaaa",
                    },
                    {
                        "tx_hash": "0xswap2",
                        "timestamp": "2026-04-09T00:05:00Z",
                        "side": "sell",
                        "amount_base": 33.0,
                        "amount_quote": 39.6,
                        "trader": "0xbbb",
                    },
                ],
                "flow_summary": {
                    "buy_count": 1,
                    "sell_count": 1,
                    "net_flow_usd": 104.4,
                    "large_trade_count": 1,
                },
                "source_meta": {"provider": "ave", "request_id": f"market-{identifier}"},
            },
            "meta": {"provider": "ave", "request_id": f"market-{identifier}"},
            "error": None,
        }

    def inspect_wallet(self, request):
        payload = request.model_dump(mode="json")
        wallet = payload["wallet"]
        return {
            "ok": True,
            "operation": "inspect_wallet",
            "request_id": f"wallet-{wallet}",
            "data": {
                "wallet_summary": {
                    "wallet_address": wallet,
                    "chain": payload.get("chain") or "eth",
                    "label": "integration wallet",
                    "balance_usd": 9999.0,
                    "token_count": 2,
                    "status": "available",
                },
                "holdings": [
                    {
                        "token_ref": {
                            "identifier": "eth:alpha",
                            "chain": payload.get("chain") or "eth",
                            "symbol": "ALPHA",
                        },
                        "quantity": 100.0,
                        "value_usd": 5000.0,
                        "allocation_pct": 50.0,
                    },
                    {
                        "token_ref": {
                            "identifier": "eth:beta",
                            "chain": payload.get("chain") or "eth",
                            "symbol": "BETA",
                        },
                        "quantity": 50.0,
                        "value_usd": 2500.0,
                        "allocation_pct": 25.0,
                    },
                ],
                "recent_activity": [
                    {
                        "tx_hash": "0xwallet1",
                        "timestamp": "2026-04-09T00:10:00Z",
                        "action": "swap",
                        "amount_usd": 150.0,
                    }
                ],
                "source_meta": {"provider": "ave", "request_id": f"wallet-{wallet}"},
            },
            "meta": {"provider": "ave", "request_id": f"wallet-{wallet}"},
            "error": None,
        }

    def review_signals(self, request):
        payload = request.model_dump(mode="json")
        token_ref = payload.get("token_ref") or {}
        identifier = token_ref.get("identifier", "market")
        return {
            "ok": True,
            "operation": "review_signals",
            "request_id": f"signals-{identifier}",
            "data": {
                "signals": [
                    {
                        "signal_id": "sig-1",
                        "title": "Social momentum",
                        "severity": "medium",
                        "chain": payload.get("chain") or "eth",
                        "token_ref": token_ref or None,
                        "description": "Momentum increased on public channels.",
                    },
                    {
                        "signal_id": "sig-2",
                        "title": "Contract watch",
                        "severity": "high",
                        "chain": payload.get("chain") or "eth",
                        "token_ref": token_ref or None,
                        "description": "Permission change noticed in signal stream.",
                    },
                ],
                "linked_token_refs": [token_ref] if token_ref else [],
                "source_meta": {"provider": "ave", "request_id": f"signals-{identifier}"},
            },
            "meta": {"provider": "ave", "request_id": f"signals-{identifier}"},
            "error": None,
        }


def _build_handlers(tmp_path: Path, client: FakeAveDataClient, fail_action: str | None = None):
    def fail_handler(step, payload, context):
        return {
            "ok": False,
            "action": step.action_id,
            "operation": step.action_id,
            "request_id": context.run_id,
            "summary": f"{step.action_id} simulated failure",
            "payload": {"request": dict(payload)},
            "artifacts": [],
            "meta": {"provider": "local"},
            "error": {
                "code": "UPSTREAM_HTTP_ERROR",
                "message": "simulated workflow failure",
                "details": {"step_id": step.step_id, "action_id": step.action_id},
            },
        }

    def maybe_fail(action_name: str, handler):
        if fail_action == action_name:
            return fail_handler
        return handler

    return {
        ("analysis-core", "plan_data_needs"): maybe_fail(
            "plan_data_needs", lambda step, payload, context: plan_data_needs(payload, workspace_dir=tmp_path)
        ),
        ("analysis-core", "synthesize_evidence"): maybe_fail(
            "synthesize_evidence", lambda step, payload, context: synthesize_evidence(payload, workspace_dir=tmp_path)
        ),
        ("analysis-core", "write_report"): maybe_fail(
            "write_report", lambda step, payload, context: write_report(payload, workspace_dir=tmp_path)
        ),
        ("ave-data-gateway", "discover_tokens"): maybe_fail(
            "discover_tokens", lambda step, payload, context: run_action("discover_tokens", payload, client=client, workspace_dir=tmp_path)
        ),
        ("ave-data-gateway", "inspect_token"): maybe_fail(
            "inspect_token", lambda step, payload, context: run_action("inspect_token", payload, client=client, workspace_dir=tmp_path)
        ),
        ("ave-data-gateway", "inspect_market"): maybe_fail(
            "inspect_market", lambda step, payload, context: run_action("inspect_market", payload, client=client, workspace_dir=tmp_path)
        ),
        ("ave-data-gateway", "inspect_wallet"): maybe_fail(
            "inspect_wallet", lambda step, payload, context: run_action("inspect_wallet", payload, client=client, workspace_dir=tmp_path)
        ),
        ("ave-data-gateway", "review_signals"): maybe_fail(
            "review_signals", lambda step, payload, context: run_action("review_signals", payload, client=client, workspace_dir=tmp_path)
        ),
    }


@pytest.mark.parametrize(
    ("preset_name", "run_input", "expected_actions"),
    [
        (
            "token_due_diligence",
            {
                "run_id": "workflow-token-success",
                "topic": "token alpha diligence",
                "objective": "review token risk and market context",
                "target_token_ref": {"identifier": "eth:alpha", "chain": "eth", "symbol": "ALPHA"},
                "chain": "eth",
            },
            [
                "plan_data_needs",
                "inspect_token",
                "inspect_market",
                "review_signals",
                "synthesize_evidence",
                "write_report",
            ],
        ),
        (
            "wallet_profile",
            {
                "run_id": "workflow-wallet-success",
                "topic": "wallet profile",
                "objective": "review holdings and activity",
                "wallet_address": "0xwallet",
                "chain": "eth",
            },
            [
                "plan_data_needs",
                "inspect_wallet",
                "inspect_token",
                "inspect_market",
                "synthesize_evidence",
                "write_report",
            ],
        ),
        (
            "hot_market_scan",
            {
                "run_id": "workflow-market-success",
                "topic": "hot market scan",
                "objective": "find active tokens",
                "query": "hot tokens",
                "chain": "eth",
                "limit": 2,
            },
            [
                "plan_data_needs",
                "discover_tokens",
                "inspect_market",
                "review_signals",
                "synthesize_evidence",
                "write_report",
            ],
        ),
    ],
)
def test_workflow_runtime_success_cases(tmp_path: Path, preset_name: str, run_input: dict[str, object], expected_actions: list[str]) -> None:
    client = FakeAveDataClient()
    runtime = WorkflowRuntime(handlers=_build_handlers(tmp_path, client), workspace_dir=tmp_path)

    result = runtime.run(preset_name, run_input, run_id=str(run_input["run_id"]), workspace_dir=tmp_path)

    assert result["ok"] is True
    assert result["status"] == "succeeded"
    assert [step["action_id"] for step in result["executed_steps"]] == expected_actions
    assert result["artifact_refs"]
    assert result["failure"] is None
    assert result["failure_summary"] is None
    assert (tmp_path / "reports" / "analysis-report.json").exists()
    assert (tmp_path / "reports" / "analysis-report.md").exists()


@pytest.mark.parametrize(
    ("preset_name", "run_input", "fail_action", "expected_actions", "expected_failure_step"),
    [
        (
            "token_due_diligence",
            {
                "run_id": "workflow-token-failure",
                "topic": "token alpha diligence",
                "objective": "review token risk and market context",
                "target_token_ref": {"identifier": "eth:alpha", "chain": "eth", "symbol": "ALPHA"},
                "chain": "eth",
            },
            "inspect_market",
            ["plan_data_needs", "inspect_token", "inspect_market"],
            "inspect_market",
        ),
        (
            "wallet_profile",
            {
                "run_id": "workflow-wallet-failure",
                "topic": "wallet profile",
                "objective": "review holdings and activity",
                "wallet_address": "0xwallet",
                "chain": "eth",
            },
            "inspect_token",
            ["plan_data_needs", "inspect_wallet", "inspect_token"],
            "inspect_token",
        ),
        (
            "hot_market_scan",
            {
                "run_id": "workflow-market-failure",
                "topic": "hot market scan",
                "objective": "find active tokens",
                "query": "hot tokens",
                "chain": "eth",
                "limit": 2,
            },
            "review_signals",
            ["plan_data_needs", "discover_tokens", "inspect_market", "review_signals"],
            "review_signals",
        ),
    ],
)
def test_workflow_runtime_failure_cases(
    tmp_path: Path,
    preset_name: str,
    run_input: dict[str, object],
    fail_action: str,
    expected_actions: list[str],
    expected_failure_step: str,
) -> None:
    client = FakeAveDataClient()
    runtime = WorkflowRuntime(handlers=_build_handlers(tmp_path, client, fail_action=fail_action), workspace_dir=tmp_path)

    result = runtime.run(preset_name, run_input, run_id=str(run_input["run_id"]), workspace_dir=tmp_path)

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert [step["action_id"] for step in result["executed_steps"]] == expected_actions
    assert result["failure_step_id"] == expected_failure_step
    assert expected_failure_step in result["failure_summary"]
    assert result["failure"]["code"] == "UPSTREAM_HTTP_ERROR"
    assert result["executed_steps"][-1]["status"] == "failed"
    assert not (tmp_path / "reports" / "analysis-report.json").exists()
