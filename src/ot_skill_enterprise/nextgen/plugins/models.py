from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from ot_skill_enterprise.shared.contracts.common import ContractModel


PluginType = Literal[
    "distillation",
    "skill-creation",
    "autoresearch",
    "benchmark",
    "review",
    "approval-convergence",
]
WorkflowStage = Literal["seed", "plan", "execute", "benchmark", "review", "finalize"]


class PluginCapabilitySpec(ContractModel):
    capability_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    consumes: list[str] = Field(default_factory=list)
    produces: list[str] = Field(default_factory=list)
    requests_follow_up: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkflowPluginSpec(ContractModel):
    plugin_id: str = Field(min_length=1)
    plugin_version: str = Field(min_length=1)
    plugin_type: PluginType
    display_name: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    supported_subjects: list[str] = Field(default_factory=list)
    input_schema_version: str = Field(default="v1", min_length=1)
    output_schema_version: str = Field(default="v1", min_length=1)
    capabilities: list[PluginCapabilitySpec] = Field(default_factory=list)
    artifact_kinds: list[str] = Field(default_factory=list)
    default_benchmark_profiles: list[str] = Field(default_factory=list)
    default_gate_profiles: list[str] = Field(default_factory=list)
    search_space_fields: list[str] = Field(default_factory=list)
    compatible_workflows: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_capability_ids(self) -> "WorkflowPluginSpec":
        capability_ids = [item.capability_id for item in self.capabilities]
        if len(capability_ids) != len(set(capability_ids)):
            raise ValueError(f"plugin {self.plugin_id!r} has duplicate capability ids")
        return self

    def worker_actions(self) -> tuple[str, ...]:
        return tuple(str(item) for item in list(self.metadata.get("worker_actions") or []) if str(item).strip())

    def action_contract(self, action_id: str) -> dict[str, Any]:
        contracts = dict(self.metadata.get("action_contracts") or {})
        payload = contracts.get(action_id)
        return dict(payload) if isinstance(payload, dict) else {}


class WorkflowStepSpec(ContractModel):
    step_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    plugin_id: str = Field(min_length=1)
    stage: WorkflowStage
    description: str = Field(min_length=1)
    depends_on: list[str] = Field(default_factory=list)
    optional: bool = False
    allow_reentry: bool = False
    consumes: list[str] = Field(default_factory=list)
    produces: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_self_dependency(self) -> "WorkflowStepSpec":
        if self.step_id in self.depends_on:
            raise ValueError(f"workflow step {self.step_id!r} cannot depend on itself")
        return self

    def action_id(self) -> str | None:
        value = self.metadata.get("action_id")
        text = str(value).strip() if value is not None else ""
        return text or None

    def role_hint(self) -> str | None:
        value = self.metadata.get("role_hint")
        text = str(value).strip() if value is not None else ""
        return text or None


class WorkflowGraphSpec(ContractModel):
    workflow_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    subject_kind: str = Field(default="skill", min_length=1)
    steps: list[WorkflowStepSpec] = Field(default_factory=list)
    entry_steps: list[str] = Field(default_factory=list)
    terminal_steps: list[str] = Field(default_factory=list)
    iterative: bool = False
    human_review_required: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_graph(self) -> "WorkflowGraphSpec":
        step_ids = [item.step_id for item in self.steps]
        if not step_ids:
            raise ValueError(f"workflow {self.workflow_id!r} must declare at least one step")
        if len(step_ids) != len(set(step_ids)):
            raise ValueError(f"workflow {self.workflow_id!r} has duplicate step ids")

        known_ids = set(step_ids)
        for step in self.steps:
            unknown_dependencies = sorted(set(step.depends_on) - known_ids)
            if unknown_dependencies:
                raise ValueError(
                    f"workflow {self.workflow_id!r} step {step.step_id!r} depends on unknown steps "
                    f"{unknown_dependencies}"
                )

        if self.entry_steps:
            unknown_entry_steps = sorted(set(self.entry_steps) - known_ids)
            if unknown_entry_steps:
                raise ValueError(
                    f"workflow {self.workflow_id!r} declares unknown entry steps {unknown_entry_steps}"
                )
        else:
            self.entry_steps = [item.step_id for item in self.steps if not item.depends_on]

        if self.terminal_steps:
            unknown_terminal_steps = sorted(set(self.terminal_steps) - known_ids)
            if unknown_terminal_steps:
                raise ValueError(
                    f"workflow {self.workflow_id!r} declares unknown terminal steps {unknown_terminal_steps}"
                )
        else:
            dependency_targets = {dep for step in self.steps for dep in step.depends_on}
            self.terminal_steps = [item.step_id for item in self.steps if item.step_id not in dependency_targets]

        return self

    def step(self, step_id: str) -> WorkflowStepSpec:
        for item in self.steps:
            if item.step_id == step_id:
                return item
        raise KeyError(f"workflow {self.workflow_id!r} has no step {step_id!r}")

    def downstream_steps(self, step_id: str) -> list[WorkflowStepSpec]:
        return [item for item in self.steps if step_id in item.depends_on]

    def plugin_ids(self) -> tuple[str, ...]:
        ordered: list[str] = []
        for item in self.steps:
            if item.plugin_id not in ordered:
                ordered.append(item.plugin_id)
        return tuple(ordered)

    def step_action_id(self, step_id: str) -> str | None:
        return self.step(step_id).action_id()
