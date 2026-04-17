from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class FlowTemplate:
    template_id: str
    title: str
    description: str
    status: str = "ready"
    source: str = "control-plane"
    compatible_agents: tuple[str, ...] = ()
    required_providers: tuple[str, ...] = ()
    required_skills: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "template_id": self.template_id,
            "title": self.title,
            "description": self.description,
            "status": self.status,
            "source": self.source,
            "compatible_agents": list(self.compatible_agents),
            "required_providers": list(self.required_providers),
            "required_skills": list(self.required_skills),
            "metadata": dict(self.metadata),
        }
