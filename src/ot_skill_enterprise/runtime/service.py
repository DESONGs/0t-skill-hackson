from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ot_skill_enterprise.service_locator import project_root as resolve_project_root

from .coordinator import RuntimeRunCoordinator, RuntimeRunResult
from .executor import SubprocessRuntimeExecutor
from .pi.bootstrap import PiRuntimeBootstrap, build_pi_runtime_bootstrap
from .registry import RuntimeRegistry
from .store import RuntimeSessionStore, build_runtime_session_store
from .translator import DefaultRuntimeTranslator


def _project_root(root: Path | None = None) -> Path:
    return Path(root).expanduser().resolve() if root is not None else resolve_project_root()


def _registry_root(workspace_root: Path) -> Path:
    root = workspace_root / "evolution-registry"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _session_store(workspace_root: Path) -> RuntimeSessionStore:
    return build_runtime_session_store(_registry_root(workspace_root))


@dataclass(slots=True)
class RuntimeService:
    project_root: Path
    workspace_root: Path
    bootstrap: PiRuntimeBootstrap
    registry: RuntimeRegistry
    session_store: RuntimeSessionStore
    coordinator: RuntimeRunCoordinator

    def list_runtimes(self) -> dict[str, Any]:
        return {
            "status": "ready",
            "count": len(self.registry.list()),
            "items": self.registry.describe(),
            "default_runtime": self.bootstrap.adapter.descriptor.runtime_id,
        }

    def list_sessions(self) -> dict[str, Any]:
        sessions = self.session_store.load_from_disk()
        sessions.sort(key=lambda item: item.updated_at, reverse=True)
        return {
            "status": "ready",
            "count": len(sessions),
            "items": [item.model_dump(mode="json") for item in sessions],
        }

    def start_session(
        self,
        *,
        runtime_id: str = "pi",
        session_id: str | None = None,
        cwd: Path | str | None = None,
        inputs: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ):
        adapter = self.registry.resolve(runtime_id)
        session = adapter.start_session(session_id=session_id, cwd=cwd, inputs=inputs, metadata=metadata)
        self.session_store.record_session(session)
        return session

    def run(
        self,
        *,
        runtime_id: str = "pi",
        prompt: str,
        session_id: str | None = None,
        cwd: Path | str | None = None,
        input_payload: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> RuntimeRunResult:
        adapter = self.registry.resolve(runtime_id)
        return self.coordinator.run(
            adapter=adapter,
            prompt=prompt,
            session_id=session_id,
            cwd=cwd,
            input_payload=input_payload,
            metadata=metadata,
        )


def build_runtime_service(
    *,
    root: Path | None = None,
    workspace_dir: Path | None = None,
    runtime_root: Path | None = None,
) -> RuntimeService:
    project_root = _project_root(root)
    workspace_root = Path(workspace_dir).expanduser().resolve() if workspace_dir is not None else project_root / ".ot-workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    bootstrap = build_pi_runtime_bootstrap(root=project_root, workspace_dir=workspace_root, runtime_root=runtime_root)
    session_store = _session_store(workspace_root)
    session_store.load_from_disk()
    coordinator = RuntimeRunCoordinator(
        project_root=project_root,
        workspace_root=workspace_root,
        session_store=session_store,
        executor=SubprocessRuntimeExecutor(),
        translator=DefaultRuntimeTranslator(),
        registry_root=_registry_root(workspace_root),
    )
    return RuntimeService(
        project_root=project_root,
        workspace_root=workspace_root,
        bootstrap=bootstrap,
        registry=bootstrap.runtime_registry,
        session_store=session_store,
        coordinator=coordinator,
    )
