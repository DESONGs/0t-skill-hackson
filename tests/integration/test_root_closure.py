from __future__ import annotations

from pathlib import Path

from ot_skill_enterprise.enterprise_bridge import EnterpriseBridge
from ot_skill_enterprise.root_runtime import LocalAveDataClient, run_preset_workflow


class FakeProvider:
    name = "fake"

    def discover_tokens(self, payload):
        chain = payload.chain or "eth"
        return {
            "token_refs": [
                {"identifier": f"{chain}:alpha", "chain": chain, "symbol": "ALPHA", "rank": 1, "score": 0.9}
            ],
            "ranking_context": {"title": "discover", "window": "24h", "source": "fake", "metadata": {}},
            "source_meta": {"provider": "fake", "request_id": "discover-1", "cached": False, "metadata": {}},
        }

    def inspect_token(self, payload):
        identifier = payload.token_ref.identifier
        chain = payload.token_ref.chain or "eth"
        return {
            "identity": {"identifier": identifier, "chain": chain, "symbol": "ALPHA"},
            "market_snapshot": {"price_usd": 1.23, "status": "available"},
            "risk_snapshot": {"risk_level": "medium", "flags": [], "status": "available"},
            "holder_snapshot": {"holder_count": 10, "top_holder_share_pct": 25.0, "holders": [], "status": "available"},
            "main_pair_ref": {"identifier": "ALPHA/USDT", "chain": chain, "pair_address": "0xpair"},
            "source_meta": {"provider": "fake", "request_id": "token-1", "cached": False, "metadata": {}},
        }

    def inspect_market(self, payload):
        chain = payload.token_ref.chain or "eth"
        identifier = payload.token_ref.identifier
        return {
            "selected_pair": {"identifier": f"{identifier}-pair", "chain": chain, "pair_address": "0xpair"},
            "ohlcv": [],
            "recent_swaps": [{"tx_hash": "0x1", "timestamp": "2026-04-09T00:00:00Z", "side": "buy"}],
            "flow_summary": {"buy_count": 1, "sell_count": 0, "net_flow_usd": 100.0},
            "source_meta": {"provider": "fake", "request_id": "market-1", "cached": False, "metadata": {}},
        }

    def inspect_wallet(self, payload):
        chain = payload.chain or "eth"
        return {
            "wallet_summary": {"wallet_address": payload.wallet, "chain": chain, "status": "available"},
            "holdings": [{"token_ref": {"identifier": f"{chain}:alpha", "chain": chain, "symbol": "ALPHA"}}],
            "recent_activity": [],
            "source_meta": {"provider": "fake", "request_id": "wallet-1", "cached": False, "metadata": {}},
        }

    def review_signals(self, payload):
        chain = payload.chain or "eth"
        token_ref = payload.token_ref.model_dump(mode="json") if payload.token_ref else {"identifier": f"{chain}:alpha", "chain": chain, "symbol": "ALPHA"}
        return {
            "signals": [{"signal_id": "sig-1", "title": "Momentum", "severity": "high", "chain": chain, "token_ref": token_ref}],
            "linked_token_refs": [token_ref],
            "source_meta": {"provider": "fake", "request_id": "signals-1", "cached": False, "metadata": {}},
        }


def test_single_root_closure_runs_workflow_and_discovers_vendored_assets(tmp_path: Path) -> None:
    bridge = EnterpriseBridge.from_project_root()
    assert bridge.vendor_root.exists()
    assert (bridge.root / "vendor" / "ave_cloud_skill" / "scripts" / "ave_data_rest.py").exists()

    result = run_preset_workflow(
        "token_due_diligence",
        {
            "run_id": "root-closure",
            "topic": "alpha diligence",
            "objective": "review token risk and market context",
            "target_token_ref": {"identifier": "eth:alpha", "chain": "eth", "symbol": "ALPHA"},
            "chain": "eth",
        },
        workspace_dir=tmp_path,
        client=LocalAveDataClient(provider=FakeProvider()),
    )

    assert result["ok"] is True
    assert result["status"] == "succeeded"
    assert (tmp_path / "reports" / "analysis-report.json").exists()


def test_single_root_closure_default_client_uses_local_provider(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("AVE_API_KEY", raising=False)
    monkeypatch.delenv("AVE_DATA_PROVIDER", raising=False)

    result = run_preset_workflow(
        "token_due_diligence",
        {
            "run_id": "root-default-client",
            "topic": "alpha diligence",
            "objective": "review token risk and market context",
            "target_token_ref": {"identifier": "eth:alpha", "chain": "eth", "symbol": "ALPHA"},
            "chain": "eth",
        },
        workspace_dir=tmp_path,
    )

    assert result["ok"] is True
    assert result["status"] == "succeeded"
    assert (tmp_path / "reports" / "analysis-report.json").exists()
