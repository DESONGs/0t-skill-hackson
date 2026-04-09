from __future__ import annotations

import json
from pathlib import Path

from ot_skill_enterprise.gateway import run_action


class FakeDiscoverTokensClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def discover_tokens(self, request):
        self.calls.append(request.model_dump(mode="json"))
        return {
            "ok": True,
            "operation": "discover_tokens",
            "request_id": "req-1",
            "data": {
                "token_refs": [
                    {"identifier": "0xabc", "symbol": "ABC"},
                    {"identifier": "0xdef", "symbol": "DEF"},
                ],
                "source_meta": {"provider": "ave", "request_id": "req-1"},
            },
            "meta": {"provider": "ave", "request_id": "req-1"},
        }


def test_discover_tokens_writes_artifact_and_stdout_payload(tmp_path: Path) -> None:
    client = FakeDiscoverTokensClient()

    result = run_action(
        "discover_tokens",
        {"query": "alpha", "chain": "eth", "limit": 2},
        client=client,
        workspace_dir=tmp_path,
    )

    assert client.calls == [{"query": "alpha", "chain": "eth", "source": None, "limit": 2}]
    assert result["ok"] is True
    assert result["action"] == "discover_tokens"
    assert result["summary"] == "discovered 2 token candidates for alpha"
    assert result["artifacts"][0]["kind"] == "json"

    artifact_path = Path(result["artifacts"][0]["uri"])
    assert artifact_path.exists()
    assert artifact_path.parent == tmp_path / "data"

    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert artifact["action"] == "discover_tokens"
    assert artifact["request"] == {"query": "alpha", "chain": "eth", "source": None, "limit": 2}
    assert artifact["response"]["meta"]["provider"] == "ave"
