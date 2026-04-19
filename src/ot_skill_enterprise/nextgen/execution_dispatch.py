from __future__ import annotations

from pathlib import Path
from typing import Any

from .adapters.builtin import build_builtin_adapter_registry
from .adapters.models import ExecutionAdapter
from .adapters.registry import AdapterRegistry


_MODE_TO_CAPABILITY = {
    "prepare": "execution_prepare",
    "prepare_only": "execution_prepare_only",
    "dry_run": "dry_run",
    "live": "live_run",
}


def _resolve_execution_adapter(
    execution_intent: dict[str, Any],
    *,
    capability_id: str,
    adapter_registry: AdapterRegistry | None,
) -> ExecutionAdapter:
    registry = adapter_registry or build_builtin_adapter_registry()
    adapter_id = str(execution_intent.get("adapter") or "").strip() or None
    return registry.resolve("execution", adapter_id=adapter_id, capability_id=capability_id)


def execute_skill_action(
    mode: str,
    trade_plan: dict[str, Any],
    execution_intent: dict[str, Any],
    *,
    project_root: Path | None = None,
    env: dict[str, str] | None = None,
    adapter_registry: AdapterRegistry | None = None,
) -> dict[str, Any]:
    capability_id = _MODE_TO_CAPABILITY.get(str(mode or "").strip())
    if capability_id is None:
        raise ValueError(f"unsupported execution mode: {mode}")
    adapter = _resolve_execution_adapter(execution_intent, capability_id=capability_id, adapter_registry=adapter_registry)
    return adapter.invoke(
        capability_id,
        {
            "trade_plan": dict(trade_plan or {}),
            "execution_intent": dict(execution_intent or {}),
            "project_root": str(project_root.resolve()) if project_root is not None else None,
            "env": dict(env or {}),
        },
    )
