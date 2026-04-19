from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .adapters import AdapterRegistry, build_builtin_adapter_registry
from .plugins import WorkflowPluginRegistry, build_default_plugin_registry


def _default_project_root() -> Path:
    return Path(__file__).resolve().parents[3]


@dataclass(slots=True)
class NextArchitectureService:
    project_root: Path
    plugin_registry: WorkflowPluginRegistry
    adapter_registry: AdapterRegistry

    def overview(self) -> dict[str, Any]:
        plugin_registrations = self.plugin_registry.list_plugins()
        workflow_registrations = self.plugin_registry.list_workflows()
        adapter_registrations = self.adapter_registry.list_registrations()
        return {
            "status": "ready",
            "architecture_mode": "ts-kernel-default",
            "target_stack": {
                "kernel": "ts-pi-kernel",
                "worker_tier": "python-domain-workers",
            },
            "project_root": str(self.project_root),
            "counts": {
                "plugins": len(plugin_registrations),
                "workflows": len(workflow_registrations),
                "adapters": len(adapter_registrations),
            },
            "default_adapters": self.adapter_registry.defaults(),
            "plugin_ids": [item.plugin_id for item in plugin_registrations],
            "workflow_ids": [item.workflow_id for item in workflow_registrations],
            "adapter_ids": [item.manifest.adapter_id for item in adapter_registrations],
            "docs": {
                "bundle": "docs/architecture/next-architecture/README.md",
                "blueprint": "docs/architecture/next-architecture/target-system-blueprint.md",
                "boundary": "docs/architecture/next-architecture/kernel-and-stack-boundary.md",
                "workflow_model": "docs/architecture/next-architecture/plugin-workflow-model.md",
                "adapters": "docs/architecture/next-architecture/data-and-execution-adapters.md",
                "migration": "docs/architecture/next-architecture/migration-phases.md",
                "delivery": "docs/architecture/next-architecture/team-delivery-plan.md",
            },
        }

    def plugins(self) -> dict[str, Any]:
        return {
            "status": "ready",
            "registry": self.plugin_registry.describe(),
        }

    def adapters(self) -> dict[str, Any]:
        return {
            "status": "ready",
            "defaults": self.adapter_registry.defaults(),
            "registrations": self.adapter_registry.describe(),
            "capability_matrix": self.adapter_registry.capability_matrix(),
        }


def build_next_architecture_service(project_root: Path | None = None) -> NextArchitectureService:
    resolved_root = (project_root or _default_project_root()).expanduser().resolve()
    return NextArchitectureService(
        project_root=resolved_root,
        plugin_registry=build_default_plugin_registry(project_root=resolved_root),
        adapter_registry=build_builtin_adapter_registry(project_root=resolved_root),
    )
