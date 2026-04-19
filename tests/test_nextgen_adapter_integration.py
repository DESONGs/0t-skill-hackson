from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from ot_skill_enterprise.chain_assets import chain_benchmark_defaults
from ot_skill_enterprise.nextgen.adapters import build_builtin_adapter_registry
from ot_skill_enterprise.nextgen.adapters.models import AdapterCapability, AdapterManifest
from ot_skill_enterprise.nextgen.adapters.registry import AdapterRegistry
from ot_skill_enterprise.nextgen.execution_dispatch import execute_skill_action
from ot_skill_enterprise.nextgen.provider_compat import build_provider_compat
from ot_skill_enterprise.providers.ave.adapter import build_ave_provider_adapter
from ot_skill_enterprise.shared.contracts import InspectWalletRequest
from ot_skill_enterprise.skills_compiler.compiler import SkillPackageCompiler
from ot_skill_enterprise.skills_compiler.models import SkillCandidate
from ot_skill_enterprise.style_distillation.service import (
    WalletStyleDistillationService,
    build_wallet_style_distillation_service,
)


class _FakeDataSourceAdapter:
    manifest = AdapterManifest(
        adapter_id="fake-data",
        adapter_type="data_source",
        adapter_version="1.0.0",
        title="Fake Data Adapter",
        summary="Test adapter for provider compatibility.",
        capabilities=(
            AdapterCapability(
                capability_id="wallet_profile",
                display_name="Wallet Profile",
                metadata={"legacy_action": "inspect_wallet"},
            ),
        ),
    )

    def supports_capability(self, capability_id: str) -> bool:
        return capability_id == "wallet_profile"

    def describe(self) -> dict[str, object]:
        return self.manifest.as_dict()

    def invoke(
        self,
        capability_id: str,
        payload: dict[str, object],
        *,
        workspace_dir: Path | None = None,
        request_id: str | None = None,
    ) -> dict[str, object]:
        assert capability_id == "wallet_profile"
        assert workspace_dir is not None
        return {
            "ok": True,
            "request_id": request_id or "req-fake",
            "response": {
                "ok": True,
                "data": {
                    "wallet_summary": {
                        "wallet_address": payload["wallet"],
                        "chain": payload["chain"],
                        "balance_usd": 1250.0,
                    },
                    "holdings": [],
                    "recent_activity": [{"tx_hash": "0x1"}],
                },
                "meta": {
                    "provider": "fake-data",
                    "request_id": request_id or "req-fake",
                    "cached": False,
                    "metadata": {"from_adapter": "yes"},
                },
            },
        }


class _FakeDataSourceAdapterNoProviderMeta(_FakeDataSourceAdapter):
    manifest = AdapterManifest(
        adapter_id="fake-source-only",
        adapter_type="data_source",
        adapter_version="1.0.0",
        title="Fake Source Only Adapter",
        summary="Test adapter without provider metadata in response envelopes.",
        capabilities=(
            AdapterCapability(
                capability_id="wallet_profile",
                display_name="Wallet Profile",
                metadata={"legacy_action": "inspect_wallet"},
            ),
        ),
    )

    def invoke(
        self,
        capability_id: str,
        payload: dict[str, object],
        *,
        workspace_dir: Path | None = None,
        request_id: str | None = None,
    ) -> dict[str, object]:
        assert capability_id == "wallet_profile"
        assert workspace_dir is not None
        return {
            "ok": True,
            "request_id": request_id or "req-source-only",
            "response": {
                "ok": True,
                "data": {
                    "wallet_summary": {
                        "wallet_address": payload["wallet"],
                        "chain": payload["chain"],
                    },
                    "holdings": [],
                    "recent_activity": [],
                },
                "meta": {
                    "request_id": request_id or "req-source-only",
                    "cached": False,
                    "metadata": {"from_adapter": "yes"},
                },
            },
        }


class _FakeExecutionAdapter:
    manifest = AdapterManifest(
        adapter_id="fake-execution",
        adapter_type="execution",
        adapter_version="1.0.0",
        title="Fake Execution Adapter",
        summary="Test adapter for dispatch integration.",
        capabilities=(
            AdapterCapability(capability_id="dry_run", display_name="Dry Run"),
            AdapterCapability(capability_id="live_run", display_name="Live Run"),
        ),
    )

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def supports_capability(self, capability_id: str) -> bool:
        return capability_id in {"dry_run", "live_run"}

    def describe(self) -> dict[str, object]:
        return self.manifest.as_dict()

    def invoke(
        self,
        capability_id: str,
        payload: dict[str, object],
        *,
        workspace_dir: Path | None = None,
        request_id: str | None = None,
    ) -> dict[str, object]:
        self.calls.append(
            {
                "capability_id": capability_id,
                "payload": dict(payload),
                "workspace_dir": workspace_dir,
                "request_id": request_id,
            }
        )
        return {"ok": True, "mode": capability_id, "adapter": "fake-execution"}


def _candidate() -> SkillCandidate:
    defaults = chain_benchmark_defaults("bsc")
    return SkillCandidate(
        candidate_id="candidate-nextgen-integration-1",
        candidate_slug="wallet-style-nextgen-integration",
        runtime_session_id="session-nextgen-integration-1",
        source_run_id="run-nextgen-1",
        source_evaluation_id="eval-nextgen-1",
        candidate_type="script",
        target_skill_name="Nextgen Integration Wallet Style",
        target_skill_kind="wallet_style",
        change_summary="Wallet style package routed through nextgen execution dispatch.",
        generation_spec={
            "wallet_style_profile": {
                "wallet": "0xabc1230000000000000000000000000000000000",
                "chain": "bsc",
                "style_label": "integration-wallet",
            },
            "strategy_spec": {
                "summary": "Minimal strategy for compiler integration coverage.",
                "entry_conditions": [],
                "metadata": {},
            },
            "execution_intent": {
                "adapter": "onchainos_cli",
                "mode": "dry_run_ready",
                "preferred_workflow": "swap_execute",
                "metadata": {
                    "chain": "bsc",
                    "default_source_token": "USDT",
                    "default_source_token_address": defaults["default_source_token_address"],
                    "default_source_unit_price_usd": defaults["default_source_unit_price_usd"],
                },
            },
        },
        metadata={"skill_family": "wallet_style", "wallet_address": "0xabc1230000000000000000000000000000000000", "chain": "bsc"},
    )


def test_provider_compat_unwraps_adapter_result_into_legacy_wallet_shape(tmp_path: Path) -> None:
    registry = AdapterRegistry()
    registry.register(_FakeDataSourceAdapter(), default=True)
    compat = build_provider_compat(workspace_dir=tmp_path, adapter_registry=registry, adapter_id="fake-data")

    payload = compat.inspect_wallet(
        InspectWalletRequest(
            wallet="0xabc1230000000000000000000000000000000000",
            chain="bsc",
            include_holdings=True,
            include_activity=True,
            activity_pages=3,
            recent_activity_limit=20,
        )
    )

    assert payload["wallet_summary"]["wallet_address"] == "0xabc1230000000000000000000000000000000000"
    assert payload["wallet_summary"]["chain"] == "bsc"
    assert payload["full_activity_history"] == payload["recent_activity"]
    assert payload["fetch_metadata"]["activity_pages_fetched"] == 3
    assert payload["source_meta"]["provider"] == "fake-data"


def test_builtin_ave_provider_adapter_uses_mock_provider_without_http_service(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("AVE_DATA_PROVIDER", "mock")
    adapter = build_ave_provider_adapter()

    payload = adapter.run(
        "inspect_wallet",
        {"wallet": "0xabc1230000000000000000000000000000000000", "chain": "solana"},
        workspace_dir=tmp_path,
    ).as_dict()

    assert payload["ok"] is True
    assert payload["response"]["wallet_summary"]["wallet_address"] == "0xabc1230000000000000000000000000000000000"
    assert payload["response"]["source_meta"]["provider"] == "mock"
    assert any(tmp_path.joinpath("data").glob("inspect_wallet-*.json"))


def test_wallet_style_service_accepts_data_source_adapter_provider(tmp_path: Path) -> None:
    service = WalletStyleDistillationService(
        project_root=Path(__file__).resolve().parents[1],
        workspace_root=tmp_path,
        provider=_FakeDataSourceAdapter(),
    )

    payload = service.provider.inspect_wallet(
        InspectWalletRequest(
            wallet="0xabc1230000000000000000000000000000000000",
            chain="bsc",
            include_holdings=True,
            include_activity=True,
        )
    )

    assert payload["wallet_summary"]["balance_usd"] == 1250.0
    assert payload["source_meta"]["provider"] == "fake-data"


def test_wallet_style_service_can_select_data_source_adapter_without_defaulting_ave(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    registry = AdapterRegistry()
    registry.register(_FakeDataSourceAdapter(), default=True)

    def _explode() -> object:
        raise AssertionError("legacy AVE provider should not be constructed when adapter_id is explicit")

    monkeypatch.setattr("ot_skill_enterprise.style_distillation.service.build_ave_provider", _explode)

    service = WalletStyleDistillationService(
        project_root=Path(__file__).resolve().parents[1],
        workspace_root=tmp_path,
        provider=None,
        adapter_registry=registry,
        data_source_adapter_id="fake-data",
    )

    payload = service.provider.inspect_wallet(
        InspectWalletRequest(
            wallet="0xabc1230000000000000000000000000000000000",
            chain="bsc",
            include_holdings=True,
            include_activity=True,
        )
    )

    assert service.data_source_adapter_id == "fake-data"
    assert payload["source_meta"]["provider"] == "fake-data"


def test_nextgen_distillation_requires_explicit_data_source_adapter(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="explicit data_source_adapter_id or provider"):
        WalletStyleDistillationService(
            project_root=Path(__file__).resolve().parents[1],
            workspace_root=tmp_path,
            provider=None,
            adapter_registry=AdapterRegistry(),
            require_explicit_data_source_adapter=True,
            allow_builtin_adapter_registry_fallback=False,
        )


def test_nextgen_distillation_does_not_implicitly_pick_registry_default_adapter(tmp_path: Path) -> None:
    registry = AdapterRegistry()
    registry.register(_FakeDataSourceAdapter(), default=True)

    with pytest.raises(ValueError, match="explicit data_source_adapter_id or provider"):
        WalletStyleDistillationService(
            project_root=Path(__file__).resolve().parents[1],
            workspace_root=tmp_path,
            provider=None,
            adapter_registry=registry,
            require_explicit_data_source_adapter=True,
            allow_builtin_adapter_registry_fallback=False,
        )


def test_wallet_style_service_requires_registry_for_explicit_data_source_adapter(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="adapter_registry injection"):
        WalletStyleDistillationService(
            project_root=Path(__file__).resolve().parents[1],
            workspace_root=tmp_path,
            provider=None,
            data_source_adapter_id="ave",
        )


def test_provider_compat_uses_adapter_identity_when_response_meta_omits_provider(tmp_path: Path) -> None:
    registry = AdapterRegistry()
    registry.register(_FakeDataSourceAdapterNoProviderMeta(), default=True)
    compat = build_provider_compat(
        workspace_dir=tmp_path,
        adapter_registry=registry,
        adapter_id="fake-source-only",
        allow_builtin_registry_fallback=False,
    )

    payload = compat.inspect_wallet(
        InspectWalletRequest(
            wallet="0xabc1230000000000000000000000000000000000",
            chain="bsc",
            include_holdings=True,
            include_activity=True,
        )
    )

    assert payload["source_meta"]["provider"] == "fake-source-only"


def test_nextgen_explicit_adapter_distillation_runs_under_mock_provider(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("AVE_DATA_PROVIDER", "mock")
    monkeypatch.setenv("OT_PI_REFLECTION_MOCK", "1")
    monkeypatch.setenv("AVE_USE_DOCKER", "false")
    project_root = Path(__file__).resolve().parents[1]
    registry = build_builtin_adapter_registry(project_root=project_root)
    service = build_wallet_style_distillation_service(
        project_root=project_root,
        workspace_root=tmp_path,
        adapter_registry=registry,
        data_source_adapter_id="ave",
        require_explicit_data_source_adapter=True,
        allow_builtin_adapter_registry_fallback=False,
    )

    result = service.distill_wallet_style(wallet="0xverifywallet0001", chain="solana", max_attempts=1)

    assert result["wallet"] == "0xverifywallet0001"
    assert result["chain"] == "solana"
    assert result["reflection_status"] == "succeeded"


def test_worker_runtime_requires_explicit_or_workspace_derived_data_source_adapter(tmp_path: Path) -> None:
    from ot_skill_enterprise.nextgen.worker_bridge.runtime import (
        WorkerBridgeInvocationRequest,
        WorkflowWorkerRuntime,
    )
    from ot_skill_enterprise.nextgen.workflows.models import WorkflowRunRequest

    runtime = WorkflowWorkerRuntime(
        project_root=Path(__file__).resolve().parents[1],
        workspace_root=tmp_path,
        adapter_registry=AdapterRegistry(),
    )

    request = WorkerBridgeInvocationRequest(
        bridge_id="worker-runtime-test",
        bridge_version="1.0.0",
        action_id="distillation.execute",
        workflow_id="distillation_seed",
        workflow_step_id="distill_baseline",
        request=WorkflowRunRequest(
            workflow_id="distillation_seed",
            workspace_id="desk-alpha",
            wallet="0xabc1230000000000000000000000000000000000",
            chain="bsc",
            workspace_dir=str(tmp_path),
        ),
    )

    with pytest.raises(ValueError, match="explicit or workspace-derived data_source_adapter_id"):
        runtime.invoke(request)


def test_worker_runtime_accepts_workspace_adapter_metadata_for_distillation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from ot_skill_enterprise.nextgen.worker_bridge.runtime import (
        WorkerBridgeInvocationRequest,
        WorkflowWorkerRuntime,
    )
    from ot_skill_enterprise.nextgen.workflows.models import WorkflowRunRequest

    captured: dict[str, object] = {}

    class _FakeRuntimeDistillationService:
        def distill_wallet_style(self, *, wallet: str, chain: str | None = None, skill_name: str | None = None, **_: object) -> dict[str, object]:
            return {
                "job_id": "job-runtime-001",
                "wallet": wallet,
                "chain": chain or "bsc",
                "profile": {"wallet": wallet, "chain": chain or "bsc", "preferred_tokens": ["SOL"]},
                "strategy": {"summary": "runtime test strategy", "entry_conditions": [], "risk_controls": []},
                "execution_intent": {"adapter": "fake-execution", "mode": "review", "preflight_checks": ["allowlist"]},
                "package": {"package_id": "package-runtime-001"},
                "summary": {"summary": "runtime distillation completed"},
            }

    def _build_service(**kwargs):  # type: ignore[no-untyped-def]
        captured["data_source_adapter_id"] = kwargs.get("data_source_adapter_id")
        return _FakeRuntimeDistillationService()

    monkeypatch.setattr(
        "ot_skill_enterprise.nextgen.worker_bridge.runtime.build_wallet_style_distillation_service",
        _build_service,
    )

    runtime = WorkflowWorkerRuntime(
        project_root=Path(__file__).resolve().parents[1],
        workspace_root=tmp_path,
        adapter_registry=AdapterRegistry(),
    )

    request = WorkerBridgeInvocationRequest(
        bridge_id="worker-runtime-test",
        bridge_version="1.0.0",
        action_id="distillation.execute",
        workflow_id="distillation_seed",
        workflow_step_id="distill_baseline",
        request=WorkflowRunRequest(
            workflow_id="distillation_seed",
            workspace_id="desk-alpha",
            wallet="0xabc1230000000000000000000000000000000000",
            chain="bsc",
            workspace_dir=str(tmp_path),
            metadata={"workspace_adapters": {"data_source": "fake-data"}},
        ),
    )

    response = runtime.invoke(request)

    assert captured["data_source_adapter_id"] == "fake-data"
    assert response.outputs["baseline_variant"]["variant_id"] == "baseline"
    assert response.state_patch["adapter_ids"]["data_source"] == "fake-data"


def test_style_distillation_service_import_does_not_trigger_nextgen_cycle(tmp_path: Path) -> None:
    env = dict(os.environ)
    project_root = Path(__file__).resolve().parents[1]
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        f"{project_root / 'src'}:{existing_pythonpath}" if existing_pythonpath else str(project_root / "src")
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from ot_skill_enterprise.style_distillation.service import WalletStyleDistillationService; "
            "print(WalletStyleDistillationService.__name__)",
        ],
        cwd=project_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "WalletStyleDistillationService"


def test_execution_dispatch_resolves_adapter_from_execution_intent() -> None:
    registry = AdapterRegistry()
    adapter = _FakeExecutionAdapter()
    registry.register(adapter, default=True)

    result = execute_skill_action(
        "dry_run",
        {"legs": [{"token": "WBNB"}]},
        {"adapter": "fake-execution", "mode": "dry_run"},
        project_root=Path.cwd(),
        env={"OKX_API_KEY": "test"},
        adapter_registry=registry,
    )

    assert result == {"ok": True, "mode": "dry_run", "adapter": "fake-execution"}
    assert adapter.calls[0]["capability_id"] == "dry_run"
    assert adapter.calls[0]["payload"]["trade_plan"] == {"legs": [{"token": "WBNB"}]}


def test_onchainos_prepare_execution_skips_ave_wss_for_nextgen_injected_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ot_skill_enterprise.execution import onchainos_cli as execution_mod

    def _explode(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("AVE WSS should not be used when nextgen context is injected")

    monkeypatch.setattr(execution_mod, "_collect_ave_wss_price_snapshot", _explode)
    monkeypatch.setattr(
        execution_mod,
        "_resolve_cli_invocation",
        lambda project_root=None: (["onchainos"], {"resolved": True, "source": "test", "path": "onchainos"}),
    )

    prepared = execution_mod.prepare_execution(
        {
            "wallet_address": "0xabc1230000000000000000000000000000000000",
            "chain": "bsc",
            "requested_target_token": "PEPE",
            "target_token_resolution": "market_search_pending",
            "execution_source_symbol": "USDT",
            "execution_source_address": "0x55d398326f99059ff775485246999027b3197955",
            "execution_source_readable_amount": 25.0,
            "market_discovery": {"enabled": True, "wss_price_enabled": True},
            "market_context": {
                "focus_token_context": [
                    {
                        "symbol": "PEPE",
                        "token_address": "0x00000000000000000000000000000000000000aa",
                        "price_usd": 0.25,
                        "liquidity_usd": 100000.0,
                    }
                ]
            },
        },
        {
            "adapter": "onchainos_cli",
            "mode": "dry_run",
            "preferred_workflow": "swap_execute",
            "preflight_checks": (),
            "route_preferences": (),
            "metadata": {
                "adapter_runtime": "nextgen_spi",
                "execution_context_source": "adapter_injected",
                "market_context_injected": True,
                "price_context_injected": True,
            },
        },
    )

    assert prepared["target_token_address"] == "0x00000000000000000000000000000000000000aa"
    assert prepared["market_discovery_meta"]["wss_price"] == {"reason": "disabled_for_nextgen_injected_context"}
    assert prepared["market_discovery_meta"]["wss_price_used"] is False
    assert prepared["market_discovery_meta"]["legacy_market_discovery_ignored"] is True


def test_onchainos_prepare_execution_ignores_explicit_legacy_ave_fallback_for_nextgen_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ot_skill_enterprise.execution import onchainos_cli as execution_mod

    def _explode(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("nextgen execution should not reopen legacy AVE fallback")

    monkeypatch.setattr(execution_mod, "_collect_ave_wss_price_snapshot", _explode)
    monkeypatch.setattr(
        execution_mod,
        "_resolve_cli_invocation",
        lambda project_root=None: (["onchainos"], {"resolved": True, "source": "test", "path": "onchainos"}),
    )

    prepared = execution_mod.prepare_execution(
        {
            "wallet_address": "0xabc1230000000000000000000000000000000000",
            "chain": "bsc",
            "requested_target_token": "PEPE",
            "execution_source_symbol": "USDT",
            "execution_source_address": "0x55d398326f99059ff775485246999027b3197955",
            "execution_source_readable_amount": 25.0,
            "market_context": {
                "focus_token_context": [
                    {
                        "symbol": "PEPE",
                        "token_address": "0x00000000000000000000000000000000000000aa",
                        "price_usd": 0.25,
                        "liquidity_usd": 100000.0,
                    }
                ]
            },
        },
        {
            "adapter": "onchainos_cli",
            "mode": "dry_run",
            "preferred_workflow": "swap_execute",
            "preflight_checks": (),
            "route_preferences": (),
            "metadata": {
                "adapter_runtime": "nextgen_spi",
                "execution_context_source": "adapter_injected",
                "market_context_injected": True,
                "price_context_injected": True,
                "allow_legacy_ave_wss_fallback": True,
            },
        },
    )

    assert prepared["market_discovery_meta"]["legacy_ave_wss_fallback_allowed"] is False
    assert prepared["market_discovery_meta"]["legacy_ave_wss_fallback_ignored"] is True
    assert prepared["market_discovery_meta"]["wss_price"] == {"reason": "disabled_for_nextgen_injected_context"}


def test_wallet_style_compiler_emits_execute_script_using_dispatch() -> None:
    compiler = SkillPackageCompiler(
        project_root=Path(__file__).resolve().parents[1],
        workspace_root=Path(__file__).resolve().parents[1] / ".ot-workspace",
    )

    with TemporaryDirectory() as tmpdir:
        package_root = Path(tmpdir) / "wallet-style-package"
        compiler.compile(_candidate(), output_root=package_root, package_kind="script")
        execute_script = (package_root / "scripts" / "execute.py").read_text(encoding="utf-8")

    assert "from ot_skill_enterprise.nextgen.execution_dispatch import execute_skill_action" in execute_script
    assert "result = execute_skill_action(mode, trade_plan, execution_intent, project_root=project_root)" in execute_script
    assert "result = execute_skill_action('live', trade_plan, live_intent, project_root=project_root)" in execute_script
    assert "metadata['adapter_runtime'] = 'nextgen_spi'" in execute_script
    assert "trade_plan['market_context'] = dict(market_context)" in execute_script
    assert "allow_legacy_ave_wss_fallback" not in execute_script
