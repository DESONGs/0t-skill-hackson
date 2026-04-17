from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ot_skill_enterprise.agents import default_agent_registry
from ot_skill_enterprise.control_plane.candidates import CandidateSurfaceService, build_candidate_surface_service
from ot_skill_enterprise.enterprise_bridge import EnterpriseBridge
from ot_skill_enterprise.providers.ave import build_ave_provider_registry
from .bootstrap import ControlPlaneBootstrap, build_control_plane_bootstrap
from .flows import FlowTemplateRegistry, build_default_flow_registry
from .query import ControlPlaneQueryService, build_control_plane_query_service


def _read_json_files(path: Path) -> list[dict[str, Any]]:
    if not path.exists() or not path.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for item in sorted(path.glob("*.json")):
        try:
            payload = json.loads(item.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(payload, dict):
            items.append(payload)
    return items


@dataclass
class ControlPlaneAPI:
    bootstrap: ControlPlaneBootstrap
    flow_registry: FlowTemplateRegistry = field(default_factory=build_default_flow_registry)
    query_service: ControlPlaneQueryService | None = None
    candidate_service: CandidateSurfaceService | None = None

    def _queries(self) -> ControlPlaneQueryService:
        if self.query_service is None:
            self.query_service = build_control_plane_query_service(self.bootstrap.project_root, self.bootstrap.workspace_root)
        return self.query_service

    def _candidates(self) -> CandidateSurfaceService:
        if self.candidate_service is None:
            self.candidate_service = build_candidate_surface_service(self.bootstrap.project_root, self.bootstrap.workspace_root)
        return self.candidate_service

    def runtimes(self) -> dict[str, Any]:
        return self._queries().list_runtimes()

    def runtime(self) -> dict[str, Any]:
        return self._queries().runtime_overview()

    def sessions(self) -> dict[str, Any]:
        return self._queries().list_sessions()

    def active_runs(self) -> dict[str, Any]:
        return self._queries().list_active_runs()

    def evaluations(self) -> dict[str, Any]:
        return self._queries().list_evaluations()

    def candidates(self) -> dict[str, Any]:
        return self._queries().list_candidates()

    def promotions(self) -> dict[str, Any]:
        return self._queries().list_promotions()

    def overview(self) -> dict[str, Any]:
        runtime = self.runtime()
        runtimes = self.runtimes()
        sessions = self.sessions()
        active_runs = self.active_runs()
        evaluations = self.evaluations()
        candidates = self.candidates()
        promotions = self.promotions()
        candidate_surface = self.candidate_overview()
        return {
            "bootstrap": self.bootstrap.to_dict(),
            "sections": {
                "runtime": runtime,
                "runtimes": runtimes,
                "sessions": sessions,
                "active_runs": active_runs,
                "evaluations": evaluations,
                "candidates": candidates,
                "promotions": promotions,
                "candidate_surface": candidate_surface,
            },
        }

    def candidate_overview(self) -> dict[str, Any]:
        return self._candidates().overview()

    def candidate_detail(self, candidate_id: str) -> dict[str, Any] | None:
        return self._candidates().candidate_detail(candidate_id)

    def candidate_compile(
        self,
        candidate: str | dict[str, Any],
        *,
        output_root: Path | None = None,
        package_kind: str | None = None,
        force: bool = True,
    ) -> dict[str, Any]:
        return self._candidates().compile_candidate(candidate, output_root=output_root, package_kind=package_kind, force=force)

    def candidate_validate(
        self,
        candidate: str | dict[str, Any],
        *,
        package_root: Path | str | None = None,
        action_id: str | None = None,
    ) -> dict[str, Any]:
        return self._candidates().validate_candidate(candidate, package_root=package_root, action_id=action_id)

    def candidate_promote(
        self,
        candidate: str | dict[str, Any],
        *,
        package_root: Path | str | None = None,
        package_kind: str | None = None,
        force: bool = True,
        action_id: str | None = None,
    ) -> dict[str, Any]:
        return self._candidates().promote_candidate(
            candidate,
            package_root=package_root,
            package_kind=package_kind,
            force=force,
            action_id=action_id,
        )

    def agents(self) -> dict[str, Any]:
        registry = default_agent_registry()
        agent_store = self.bootstrap.workspace_root / "evolution-registry" / "agents"
        persisted = _read_json_files(agent_store)
        persisted_ids = {item.get("agent_id") for item in persisted}
        registered = [item.adapter.model_dump(mode="json") for item in registry.list_enabled()]
        for item in persisted:
            if item.get("agent_id") in {agent["agent_id"] for agent in registered}:
                continue
            registered.append(item)
        return {
            "status": "ready",
            "compatible_agents": [item["display_name"] for item in registered],
            "registered_agents": registered,
            "persisted_agent_count": len(persisted_ids),
            "note": "agent adapters remain integration metadata only",
        }

    def providers(self) -> dict[str, Any]:
        bridge = EnterpriseBridge.from_project_root(self.bootstrap.project_root)
        registry = build_ave_provider_registry()
        return {
            "status": "ready",
            "provider_service": "ave-data-service",
            "available_modes": ["mock", "ave_rest"],
            "registrations": registry.describe(),
            "vendored_reference_skills": [item.to_dict() for item in bridge.discover_ave_cloud_skill_snapshots()],
        }

    def skills(self) -> dict[str, Any]:
        bridge = EnterpriseBridge.from_project_root(self.bootstrap.project_root)
        return {
            "status": "ready",
            "local_skills": [item.to_dict() for item in bridge.discover_local_skill_packages()],
            "vendored_ave_skills": [item.to_dict() for item in bridge.discover_ave_cloud_skill_snapshots()],
        }

    def flows(self) -> dict[str, Any]:
        return {
            "status": "ready",
            "templates": [template.to_dict() for template in self.flow_registry.list()],
        }

    def evolution(self) -> dict[str, Any]:
        return self._queries().evolution_summary()

    def to_dict(self) -> dict[str, Any]:
        return self.overview()


def build_control_plane_api(
    bootstrap: ControlPlaneBootstrap | None = None,
    *,
    root: Path | None = None,
    workspace_dir: Path | None = None,
) -> ControlPlaneAPI:
    resolved_bootstrap = bootstrap or build_control_plane_bootstrap(root=root, workspace_dir=workspace_dir)
    return ControlPlaneAPI(bootstrap=resolved_bootstrap)
