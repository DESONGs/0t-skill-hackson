from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from ot_skill_enterprise.shared.contracts.common import ContractModel, utc_now


class TeamAdapterCapability(ContractModel):
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)


class TeamAdapterSpec(ContractModel):
    adapter_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    session_mode: str = Field(default="local-bridge", min_length=1)
    homogeneous_only: bool = True
    supported_roles: list[str] = Field(default_factory=list)
    capabilities: list[TeamAdapterCapability] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TeamAdapterSession(ContractModel):
    agent_session_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    work_item_id: str = Field(min_length=1)
    adapter_id: str = Field(min_length=1)
    role_id: str = Field(min_length=1)
    status: str = Field(default="ready", min_length=1)
    handoff_path: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TeamRoleSpec(ContractModel):
    role_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    outputs: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkflowModuleSpec(ContractModel):
    module_id: str = Field(min_length=1)
    module_version: str = Field(min_length=1)
    capability_type: str = Field(default="workflow_optimizer", min_length=1)
    target_subjects: list[str] = Field(default_factory=list)
    search_space_schema: dict[str, Any] = Field(default_factory=dict)
    benchmark_profiles: list[dict[str, Any]] = Field(default_factory=list)
    gate_profiles: list[dict[str, Any]] = Field(default_factory=list)
    decision_policy: dict[str, Any] = Field(default_factory=dict)
    supported_team_topologies: list[str] = Field(default_factory=list)
    workspace_compatibility: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkflowDefinition(ContractModel):
    workflow_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    module_id: str = Field(min_length=1)
    default_adapter_family: str = Field(min_length=1)
    team_topology: str = Field(default="homogeneous", min_length=1)
    roles: list[str] = Field(default_factory=list)
    search_space: list[str] = Field(default_factory=list)
    hard_gates: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def kernel_workflow_id(self) -> str:
        return str(self.metadata.get("kernel_workflow_id") or self.workflow_id)


class OptimizationScorecard(ContractModel):
    primary_quality_score: float | None = None
    backtest_confidence: float | None = None
    execution_readiness: float | None = None
    strategy_quality: float | None = None
    style_distance: float | None = None
    risk_penalty: float | None = None
    confidence_vs_noise: float | None = None
    hard_gates_passed: bool | None = None
    notes: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class OptimizationVariant(ContractModel):
    variant_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    module_id: str = Field(min_length=1)
    subject_kind: str = Field(default="skill", min_length=1)
    subject_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    kind: str = Field(default="proposal", min_length=1)
    parent_variant_id: str | None = None
    source_skill_ref: str | None = None
    strategy_patch: dict[str, Any] = Field(default_factory=dict)
    execution_patch: dict[str, Any] = Field(default_factory=dict)
    review_patch: dict[str, Any] = Field(default_factory=dict)
    created_by_role: str = Field(min_length=1)
    created_by_agent_id: str | None = None
    lineage: dict[str, Any] = Field(default_factory=dict)
    status: str = Field(default="draft", min_length=1)
    scorecard: OptimizationScorecard | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class OptimizationRun(ContractModel):
    run_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    variant_id: str = Field(min_length=1)
    runner_id: str = Field(min_length=1)
    status: str = Field(default="pending", min_length=1)
    summary: str = Field(min_length=1)
    benchmark_profile: str | None = None
    gate_profile: str | None = None
    hard_gates_passed: bool | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class OptimizationDecision(ContractModel):
    decision_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    variant_id: str = Field(min_length=1)
    role_id: str = Field(min_length=1)
    decision: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    rationale: str | None = None
    reviewer_confidence: float | None = None
    created_by_agent_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class OptimizationRecommendation(ContractModel):
    recommendation_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    variant_id: str = Field(min_length=1)
    status: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    decision_ids: list[str] = Field(default_factory=list)
    scorecard: OptimizationScorecard | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class OptimizationActivation(ContractModel):
    activation_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    variant_id: str = Field(min_length=1)
    status: str = Field(min_length=1)
    approved_by: str | None = None
    activated_by: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class WorkItem(ContractModel):
    work_item_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    role_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    status: str = Field(default="queued", min_length=1)
    adapter_id: str = Field(min_length=1)
    depends_on: list[str] = Field(default_factory=list)
    input_refs: list[str] = Field(default_factory=list)
    instructions_path: str | None = None
    result_path: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class OptimizationSession(ContractModel):
    session_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    workflow_id: str = Field(min_length=1)
    module_id: str = Field(min_length=1)
    status: str = Field(default="draft", min_length=1)
    adapter_family: str = Field(min_length=1)
    team_topology: str = Field(default="homogeneous", min_length=1)
    subject_kind: str = Field(default="skill", min_length=1)
    subject_id: str = Field(min_length=1)
    source_skill_path: str | None = None
    baseline_variant_id: str | None = None
    title: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    constraints: dict[str, Any] = Field(default_factory=dict)
    hard_gates: list[str] = Field(default_factory=list)
    enabled_roles: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)
