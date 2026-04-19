from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from ot_skill_enterprise.execution.onchainos_cli import prepare_execution, prepare_only_result, run_dry_run, run_live
from ot_skill_enterprise.providers.ave.adapter import AveDataProviderAdapter, build_ave_provider_adapter
from ot_skill_enterprise.providers.contracts import ProviderActionResult

from .models import (
    AdapterCapability,
    AdapterCapabilityError,
    AdapterManifest,
    AdapterResultEnvelope,
    DataSourceAdapter,
    ExecutionAdapter,
)
from .registry import AdapterRegistry


_DATA_CAPABILITY_TO_ACTION = {
    "wallet_profile": "inspect_wallet",
    "wallet_trades": "inspect_wallet",
    "token_metadata": "inspect_token",
    "market_context": "inspect_market",
    "research_dataset": "discover_tokens",
    "signal_context": "review_signals",
}

_EXECUTION_CAPABILITY_TO_CALL = {
    "execution_prepare": "prepare_execution",
    "execution_prepare_only": "prepare_only_result",
    "dry_run": "run_dry_run",
    "live_run": "run_live",
}


def _result_as_dict(result: ProviderActionResult | dict[str, Any]) -> dict[str, Any]:
    dumper = getattr(result, "as_dict", None)
    if callable(dumper):
        return dumper()
    return dict(result)


def _required_payload(capability: AdapterCapability, payload: dict[str, Any]) -> None:
    missing: list[str] = []
    for item in capability.required_payload_keys:
        aliases = [part.strip() for part in str(item).split("|") if part.strip()]
        if aliases and any(payload.get(alias) is not None for alias in aliases):
            continue
        missing.append(item)
    if missing:
        raise AdapterCapabilityError(
            f"capability {capability.capability_id} requires payload keys: {', '.join(missing)}"
        )


def _project_root_from_payload(payload: dict[str, Any]) -> Path | None:
    raw = payload.get("project_root")
    if not raw:
        return None
    return Path(raw).expanduser().resolve()


AVE_DATA_MANIFEST = AdapterManifest(
    adapter_id="ave",
    adapter_type="data_source",
    adapter_version="1.0.0",
    title="AVE Data Source Adapter",
    summary="First-party wrapper exposing the current AVE data service through the nextgen data adapter SPI.",
    capabilities=(
        AdapterCapability(
            capability_id="wallet_profile",
            display_name="Wallet Profile",
            description="Resolve the normalized wallet profile and holdings snapshot through AVE inspect_wallet.",
            tags=("wallet", "profile"),
            required_payload_keys=("wallet|wallet_address",),
            optional_payload_keys=("chain", "include_holdings", "include_activity", "activity_pages", "recent_activity_limit"),
            normalized_result_keys=("wallet_summary", "holdings", "recent_activity", "source_meta"),
            metadata={"legacy_action": "inspect_wallet"},
        ),
        AdapterCapability(
            capability_id="wallet_trades",
            display_name="Wallet Trades",
            description="Resolve wallet activity samples through AVE inspect_wallet activity payloads.",
            tags=("wallet", "activity"),
            required_payload_keys=("wallet|wallet_address",),
            optional_payload_keys=("chain", "include_holdings", "include_activity", "activity_pages", "recent_activity_limit"),
            normalized_result_keys=("wallet_summary", "recent_activity", "full_activity_history", "fetch_metadata", "source_meta"),
            metadata={"legacy_action": "inspect_wallet"},
        ),
        AdapterCapability(
            capability_id="token_metadata",
            display_name="Token Metadata",
            description="Resolve token metadata, holders, and risk annotations through AVE inspect_token.",
            tags=("token", "metadata"),
            required_payload_keys=("token|token_ref|address",),
            optional_payload_keys=("chain", "include_holders", "include_risk"),
            normalized_result_keys=("identity", "market_snapshot", "risk_snapshot", "holder_snapshot", "source_meta"),
            metadata={"legacy_action": "inspect_token"},
        ),
        AdapterCapability(
            capability_id="market_context",
            display_name="Market Context",
            description="Resolve market and pair context through AVE inspect_market.",
            tags=("market", "context"),
            required_payload_keys=("token|token_ref|address",),
            optional_payload_keys=("pair", "pair_ref", "chain", "interval", "window"),
            normalized_result_keys=("selected_pair", "ohlcv", "recent_swaps", "flow_summary", "source_meta"),
            metadata={"legacy_action": "inspect_market"},
        ),
        AdapterCapability(
            capability_id="research_dataset",
            display_name="Research Dataset",
            description="Resolve token discovery datasets through AVE discover_tokens.",
            tags=("research", "dataset"),
            required_payload_keys=("query|source",),
            optional_payload_keys=("chain", "limit"),
            normalized_result_keys=("token_refs", "ranking_context", "source_meta"),
            metadata={"legacy_action": "discover_tokens"},
        ),
        AdapterCapability(
            capability_id="signal_context",
            display_name="Signal Context",
            description="Resolve signal review context through AVE review_signals.",
            tags=("signals", "review"),
            required_payload_keys=("chain",),
            optional_payload_keys=("limit", "token", "token_ref"),
            normalized_result_keys=("signals", "linked_token_refs", "source_meta"),
            metadata={"legacy_action": "review_signals"},
        ),
    ),
    tags=("builtin", "ave", "data"),
    wraps=("ot_skill_enterprise.providers.ave.adapter.AveDataProviderAdapter",),
    is_builtin=True,
    metadata={"current_provider_id": "ave"},
)


ONCHAINOS_EXECUTION_MANIFEST = AdapterManifest(
    adapter_id="onchainos_cli",
    adapter_type="execution",
    adapter_version="1.0.0",
    title="OnchainOS CLI Execution Adapter",
    summary="First-party wrapper exposing the current OnchainOS CLI execution flow through the nextgen execution adapter SPI.",
    capabilities=(
        AdapterCapability(
            capability_id="execution_prepare",
            display_name="Execution Prepare",
            description="Resolve trade plans, checks, and execution discovery through prepare_execution.",
            tags=("execution", "prepare"),
            required_payload_keys=("trade_plan", "execution_intent"),
            optional_payload_keys=("project_root", "env", "market_context", "price_context", "target_token_context", "market_stream_snapshot", "allow_legacy_ave_wss_fallback"),
            normalized_result_keys=("prepared_execution", "metadata", "trade_plan"),
            metadata={"legacy_call": "prepare_execution"},
        ),
        AdapterCapability(
            capability_id="execution_prepare_only",
            display_name="Execution Prepare Only",
            description="Resolve the existing prepare-only execution result without running a dry run or live trade.",
            tags=("execution", "prepare"),
            required_payload_keys=("trade_plan", "execution_intent"),
            optional_payload_keys=("project_root", "env", "market_context", "price_context", "target_token_context", "market_stream_snapshot", "allow_legacy_ave_wss_fallback"),
            normalized_result_keys=("prepared_execution", "metadata", "trade_plan"),
            metadata={"legacy_call": "prepare_only_result"},
        ),
        AdapterCapability(
            capability_id="dry_run",
            display_name="Dry Run",
            description="Execute the current dry-run path through the vendored OnchainOS CLI wrapper.",
            tags=("execution", "simulation"),
            required_payload_keys=("trade_plan", "execution_intent"),
            optional_payload_keys=("project_root", "env", "market_context", "price_context", "target_token_context", "market_stream_snapshot", "allow_legacy_ave_wss_fallback"),
            normalized_result_keys=("prepared_execution", "execution", "simulation_result", "metadata", "trade_plan"),
            metadata={"legacy_call": "run_dry_run"},
        ),
        AdapterCapability(
            capability_id="live_run",
            display_name="Live Run",
            description="Execute the current live path through the vendored OnchainOS CLI wrapper.",
            tags=("execution", "broadcast"),
            required_payload_keys=("trade_plan", "execution_intent"),
            optional_payload_keys=("project_root", "env", "market_context", "price_context", "target_token_context", "market_stream_snapshot", "allow_legacy_ave_wss_fallback"),
            normalized_result_keys=("prepared_execution", "execution", "broadcast_results", "metadata", "trade_plan"),
            metadata={"legacy_call": "run_live"},
        ),
    ),
    tags=("builtin", "onchainos", "execution"),
    wraps=("ot_skill_enterprise.execution.onchainos_cli",),
    is_builtin=True,
    metadata={"current_execution_provider_id": "onchainos_cli"},
)


@dataclass(slots=True)
class AveDataSourceAdapterWrapper(DataSourceAdapter):
    provider: AveDataProviderAdapter = field(default_factory=build_ave_provider_adapter)
    manifest: AdapterManifest = AVE_DATA_MANIFEST

    def supports_capability(self, capability_id: str) -> bool:
        return self.manifest.supports(capability_id)

    def describe(self) -> dict[str, Any]:
        payload = self.manifest.as_dict()
        payload["legacy_actions"] = dict(_DATA_CAPABILITY_TO_ACTION)
        return payload

    def invoke(
        self,
        capability_id: str,
        payload: dict[str, Any],
        *,
        workspace_dir: Path | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        capability = self.manifest.capability(capability_id)
        _required_payload(capability, payload)
        action_name = _DATA_CAPABILITY_TO_ACTION[capability_id]
        request_payload = dict(payload)
        provider_result = self.provider.run(
            action_name,
            request_payload,
            workspace_dir=workspace_dir,
            request_id=request_id,
        )
        result = _result_as_dict(provider_result)
        envelope = AdapterResultEnvelope(
            adapter_id=self.manifest.adapter_id,
            adapter_type=self.manifest.adapter_type,
            capability_id=capability_id,
            ok=bool(result.get("ok", False)),
            request_id=str(result.get("request_id") or request_id or ""),
            summary=str(result.get("summary") or ""),
            payload=request_payload,
            result=result,
            error=result.get("error") if isinstance(result.get("error"), dict) else None,
            artifacts=tuple(dict(item) for item in list(result.get("artifacts") or []) if isinstance(item, dict)),
            metadata={
                "wrapped_provider": getattr(self.provider, "name", "ave"),
                "normalized_result_keys": list(capability.normalized_result_keys),
            },
        )
        return envelope.as_dict()


def _normalize_execution_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    trade_plan = dict(payload.get("trade_plan") or {})
    execution_intent = dict(payload.get("execution_intent") or {})
    metadata = dict(execution_intent.get("metadata") or {})
    injected: dict[str, Any] = {}
    if isinstance(payload.get("market_context"), dict):
        trade_plan["market_context"] = dict(payload["market_context"])
        metadata["market_context_injected"] = True
        injected["market_context"] = dict(payload["market_context"])
    if isinstance(payload.get("target_token_context"), dict):
        trade_plan["target_token_context"] = dict(payload["target_token_context"])
        metadata["market_context_injected"] = True
        injected["target_token_context"] = dict(payload["target_token_context"])
    if isinstance(payload.get("price_context"), dict):
        price_context = dict(payload["price_context"])
        if isinstance(price_context.get("target_market_snapshot"), dict):
            trade_plan["target_market_snapshot"] = dict(price_context["target_market_snapshot"])
        else:
            trade_plan["target_market_snapshot"] = price_context
        metadata["price_context_injected"] = True
        injected["price_context"] = price_context
    if isinstance(payload.get("market_stream_snapshot"), dict):
        trade_plan["market_stream_snapshot"] = dict(payload["market_stream_snapshot"])
        metadata["price_context_injected"] = True
        injected["market_stream_snapshot"] = dict(payload["market_stream_snapshot"])
    if "allow_legacy_ave_wss_fallback" in payload:
        metadata["allow_legacy_ave_wss_fallback"] = bool(payload.get("allow_legacy_ave_wss_fallback"))
    if injected:
        metadata["adapter_runtime"] = "nextgen_spi"
        metadata["execution_context_source"] = "adapter_injected"
    execution_intent["metadata"] = metadata
    return trade_plan, execution_intent, injected


@dataclass(slots=True)
class OnchainOSExecutionAdapterWrapper(ExecutionAdapter):
    manifest: AdapterManifest = ONCHAINOS_EXECUTION_MANIFEST

    def supports_capability(self, capability_id: str) -> bool:
        return self.manifest.supports(capability_id)

    def describe(self) -> dict[str, Any]:
        payload = self.manifest.as_dict()
        payload["legacy_calls"] = dict(_EXECUTION_CAPABILITY_TO_CALL)
        return payload

    def invoke(
        self,
        capability_id: str,
        payload: dict[str, Any],
        *,
        workspace_dir: Path | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        del workspace_dir
        capability = self.manifest.capability(capability_id)
        _required_payload(capability, payload)
        trade_plan, execution_intent, injected_context = _normalize_execution_payload(payload)
        env = payload.get("env")
        project_root = _project_root_from_payload(payload)
        if capability_id == "execution_prepare":
            result = prepare_execution(trade_plan, execution_intent, project_root=project_root, env=env)
        elif capability_id == "execution_prepare_only":
            result = prepare_only_result(trade_plan, execution_intent, project_root=project_root, env=env)
        elif capability_id == "dry_run":
            result = run_dry_run(trade_plan, execution_intent, project_root=project_root, env=env)
        elif capability_id == "live_run":
            result = run_live(trade_plan, execution_intent, project_root=project_root, env=env)
        else:
            raise AdapterCapabilityError(f"unsupported execution capability: {capability_id}")
        envelope = AdapterResultEnvelope(
            adapter_id=self.manifest.adapter_id,
            adapter_type=self.manifest.adapter_type,
            capability_id=capability_id,
            ok=bool(result.get("ok", False)),
            request_id=str(request_id or ""),
            summary=str(result.get("summary") or ""),
            payload={
                "trade_plan": trade_plan,
                "execution_intent": execution_intent,
                **({"project_root": str(project_root)} if project_root is not None else {}),
            },
            result=dict(result),
            error=result.get("error") if isinstance(result.get("error"), dict) else None,
            metadata={
                "wrapped_execution_provider": "onchainos_cli",
                "normalized_result_keys": list(capability.normalized_result_keys),
                "injected_context": injected_context,
            },
        )
        normalized = envelope.as_dict()
        normalized["legacy_result"] = dict(result)
        normalized.update(dict(result))
        return normalized


def build_ave_data_source_adapter(
    *,
    provider: AveDataProviderAdapter | None = None,
    manifest: AdapterManifest | None = None,
) -> AveDataSourceAdapterWrapper:
    return AveDataSourceAdapterWrapper(
        manifest=manifest or AVE_DATA_MANIFEST,
        provider=provider or build_ave_provider_adapter(),
    )


def build_onchainos_execution_adapter(*, manifest: AdapterManifest | None = None) -> OnchainOSExecutionAdapterWrapper:
    return OnchainOSExecutionAdapterWrapper(manifest=manifest or ONCHAINOS_EXECUTION_MANIFEST)


def register_builtin_adapters(
    registry: AdapterRegistry | None = None,
    *,
    project_root: Path | None = None,
    manifest_overrides: Mapping[str, AdapterManifest] | None = None,
) -> AdapterRegistry:
    resolved = registry or AdapterRegistry()
    protocol_manifests: dict[str, AdapterManifest] = {}
    try:
        from ot_skill_enterprise.nextgen.protocol import build_adapter_manifests, load_nextgen_protocol_bundle

        protocol_manifests = build_adapter_manifests(load_nextgen_protocol_bundle(project_root=project_root))
    except Exception:
        protocol_manifests = {}
    if manifest_overrides:
        protocol_manifests = {**protocol_manifests, **dict(manifest_overrides)}
    resolved.register(
        build_ave_data_source_adapter(manifest=protocol_manifests.get("ave")),
        manifest_override=protocol_manifests.get("ave"),
        default=True,
        source=(
            "manifest-override"
            if manifest_overrides and "ave" in manifest_overrides
            else ("protocol-builtin" if "ave" in protocol_manifests else "builtin")
        ),
    )
    resolved.register(
        build_onchainos_execution_adapter(manifest=protocol_manifests.get("onchainos_cli")),
        manifest_override=protocol_manifests.get("onchainos_cli"),
        default=True,
        source=(
            "manifest-override"
            if manifest_overrides and "onchainos_cli" in manifest_overrides
            else ("protocol-builtin" if "onchainos_cli" in protocol_manifests else "builtin")
        ),
    )
    return resolved


def build_builtin_adapter_registry(
    *,
    project_root: Path | None = None,
    manifest_overrides: Mapping[str, AdapterManifest] | None = None,
) -> AdapterRegistry:
    return register_builtin_adapters(AdapterRegistry(), project_root=project_root, manifest_overrides=manifest_overrides)
