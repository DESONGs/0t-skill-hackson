from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from ot_skill_enterprise.shared.clients import AveDataServiceClient, HttpClientError
from ot_skill_enterprise.shared.contracts import (
    ArtifactRef,
    DiscoverTokensRequest,
    InspectMarketRequest,
    InspectTokenRequest,
    InspectWalletRequest,
    ReviewSignalsRequest,
)
from ot_skill_enterprise.providers.contracts import ProviderActionResult


ACTION_NAMES = (
    "discover_tokens",
    "inspect_token",
    "inspect_market",
    "inspect_wallet",
    "review_signals",
)


def _dump_model(value: Any) -> Any:
    dumper = getattr(value, "model_dump", None)
    if dumper is not None:
        return dumper(mode="json")
    return value


def _make_client() -> AveDataServiceClient:
    base_url = os.environ.get("AVE_DATA_SERVICE_URL", "http://127.0.0.1:8080")
    api_key = os.environ.get("AVE_API_KEY")
    timeout = float(os.environ.get("AVE_DATA_SERVICE_TIMEOUT", "10"))
    return AveDataServiceClient(base_url, api_key=api_key, timeout=timeout)


def _provider_mode_from_env() -> str:
    mode = os.environ.get("AVE_DATA_PROVIDER", "").strip().lower()
    if mode:
        return mode
    return "auto" if os.environ.get("AVE_API_KEY") else "mock"


class _ProviderClientShim:
    def __init__(self, provider: Any) -> None:
        self._provider = provider

    def __getattr__(self, name: str) -> Any:
        return getattr(self._provider, name)


def _build_client_from_env() -> AveDataServiceClient | _ProviderClientShim:
    mode = _provider_mode_from_env()
    if mode in {"mock", "auto"} and mode != "ave_rest" and mode != "real":
        from ot_skill_enterprise.service_entrypoints import build_ave_provider

        return _ProviderClientShim(build_ave_provider())
    return _make_client()


def _workspace_dir(workspace_dir: Path | None = None) -> Path:
    root = workspace_dir or Path(os.environ.get("WORKSPACE_DIR", Path.cwd()))
    root = root.expanduser().resolve()
    (root / "data").mkdir(parents=True, exist_ok=True)
    return root


def _artifact_ref(action_name: str, run_id: str, artifact_path: Path) -> dict[str, Any]:
    ref = ArtifactRef(
        artifact_id=f"{action_name}-{run_id}",
        kind="json",
        uri=str(artifact_path),
        label=f"{action_name} artifact",
        metadata={"subdir": "data", "provider_id": "ave"},
    )
    return _dump_model(ref)


def _build_summary(action_name: str, request: Any, response: Any) -> str:
    request_dump = _dump_model(request)
    response_dump = _dump_model(response)
    response_data = response_dump.get("data", {}) if isinstance(response_dump, dict) else {}
    if action_name == "discover_tokens":
        query = request_dump.get("query") or request_dump.get("chain") or request_dump.get("source")
        count = len(response_data.get("token_refs", [])) if isinstance(response_data, dict) else 0
        return f"discovered {count} token candidates for {query}" if query else f"discovered {count} token candidates"
    if action_name == "inspect_token":
        token = request_dump.get("token_ref", {}).get("identifier") if isinstance(request_dump.get("token_ref"), dict) else None
        return f"inspected token {token}" if token else "inspected token"
    if action_name == "inspect_market":
        token = request_dump.get("token_ref", {}).get("identifier") if isinstance(request_dump.get("token_ref"), dict) else None
        return f"inspected market activity for {token}" if token else "inspected market activity"
    if action_name == "inspect_wallet":
        wallet = request_dump.get("wallet")
        return f"inspected wallet {wallet}" if wallet else "inspected wallet"
    if action_name == "review_signals":
        chain = request_dump.get("chain")
        count = len(response_data.get("signals", [])) if isinstance(response_data, dict) else 0
        return f"reviewed {count} signals for {chain}" if chain else f"reviewed {count} signals"
    return f"{action_name} completed"


def _request_model(action_name: str, payload: dict[str, Any]) -> Any:
    if action_name == "discover_tokens":
        return DiscoverTokensRequest.model_validate(payload)
    if action_name == "inspect_token":
        token = payload.get("token") or payload.get("address")
        if token is None:
            raise ValueError("inspect_token requires token or address")
        return InspectTokenRequest.model_validate(
            {
                "token_ref": {"identifier": token, "chain": payload.get("chain")},
                "include_holders": payload.get("include_holders", True),
                "include_risk": payload.get("include_risk", True),
            }
        )
    if action_name == "inspect_market":
        token = payload.get("token") or payload.get("address")
        if token is None:
            raise ValueError("inspect_market requires token or address")
        request_payload: dict[str, Any] = {
            "token_ref": {"identifier": token, "chain": payload.get("chain")},
            "interval": payload.get("interval", "1h"),
            "window": payload.get("window", "24h"),
        }
        pair = payload.get("pair")
        if pair:
            request_payload["pair_ref"] = {"identifier": pair, "chain": payload.get("chain"), "pair_address": pair}
        return InspectMarketRequest.model_validate(request_payload)
    if action_name == "inspect_wallet":
        wallet = payload.get("wallet") or payload.get("wallet_address")
        if wallet is None:
            raise ValueError("inspect_wallet requires wallet")
        return InspectWalletRequest.model_validate(
            {
                "wallet": wallet,
                "chain": payload.get("chain"),
                "include_holdings": payload.get("include_holdings", True),
                "include_activity": payload.get("include_activity", True),
            }
        )
    if action_name == "review_signals":
        token = payload.get("token") or payload.get("token_ref")
        request_payload: dict[str, Any] = {
            "chain": payload.get("chain"),
            "limit": payload.get("limit", 20),
        }
        if isinstance(token, dict):
            request_payload["token_ref"] = token
        elif token is not None:
            request_payload["token_ref"] = {"identifier": token, "chain": payload.get("chain")}
        return ReviewSignalsRequest.model_validate(request_payload)
    raise ValueError(f"unsupported action: {action_name}")


def _call_client(client: AveDataServiceClient, action_name: str, request_model: Any) -> Any:
    method = getattr(client, action_name, None)
    if method is None:
        raise ValueError(f"client does not implement {action_name}")
    return method(request_model)


def _normalize_error(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, HttpClientError):
        details: dict[str, Any] = {}
        if exc.status_code is not None:
            details["status_code"] = exc.status_code
        if exc.body is not None:
            details["body"] = exc.body
        if exc.payload is not None:
            details["payload"] = exc.payload
        return {"code": "UPSTREAM_HTTP_ERROR", "message": str(exc), "details": details}
    if isinstance(exc, ValidationError):
        return {"code": "VALIDATION_ERROR", "message": str(exc), "details": {"errors": exc.errors()}}
    if isinstance(exc, ValueError):
        return {"code": "VALIDATION_ERROR", "message": str(exc), "details": {}}
    return {"code": "INTERNAL_ERROR", "message": str(exc), "details": {}}


@dataclass(slots=True)
class AveDataProviderAdapter:
    name: str = "ave"
    supported_actions: tuple[str, ...] = ACTION_NAMES
    client: AveDataServiceClient | None = None

    def run(
        self,
        action_name: str,
        payload: dict[str, Any],
        *,
        workspace_dir: Path | None = None,
        request_id: str | None = None,
    ) -> ProviderActionResult:
        if action_name not in ACTION_NAMES:
            raise ValueError(f"unsupported action: {action_name}")

        resolved_request_id = request_id or uuid4().hex
        workspace = _workspace_dir(workspace_dir)
        summary = f"{action_name} failed"
        response_dump: Any = None
        response_ok = False
        response_meta: dict[str, Any] = {"provider": "ave"}
        response_error: dict[str, Any] | None = None
        request_dump: Any = payload

        try:
            request_model = _request_model(action_name, payload)
            request_dump = _dump_model(request_model)
            client = self.client or _make_client()
            response = _call_client(client, action_name, request_model)
            summary = _build_summary(action_name, request_model, response)
            response_dump = _dump_model(response)
            response_ok = response_dump.get("ok", True) if isinstance(response_dump, dict) else True
            response_meta = response_dump.get("meta", response_meta) if isinstance(response_dump, dict) else response_meta
            response_meta = {"provider_id": "ave", **dict(response_meta)}
            response_error = response_dump.get("error") if isinstance(response_dump, dict) else None
            artifact_body = {
                "action": action_name,
                "request_id": resolved_request_id,
                "summary": summary,
                "request": request_dump,
                "response": response_dump,
                "provider_id": "ave",
                "generated_at": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as exc:  # pragma: no cover
            response_error = _normalize_error(exc)
            artifact_body = {
                "action": action_name,
                "request_id": resolved_request_id,
                "summary": summary,
                "request": request_dump,
                "response": response_dump,
                "error": response_error,
                "provider_id": "ave",
                "generated_at": datetime.now(timezone.utc).isoformat(),
            }

        artifact_path = workspace / "data" / f"{action_name}-{resolved_request_id}.json"
        artifact_path.write_text(json.dumps(artifact_body, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

        return ProviderActionResult(
            ok=bool(response_ok) if response_error is None else False,
            provider=self.name,
            action=action_name,
            request_id=resolved_request_id,
            summary=summary,
            request=request_dump if isinstance(request_dump, dict) else {"value": request_dump},
            response=response_dump if isinstance(response_dump, dict) else {},
            error=response_error,
            meta=response_meta,
            artifacts=[_artifact_ref(action_name, resolved_request_id, artifact_path)],
        )


def build_ave_provider_adapter(*, client: AveDataServiceClient | None = None) -> AveDataProviderAdapter:
    return AveDataProviderAdapter(client=client or _build_client_from_env())


def run_provider_action(
    action_name: str,
    payload: dict[str, Any],
    *,
    client: AveDataServiceClient | None = None,
    workspace_dir: Path | str | None = None,
) -> dict[str, Any]:
    adapter = build_ave_provider_adapter(client=client)
    result = adapter.run(
        action_name,
        payload,
        workspace_dir=Path(workspace_dir) if workspace_dir is not None else None,
    )
    return result.as_dict()
