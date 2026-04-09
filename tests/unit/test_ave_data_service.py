from __future__ import annotations

import os
from pathlib import Path
import sys
import subprocess

import anyio
import httpx

SERVICE_DIR = Path("/Users/chenge/Desktop/0t-ave/0t-skill_enterprise/services/ave-data-service")
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

os.environ.setdefault("AVE_DATA_PROVIDER", "mock")

import main as ave_service  # noqa: E402
from models import DiscoverTokensRequest  # noqa: E402
from providers import AveRestProvider  # noqa: E402


def request_json(method: str, path: str, payload: dict[str, object] | None = None) -> httpx.Response:
    async def _call() -> httpx.Response:
        transport = httpx.ASGITransport(app=ave_service.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.request(method, path, json=payload)

    return anyio.run(_call)


def test_healthz_returns_ok() -> None:
    response = request_json("GET", "/healthz")
    body = response.json()

    assert response.status_code == 200
    assert body["ok"] is True
    assert body["operation"] == "healthz"
    assert body["data"]["status"] == "ok"
    assert body["data"]["service"] == "ave-data-service"
    assert body["meta"]["provider"] == "mock"


def test_discover_tokens_basic_return() -> None:
    response = request_json("POST", "/v1/operations/discover_tokens", {"query": "alpha", "chain": "eth", "limit": 2})
    body = response.json()

    assert response.status_code == 200
    assert body["ok"] is True
    assert body["operation"] == "discover_tokens"
    assert len(body["data"]["token_refs"]) == 2
    assert body["data"]["token_refs"][0]["identifier"] == "eth:ALPHA"
    assert body["data"]["ranking_context"]["metadata"]["query"] == "alpha"


def test_inspect_token_basic_return() -> None:
    response = request_json(
        "POST",
        "/v1/operations/inspect_token",
        {"token_ref": {"identifier": "eth:ave", "chain": "eth", "symbol": "AVE"}},
    )
    body = response.json()

    assert response.status_code == 200
    assert body["operation"] == "inspect_token"
    assert body["data"]["identity"]["identifier"] == "eth:ave"
    assert body["data"]["identity"]["symbol"] == "AVE"
    assert "market_snapshot" in body["data"]
    assert "risk_snapshot" in body["data"]


def test_inspect_market_basic_return() -> None:
    response = request_json(
        "POST",
        "/v1/operations/inspect_market",
        {"token_ref": {"identifier": "eth:ave", "chain": "eth", "symbol": "AVE"}, "interval": "1h", "window": "24h"},
    )
    body = response.json()

    assert response.status_code == 200
    assert body["data"]["selected_pair"]["identifier"] == "AVE/USDT"
    assert len(body["data"]["ohlcv"]) == 4
    assert "recent_swaps" in body["data"]


def test_inspect_wallet_basic_return() -> None:
    response = request_json("POST", "/v1/operations/inspect_wallet", {"wallet": "0xabc", "chain": "eth"})
    body = response.json()

    assert response.status_code == 200
    assert body["data"]["wallet_summary"]["wallet_address"] == "0xabc"
    assert len(body["data"]["holdings"]) == 2


def test_review_signals_basic_return() -> None:
    response = request_json(
        "POST",
        "/v1/operations/review_signals",
        {"chain": "eth", "limit": 2, "token_ref": {"identifier": "eth:alpha", "chain": "eth", "symbol": "ALPHA"}},
    )
    body = response.json()

    assert response.status_code == 200
    assert body["data"]["signals"][0]["token_ref"]["identifier"] == "eth:alpha"
    assert len(body["data"]["signals"]) == 2


def test_ave_rest_provider_translates_search_command(monkeypatch, tmp_path) -> None:
    script_path = tmp_path / "ave_data_rest.py"
    script_path.write_text("print('stub')")

    seen: dict[str, object] = {}

    def fake_run(cmd, *, capture_output, text, env, timeout):  # type: ignore[no-untyped-def]
        seen["cmd"] = cmd
        seen["env"] = env
        seen["timeout"] = timeout

        class CompletedProcess:
            returncode = 0
            stdout = (
                '{"data":{"items":[{"identifier":"eth:0xabc","chain":"eth","token_address":"0xabc",'
                '"symbol":"ABC","name":"Alpha","rank":1,"score":99.0}],'
                '"ranking_context":{"title":"alpha"}},'
                '"request_id":"req-search","meta":{"request_id":"req-search"}}'
            )
            stderr = ""

        return CompletedProcess()

    monkeypatch.setattr(subprocess, "run", fake_run)

    provider = AveRestProvider(script_path=script_path, python_executable="/usr/bin/python3", timeout_seconds=5.0)
    response = provider.discover_tokens(DiscoverTokensRequest(query="alpha", chain="eth", limit=1))

    assert seen["cmd"] == ["/usr/bin/python3", str(script_path), "search", "--keyword", "alpha", "--limit", "1", "--chain", "eth"]
    assert seen["env"]["AVE_IN_SERVER"] == "true"
    assert seen["env"]["AVE_USE_DOCKER"] == "false"
    assert response["token_refs"][0]["identifier"] == "eth:0xabc"
    assert response["source_meta"]["request_id"] == "req-search"
