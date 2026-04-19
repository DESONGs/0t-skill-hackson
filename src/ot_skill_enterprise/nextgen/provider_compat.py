from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ot_skill_enterprise.shared.contracts import (
    DiscoverTokensRequest,
    InspectMarketRequest,
    InspectTokenRequest,
    InspectWalletRequest,
    ReviewSignalsRequest,
)

from .adapters.builtin import build_builtin_adapter_registry
from .adapters.models import DataSourceAdapter
from .adapters.registry import AdapterRegistration, AdapterRegistry


def _dump_request(value: Any) -> dict[str, Any]:
    dumper = getattr(value, "model_dump", None)
    if callable(dumper):
        return dumper(mode="json", exclude_none=True)
    if isinstance(value, Mapping):
        return dict(value)
    raise TypeError(f"unsupported request payload: {type(value)!r}")


def _resolve_provider_identity(
    result: Mapping[str, Any],
    payload: Mapping[str, Any],
    *,
    adapter_id: str | None = None,
    registration: AdapterRegistration | None = None,
) -> str:
    if adapter_id:
        return str(adapter_id)
    if registration is not None:
        return str(registration.manifest.adapter_id)
    provider_hint = payload.get("provider") or payload.get("adapter_id")
    if provider_hint:
        return str(provider_hint)
    metadata = payload.get("metadata")
    if isinstance(metadata, Mapping):
        provider_hint = metadata.get("provider") or metadata.get("adapter_id")
        if provider_hint:
            return str(provider_hint)
    if isinstance(result.get("adapter_id"), str):
        return str(result["adapter_id"])
    return "unknown"


def _source_meta_from_envelope(
    result: Mapping[str, Any],
    payload: Mapping[str, Any],
    *,
    adapter_id: str | None = None,
    registration: AdapterRegistration | None = None,
) -> dict[str, Any]:
    if "adapter_id" in result and isinstance(result.get("result"), Mapping):
        return _source_meta_from_envelope(
            result["result"],
            payload,
            adapter_id=str(result.get("adapter_id") or adapter_id or ""),
            registration=registration,
        )
    response = result.get("response")
    if isinstance(response, Mapping):
        data = response.get("data")
        if isinstance(data, Mapping):
            source_meta = data.get("source_meta")
            if isinstance(source_meta, Mapping):
                return dict(source_meta)
        meta = response.get("meta")
        if isinstance(meta, Mapping):
            return {
                "provider": meta.get("provider")
                or _resolve_provider_identity(result, payload, adapter_id=adapter_id, registration=registration),
                "request_id": meta.get("request_id"),
                "fetched_at": meta.get("timestamp"),
                "cached": bool(meta.get("cached", False)),
                "source_version": meta.get("source"),
                "metadata": dict(meta.get("metadata") or {}),
            }
    return {
        "provider": _resolve_provider_identity(result, payload, adapter_id=adapter_id, registration=registration),
        "request_id": result.get("request_id"),
        "metadata": dict(payload.get("metadata") or {}),
    }


def _unwrap_adapter_result(
    capability_id: str,
    request_payload: dict[str, Any],
    result: Mapping[str, Any],
    *,
    adapter_id: str | None = None,
    registration: AdapterRegistration | None = None,
) -> dict[str, Any]:
    if "adapter_id" in result and isinstance(result.get("result"), Mapping):
        result = result["result"]
    if not result.get("ok", True):
        raise RuntimeError(str(result.get("summary") or f"{capability_id} failed"))
    response = result.get("response")
    if not isinstance(response, Mapping):
        return dict(result)
    data = response.get("data")
    if not isinstance(data, Mapping):
        return dict(response)
    payload = dict(data)
    payload.setdefault(
        "source_meta",
        _source_meta_from_envelope(result, payload, adapter_id=adapter_id, registration=registration),
    )
    if capability_id in {"wallet_profile", "wallet_trades"}:
        wallet_summary = dict(payload.get("wallet_summary") or {})
        wallet_summary.setdefault("wallet_address", request_payload.get("wallet"))
        wallet_summary.setdefault("chain", request_payload.get("chain"))
        payload["wallet_summary"] = wallet_summary
        payload.setdefault("holdings", [])
        payload.setdefault("recent_activity", [])
        payload.setdefault("full_activity_history", list(payload.get("recent_activity") or []))
        payload.setdefault(
            "fetch_metadata",
            {
                "activity_pages_fetched": int(request_payload.get("activity_pages") or 1),
                "recent_activity_limit": int(request_payload.get("recent_activity_limit") or 20),
                "adapter_compat": True,
            },
        )
    elif capability_id == "token_metadata":
        payload.setdefault("identity", dict(request_payload.get("token_ref") or {}))
        payload.setdefault("market_snapshot", {})
        payload.setdefault("risk_snapshot", {})
        payload.setdefault("holder_snapshot", {})
    elif capability_id == "market_context":
        payload.setdefault("selected_pair", None)
        payload.setdefault("ohlcv", [])
        payload.setdefault("recent_swaps", [])
        payload.setdefault("flow_summary", {})
    elif capability_id == "signal_context":
        payload.setdefault("signals", [])
        payload.setdefault("linked_token_refs", [])
    elif capability_id == "research_dataset":
        payload.setdefault("token_refs", [])
        payload.setdefault("ranking_context", {})
    return payload


@dataclass(slots=True)
class DataSourceProviderCompat:
    workspace_dir: Path
    legacy_provider: Any | None = None
    adapter: DataSourceAdapter | None = None
    adapter_registry: AdapterRegistry | None = None
    adapter_id: str | None = None
    allow_builtin_registry_fallback: bool = True

    def _resolve_registration(self, capability_id: str) -> AdapterRegistration:
        if self.adapter is not None:
            if not self.adapter.supports_capability(capability_id):
                raise KeyError(f"adapter does not support capability: {capability_id}")
            return AdapterRegistration(
                manifest=self.adapter.manifest,
                adapter=self.adapter,
                default=False,
                source="injected",
            )
        if self.adapter_registry is None:
            if not self.allow_builtin_registry_fallback:
                raise RuntimeError("data source adapter registry injection is required for nextgen provider compatibility")
            registry = build_builtin_adapter_registry()
        else:
            registry = self.adapter_registry
        return registry.resolve_registration(
            "data_source",
            adapter_id=self.adapter_id,
            capability_id=capability_id,
        )

    def _adapter(self, capability_id: str) -> DataSourceAdapter:
        if self.adapter is not None:
            if not self.adapter.supports_capability(capability_id):
                raise KeyError(f"adapter does not support capability: {capability_id}")
            return self.adapter
        return self._resolve_registration(capability_id).adapter

    def _invoke(self, capability_id: str, payload: Any, legacy_method_name: str) -> dict[str, Any]:
        request_payload = _dump_request(payload)
        if self.legacy_provider is not None:
            method = getattr(self.legacy_provider, legacy_method_name)
            return dict(method(payload))
        registration = self._resolve_registration(capability_id)
        adapter = registration.adapter
        result = adapter.invoke(capability_id, request_payload, workspace_dir=self.workspace_dir)
        return _unwrap_adapter_result(
            capability_id,
            request_payload,
            result,
            adapter_id=registration.manifest.adapter_id,
            registration=registration,
        )

    def inspect_wallet(self, payload: InspectWalletRequest) -> dict[str, Any]:
        return self._invoke("wallet_profile", payload, "inspect_wallet")

    def inspect_token(self, payload: InspectTokenRequest) -> dict[str, Any]:
        return self._invoke("token_metadata", payload, "inspect_token")

    def inspect_market(self, payload: InspectMarketRequest) -> dict[str, Any]:
        return self._invoke("market_context", payload, "inspect_market")

    def review_signals(self, payload: ReviewSignalsRequest) -> dict[str, Any]:
        return self._invoke("signal_context", payload, "review_signals")

    def discover_tokens(self, payload: DiscoverTokensRequest) -> dict[str, Any]:
        return self._invoke("research_dataset", payload, "discover_tokens")


def build_provider_compat(
    *,
    workspace_dir: Path,
    provider: Any | None = None,
    adapter_registry: AdapterRegistry | None = None,
    adapter_id: str | None = None,
    allow_builtin_registry_fallback: bool = True,
) -> DataSourceProviderCompat:
    if provider is not None and hasattr(provider, "invoke") and hasattr(provider, "manifest"):
        return DataSourceProviderCompat(
            workspace_dir=workspace_dir,
            legacy_provider=None,
            adapter=provider,
            adapter_registry=adapter_registry,
            adapter_id=adapter_id or getattr(getattr(provider, "manifest", None), "adapter_id", None),
            allow_builtin_registry_fallback=allow_builtin_registry_fallback,
        )
    return DataSourceProviderCompat(
        workspace_dir=workspace_dir,
        legacy_provider=provider,
        adapter=None,
        adapter_registry=adapter_registry,
        adapter_id=adapter_id,
        allow_builtin_registry_fallback=allow_builtin_registry_fallback,
    )
