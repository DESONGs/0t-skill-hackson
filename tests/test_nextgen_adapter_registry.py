from __future__ import annotations

from pathlib import Path

import pytest

import ot_skill_enterprise.nextgen.adapters.builtin as builtin_mod
from ot_skill_enterprise.nextgen.adapters import (
    AdapterCapabilityError,
    AdapterRegistry,
    AdapterRegistryError,
    AdapterManifest,
    build_ave_data_source_adapter,
    build_builtin_adapter_registry,
    build_onchainos_execution_adapter,
)
from ot_skill_enterprise.providers.contracts import ProviderActionResult


class _FakeAveProvider:
    name = "ave"
    supported_actions = ()

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def run(
        self,
        action_name: str,
        payload: dict[str, object],
        *,
        workspace_dir: Path | None = None,
        request_id: str | None = None,
    ) -> ProviderActionResult:
        self.calls.append(
            {
                "action_name": action_name,
                "payload": dict(payload),
                "workspace_dir": workspace_dir,
                "request_id": request_id,
            }
        )
        return ProviderActionResult(
            ok=True,
            provider="ave",
            action=action_name,
            request_id=request_id or "req-1",
            summary=f"{action_name} ok",
            request=dict(payload),
            response={"ok": True, "data": {"action": action_name}},
            meta={"provider_id": "ave"},
        )


def test_builtin_registry_describes_current_adapter_surface() -> None:
    registry = build_builtin_adapter_registry()

    described = {item["adapter_id"]: item for item in registry.describe()}

    assert registry.defaults() == {"data_source": "ave", "execution": "onchainos_cli"}
    assert set(described) == {"ave", "onchainos_cli"}
    assert described["ave"]["adapter_type"] == "data_source"
    assert described["onchainos_cli"]["adapter_type"] == "execution"
    assert [item["capability_id"] for item in described["ave"]["capabilities"]] == [
        "wallet_profile",
        "wallet_trades",
        "token_metadata",
        "market_context",
        "research_dataset",
        "signal_context",
    ]
    assert [item["capability_id"] for item in described["onchainos_cli"]["capabilities"]] == [
        "execution_prepare",
        "execution_prepare_only",
        "dry_run",
        "live_run",
    ]
    assert described["ave"]["source"] == "protocol-builtin"
    assert described["onchainos_cli"]["source"] == "protocol-builtin"
    assert described["ave"]["capabilities"][0]["required_payload_keys"] == ["wallet|wallet_address"]
    assert "normalized_result_keys" in described["onchainos_cli"]["capabilities"][0]


def test_registry_resolves_default_adapters_by_capability() -> None:
    registry = build_builtin_adapter_registry()

    data_adapter = registry.resolve("data_source", capability_id="market_context")
    execution_adapter = registry.resolve("execution", required_capabilities=("dry_run", "live_run"))

    assert data_adapter.manifest.adapter_id == "ave"
    assert execution_adapter.manifest.adapter_id == "onchainos_cli"
    assert registry.capability_matrix("data_source")["market_context"] == ["ave"]


def test_builtin_registry_accepts_explicit_manifest_overrides() -> None:
    original_data_manifest = build_ave_data_source_adapter().manifest
    overridden_data_manifest = AdapterManifest(
        adapter_id=original_data_manifest.adapter_id,
        adapter_type=original_data_manifest.adapter_type,
        adapter_version=original_data_manifest.adapter_version,
        title="Override Data Adapter",
        summary=original_data_manifest.summary,
        capabilities=original_data_manifest.capabilities,
        tags=original_data_manifest.tags,
        wraps=original_data_manifest.wraps,
        is_builtin=original_data_manifest.is_builtin,
        workspace_compatibility=original_data_manifest.workspace_compatibility,
        metadata=dict(original_data_manifest.metadata),
    )
    original_execution_manifest = build_onchainos_execution_adapter().manifest
    overridden_execution_manifest = AdapterManifest(
        adapter_id=original_execution_manifest.adapter_id,
        adapter_type=original_execution_manifest.adapter_type,
        adapter_version=original_execution_manifest.adapter_version,
        title="Override Execution Adapter",
        summary=original_execution_manifest.summary,
        capabilities=original_execution_manifest.capabilities,
        tags=original_execution_manifest.tags,
        wraps=original_execution_manifest.wraps,
        is_builtin=original_execution_manifest.is_builtin,
        workspace_compatibility=original_execution_manifest.workspace_compatibility,
        metadata=dict(original_execution_manifest.metadata),
    )

    registry = build_builtin_adapter_registry(
        manifest_overrides={
            "ave": overridden_data_manifest,
            "onchainos_cli": overridden_execution_manifest,
        }
    )

    described = {item["adapter_id"]: item for item in registry.describe()}
    assert described["ave"]["title"] == "Override Data Adapter"
    assert described["onchainos_cli"]["title"] == "Override Execution Adapter"
    assert described["ave"]["source"] == "manifest-override"
    assert described["onchainos_cli"]["source"] == "manifest-override"


def test_registry_rejects_duplicate_registration() -> None:
    registry = AdapterRegistry()
    registry.register(build_ave_data_source_adapter(), default=True)

    with pytest.raises(AdapterRegistryError):
        registry.register(build_ave_data_source_adapter())


def test_ave_wrapper_routes_capability_to_legacy_action(tmp_path: Path) -> None:
    provider = _FakeAveProvider()
    adapter = build_ave_data_source_adapter(provider=provider)

    result = adapter.invoke(
        "wallet_trades",
        {"wallet": "0xabc", "chain": "bsc"},
        workspace_dir=tmp_path,
        request_id="req-42",
    )

    assert provider.calls == [
        {
            "action_name": "inspect_wallet",
            "payload": {"wallet": "0xabc", "chain": "bsc"},
            "workspace_dir": tmp_path,
            "request_id": "req-42",
        }
    ]
    assert result["adapter_id"] == "ave"
    assert result["capability_id"] == "wallet_trades"
    assert result["result"]["action"] == "inspect_wallet"
    assert result["request_id"] == "req-42"


def test_onchainos_wrapper_dispatches_to_current_execution_functions(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: dict[str, object] = {}

    def _fake_run_dry_run(
        trade_plan: dict[str, object],
        execution_intent: dict[str, object],
        *,
        project_root: Path | None = None,
        env: dict[str, str] | None = None,
        executor=None,
    ) -> dict[str, object]:
        del executor
        calls["trade_plan"] = trade_plan
        calls["execution_intent"] = execution_intent
        calls["project_root"] = project_root
        calls["env"] = env
        return {"ok": True, "mode": "dry_run"}

    monkeypatch.setattr(builtin_mod, "run_dry_run", _fake_run_dry_run)

    adapter = build_onchainos_execution_adapter()
    result = adapter.invoke(
        "dry_run",
        {
            "trade_plan": {"legs": [{"token": "WBNB"}]},
            "execution_intent": {"adapter": "onchainos_cli", "mode": "dry_run"},
            "project_root": str(tmp_path),
            "env": {"OKX_API_KEY": "test"},
        },
    )

    assert result["ok"] is True
    assert result["mode"] == "dry_run"
    assert result["adapter_id"] == "onchainos_cli"
    assert result["capability_id"] == "dry_run"
    assert result["metadata"]["wrapped_execution_provider"] == "onchainos_cli"
    assert calls["trade_plan"] == {"legs": [{"token": "WBNB"}]}
    assert calls["execution_intent"] == {"adapter": "onchainos_cli", "mode": "dry_run", "metadata": {}}
    assert calls["project_root"] == tmp_path.resolve()
    assert calls["env"] == {"OKX_API_KEY": "test"}


def test_onchainos_wrapper_injects_context_and_disables_legacy_ave_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def _fake_prepare_execution(
        trade_plan: dict[str, object],
        execution_intent: dict[str, object],
        *,
        project_root=None,
        env=None,
        executor=None,
    ) -> dict[str, object]:
        del project_root, env, executor
        captured["trade_plan"] = trade_plan
        captured["execution_intent"] = execution_intent
        return {"ok": True, "prepared_execution": {}, "trade_plan": trade_plan, "metadata": {}}

    monkeypatch.setattr(builtin_mod, "prepare_execution", _fake_prepare_execution)

    adapter = build_onchainos_execution_adapter()
    result = adapter.invoke(
        "execution_prepare",
        {
            "trade_plan": {"requested_target_token": "PEPE"},
            "execution_intent": {"adapter": "onchainos_cli", "mode": "dry_run", "metadata": {}},
            "market_context": {"focus_token_context": [{"symbol": "PEPE", "token_address": "0x00000000000000000000000000000000000000aa"}]},
            "price_context": {"target_market_snapshot": {"price_usd": 0.25}},
        },
    )

    assert result["adapter_id"] == "onchainos_cli"
    assert captured["trade_plan"] == {
        "requested_target_token": "PEPE",
        "market_context": {"focus_token_context": [{"symbol": "PEPE", "token_address": "0x00000000000000000000000000000000000000aa"}]},
        "target_market_snapshot": {"price_usd": 0.25},
    }
    assert captured["execution_intent"] == {
        "adapter": "onchainos_cli",
        "mode": "dry_run",
        "metadata": {
            "market_context_injected": True,
            "price_context_injected": True,
            "adapter_runtime": "nextgen_spi",
            "execution_context_source": "adapter_injected",
        },
    }


def test_wrapper_rejects_unknown_capability() -> None:
    adapter = build_onchainos_execution_adapter()

    with pytest.raises(AdapterCapabilityError):
        adapter.invoke("execution_status", {})
