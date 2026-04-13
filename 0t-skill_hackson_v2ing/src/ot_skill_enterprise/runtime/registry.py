from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .contracts import RuntimeAdapter
from .models import RuntimeDescriptor


@dataclass(slots=True)
class RuntimeRegistration:
    runtime_id: str
    descriptor: RuntimeDescriptor
    adapter: RuntimeAdapter
    enabled: bool = True
    tags: tuple[str, ...] = field(default_factory=tuple)
    metadata: dict[str, Any] = field(default_factory=dict)


class RuntimeRegistry:
    def __init__(self) -> None:
        self._registrations: dict[str, RuntimeRegistration] = {}

    def register(
        self,
        adapter: RuntimeAdapter,
        *,
        runtime_id: str | None = None,
        enabled: bool = True,
        tags: tuple[str, ...] = (),
        metadata: dict[str, Any] | None = None,
    ) -> RuntimeRegistration:
        descriptor = adapter.descriptor
        resolved_runtime_id = runtime_id or descriptor.runtime_id
        registration = RuntimeRegistration(
            runtime_id=resolved_runtime_id,
            descriptor=descriptor,
            adapter=adapter,
            enabled=enabled,
            tags=tuple(tags),
            metadata=dict(metadata or {}),
        )
        self._registrations[resolved_runtime_id] = registration
        return registration

    def get(self, runtime_id: str) -> RuntimeRegistration | None:
        return self._registrations.get(runtime_id)

    def resolve(self, runtime_id: str) -> RuntimeAdapter:
        registration = self._registrations.get(runtime_id)
        if registration is None:
            raise KeyError(f"no runtime registered for {runtime_id!r}")
        return registration.adapter

    def list(self, *, enabled_only: bool = False) -> list[RuntimeRegistration]:
        registrations = list(self._registrations.values())
        if enabled_only:
            registrations = [item for item in registrations if item.enabled]
        return registrations

    def describe(self, *, enabled_only: bool = False) -> list[dict[str, Any]]:
        return [
            {
                "runtime_id": registration.runtime_id,
                "descriptor": registration.descriptor.model_dump(mode="json"),
                "enabled": registration.enabled,
                "tags": list(registration.tags),
                "metadata": dict(registration.metadata),
            }
            for registration in self.list(enabled_only=enabled_only)
        ]


def build_default_runtime_registry(
    adapter: RuntimeAdapter | None = None,
    *,
    runtime_root: Path | str | None = None,
    workspace_dir: Path | str | None = None,
) -> RuntimeRegistry:
    registry = RuntimeRegistry()
    if adapter is None:
        from .pi.adapter import build_pi_runtime_adapter

        adapter = build_pi_runtime_adapter(runtime_root=runtime_root, workspace_dir=workspace_dir)
    registry.register(adapter, tags=("pi", "embedded", "node-ts"))
    return registry

