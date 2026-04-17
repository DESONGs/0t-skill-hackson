from __future__ import annotations

from pathlib import Path
from typing import Any

from .adapter import ACTION_NAMES, AveDataProviderAdapter, build_ave_provider_adapter
from ..compat.gateway import GatewayCompatRunner
from ..registry import ProviderRegistry


def build_ave_provider_registry(client: Any | None = None) -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register(
        build_ave_provider_adapter(client=client),
        provider_name="ave",
        actions=ACTION_NAMES,
        compat=True,
        tags=("ave", "data", "compat"),
    )
    return registry


def build_ave_gateway_runner(
    *,
    client: Any | None = None,
    workspace_dir: Path | None = None,
) -> GatewayCompatRunner:
    registry = build_ave_provider_registry(client=client)
    return GatewayCompatRunner(registry=registry, workspace_dir=workspace_dir)
