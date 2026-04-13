from __future__ import annotations

from dataclasses import dataclass, field

from .models import AgentAdapter, AgentRegistration


@dataclass
class AgentAdapterRegistry:
    """Small in-memory registry for agent integration metadata."""

    registrations: dict[str, AgentRegistration] = field(default_factory=dict)

    def register(self, adapter: AgentAdapter, *, source: str = "local", enabled: bool = True) -> AgentRegistration:
        registration = AgentRegistration(adapter=adapter, source=source, enabled=enabled)
        self.registrations[adapter.agent_id] = registration
        return registration

    def get(self, agent_id: str) -> AgentRegistration | None:
        return self.registrations.get(agent_id)

    def list_enabled(self) -> list[AgentRegistration]:
        return [item for item in self.registrations.values() if item.enabled]


def default_agent_registry() -> AgentAdapterRegistry:
    registry = AgentAdapterRegistry()
    registry.register(AgentAdapter(agent_id="codex", display_name="Codex", metadata={"kind": "example"}))
    registry.register(AgentAdapter(agent_id="claude-code", display_name="Claude Code", metadata={"kind": "example"}))
    registry.register(AgentAdapter(agent_id="hermes", display_name="Hermes", metadata={"kind": "example"}))
    registry.register(AgentAdapter(agent_id="openclaw", display_name="OpenClaw", metadata={"kind": "example"}))
    return registry

