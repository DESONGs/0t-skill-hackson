from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ot_skill_enterprise.enterprise_bridge import EnterpriseBridge
from ot_skill_enterprise.service_locator import control_plane_root, project_root, src_root, workspace_root

from .flows import build_default_flow_registry


@dataclass(frozen=True)
class ControlPlaneBootstrap:
    project_root: Path
    src_root: Path
    control_plane_root: Path
    workspace_root: Path
    bridge: dict[str, Any]
    flow_templates: list[dict[str, Any]] = field(default_factory=list)
    runtime_notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_root": str(self.project_root),
            "src_root": str(self.src_root),
            "control_plane_root": str(self.control_plane_root),
            "workspace_root": str(self.workspace_root),
            "bridge": self.bridge,
            "flow_templates": list(self.flow_templates),
            "runtime_notes": list(self.runtime_notes),
        }


def build_control_plane_bootstrap(root: Path | None = None, *, workspace_dir: Path | None = None) -> ControlPlaneBootstrap:
    project = Path(root) if root is not None else project_root()
    resolved_workspace = Path(workspace_dir).resolve() if workspace_dir is not None else workspace_root(root=project)
    bridge = EnterpriseBridge.from_project_root(project)
    flow_registry = build_default_flow_registry()
    return ControlPlaneBootstrap(
        project_root=project,
        src_root=src_root(project),
        control_plane_root=control_plane_root(project),
        workspace_root=resolved_workspace,
        bridge=bridge.runtime_entrypoint(),
        flow_templates=[template.to_dict() for template in flow_registry.list()],
        runtime_notes=(
            "control-plane is a coordination layer, not a second execution engine",
            "runtime views are read from persisted run/session state",
            "AVE remains a provider adapter behind the control plane",
        ),
    )
