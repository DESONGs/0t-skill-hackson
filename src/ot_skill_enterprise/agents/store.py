from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .models import AgentAdapter


def _dump_model(value: Any) -> Any:
    dumper = getattr(value, "model_dump", None)
    if dumper is not None:
        return dumper(mode="json")
    return value


def _json_payload(value: Any) -> dict[str, Any]:
    payload = _dump_model(value)
    if isinstance(payload, Mapping):
        return dict(payload)
    raise TypeError("agent records must be mappings or pydantic models")


@dataclass
class AgentStore:
    root: Path | None = None
    agents: dict[str, dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.root is not None:
            self.root = Path(self.root).expanduser().resolve()
            self.root.mkdir(parents=True, exist_ok=True)

    def _category_dir(self) -> Path | None:
        if self.root is None:
            return None
        path = self.root / "agents"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _persist(self, agent_id: str, payload: dict[str, Any]) -> None:
        directory = self._category_dir()
        if directory is None:
            return
        (directory / f"{agent_id}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def record_agent(self, agent: AgentAdapter | Mapping[str, Any]) -> AgentAdapter:
        normalized = agent if isinstance(agent, AgentAdapter) else AgentAdapter.model_validate(dict(agent))
        payload = normalized.model_dump(mode="json")
        self.agents[normalized.agent_id] = payload
        self._persist(normalized.agent_id, payload)
        return normalized

    def get_agent(self, agent_id: str) -> AgentAdapter | None:
        payload = self.agents.get(agent_id)
        if payload is None:
            return None
        return AgentAdapter.model_validate(payload)

    def list_agents(self) -> list[AgentAdapter]:
        return [AgentAdapter.model_validate(payload) for payload in self.agents.values()]

    def load_from_disk(self) -> list[AgentAdapter]:
        directory = self._category_dir()
        if directory is None:
            return []
        loaded: list[AgentAdapter] = []
        for path in sorted(directory.glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            adapter = AgentAdapter.model_validate(payload)
            self.agents[adapter.agent_id] = adapter.model_dump(mode="json")
            loaded.append(adapter)
        return loaded


def build_agent_store(root: Path | str | None = None) -> AgentStore:
    return AgentStore(root=Path(root) if root is not None else None)
