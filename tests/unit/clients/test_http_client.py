from __future__ import annotations

import json

import pytest

from ot_skill_enterprise.shared.clients import AveDataServiceClient, HttpClientError, HttpJsonClient
from ot_skill_enterprise.shared.clients.http import HttpRequest, HttpResponse
from ot_skill_enterprise.shared.contracts import DiscoverTokensResponse, TokenDiscoveryDomain


def test_http_json_client_serializes_payload_and_parses_json() -> None:
    seen: dict[str, HttpRequest] = {}

    def fake_transport(http_request: HttpRequest) -> HttpResponse:
        seen["request"] = http_request
        return HttpResponse(
            status_code=200,
            headers={"content-type": "application/json"},
            body=b'{"ok":true,"operation":"ping","data":{"message":"pong"},"meta":{"provider":"ave"}}',
        )

    client = HttpJsonClient("https://example.test", default_headers={"x-test": "1"}, transport=fake_transport)
    body = client.post_json("/v1/ping", {"hello": "world"})

    assert seen["request"].url == "https://example.test/v1/ping"
    assert seen["request"].headers["x-test"] == "1"
    assert seen["request"].headers["content-type"] == "application/json"
    assert json.loads(seen["request"].body.decode("utf-8")) == {"hello": "world"}
    assert body["data"]["message"] == "pong"


def test_http_json_client_raises_for_non_success_status() -> None:
    def fake_transport(_: HttpRequest) -> HttpResponse:
        return HttpResponse(
            status_code=503,
            headers={"content-type": "application/json"},
            body=b'{"error":{"code":"upstream_down","message":"try later"}}',
        )

    client = HttpJsonClient("https://example.test", transport=fake_transport)

    with pytest.raises(HttpClientError) as exc_info:
        client.get_json("/v1/ping")

    assert exc_info.value.status_code == 503
    assert exc_info.value.payload["error"]["code"] == "upstream_down"


def test_ave_data_service_client_invokes_operation_endpoint() -> None:
    seen: dict[str, HttpRequest] = {}

    def fake_transport(http_request: HttpRequest) -> HttpResponse:
        seen["request"] = http_request
        return HttpResponse(
            status_code=200,
            headers={"content-type": "application/json"},
            body=b'''{
                "ok": true,
                "operation": "discover_tokens",
                "request_id": "req-1",
                "data": {
                    "token_refs": [{"identifier": "0xabc", "symbol": "ABC"}],
                    "source_meta": {"provider": "ave", "request_id": "req-1"}
                },
                "meta": {"provider": "ave", "request_id": "req-1"}
            }''',
        )

    client = AveDataServiceClient("https://api.example.test", api_key="secret", http_client=HttpJsonClient("https://api.example.test", transport=fake_transport))
    envelope: DiscoverTokensResponse = client.discover_tokens(query="abc", chain="eth", limit=5)

    assert seen["request"].url == "https://api.example.test/v1/operations/discover_tokens"
    assert seen["request"].headers["authorization"] == "Bearer secret"
    assert envelope.ok is True
    assert envelope.data is not None
    assert envelope.data.token_refs[0].identifier == "0xabc"
    assert envelope.data.source_meta.request_id == "req-1"

