from __future__ import annotations

from typing import Any

from pydantic import Field

from ot_skill_enterprise.shared.contracts.common import ContractModel


class AgentCapability(ContractModel):
    name: str = Field(min_length=1)
    description: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentAdapter(ContractModel):
    agent_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    execution_mode: str = Field(default="external", min_length=1)
    can_invoke_skills: bool = True
    can_emit_trace: bool = True
    can_emit_artifacts: bool = True
    capabilities: list[AgentCapability] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentRegistration(ContractModel):
    adapter: AgentAdapter
    source: str = Field(default="local", min_length=1)
    enabled: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)

