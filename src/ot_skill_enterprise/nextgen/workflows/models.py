from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from ot_skill_enterprise.shared.contracts.common import ArtifactRef, ContractModel


DecisionStatus = Literal["keep", "discard", "review_required", "recommended"]
ApprovalStatus = Literal["review_required", "approved", "activated", "blocked"]


class WorkflowArtifact(ContractModel):
    ref: ArtifactRef
    payload: dict[str, Any] = Field(default_factory=dict)


class ResearchVariantPlan(ContractModel):
    plan_id: str = Field(min_length=1)
    template_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    hypothesis: str = Field(min_length=1)
    change_summary: str = Field(min_length=1)
    target_fields: list[str] = Field(default_factory=list)
    mutations: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[WorkflowArtifact] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReviewArtifact(ContractModel):
    variant_id: str = Field(min_length=1)
    governance_status: DecisionStatus
    approval_required: bool = True
    activation_allowed: bool = False
    rationale: str = Field(min_length=1)
    blocking_findings: list[str] = Field(default_factory=list)
    follow_up_actions: list[str] = Field(default_factory=list)
    artifacts: list[WorkflowArtifact] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkflowVariant(ContractModel):
    variant_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    source: str = Field(min_length=1)
    status: str = Field(default="ready", min_length=1)
    strategy_spec: dict[str, Any] = Field(default_factory=dict)
    execution_intent: dict[str, Any] = Field(default_factory=dict)
    style_profile: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[WorkflowArtifact] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class BenchmarkScorecard(ContractModel):
    variant_id: str = Field(min_length=1)
    primary_quality_score: float = Field(ge=0.0, le=1.0)
    backtest_confidence: float = Field(ge=0.0, le=1.0)
    execution_readiness: str = Field(min_length=1)
    strategy_quality: str = Field(min_length=1)
    style_distance: float = Field(ge=0.0, le=1.0)
    risk_penalty: float = Field(ge=0.0, le=1.0)
    confidence_vs_noise: float
    hard_gates_passed: bool = True
    notes: list[str] = Field(default_factory=list)
    artifacts: list[WorkflowArtifact] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReviewDecision(ContractModel):
    variant_id: str = Field(min_length=1)
    status: DecisionStatus
    reasoning: str = Field(min_length=1)
    review_notes: list[str] = Field(default_factory=list)
    governance: ReviewArtifact | None = None
    artifacts: list[WorkflowArtifact] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResearchStopDecision(ContractModel):
    decision: Literal["continue", "stop"] = "stop"
    reason: str = Field(min_length=1)
    selected_variant_id: str | None = None
    next_parent_variant_id: str | None = None
    human_review_required: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResearchIterationRecord(ContractModel):
    session_id: str = Field(min_length=1)
    iteration_index: int = Field(ge=1)
    parent_variant_id: str = Field(min_length=1)
    plan_ids: list[str] = Field(default_factory=list)
    generated_variant_ids: list[str] = Field(default_factory=list)
    benchmarked_variant_ids: list[str] = Field(default_factory=list)
    reviewed_variant_ids: list[str] = Field(default_factory=list)
    selected_variant_id: str | None = None
    recommendation_status: str = Field(default="review_required", min_length=1)
    stop_decision: ResearchStopDecision
    artifacts: list[WorkflowArtifact] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResearchSessionState(ContractModel):
    session_id: str = Field(min_length=1)
    workflow_id: str = Field(min_length=1)
    workspace_id: str | None = None
    workspace_dir: str = Field(min_length=1)
    wallet: str = Field(min_length=1)
    chain: str = Field(min_length=1)
    skill_name: str | None = None
    objective: str = Field(min_length=1)
    baseline_variant_id: str = Field(min_length=1)
    current_iteration: int = Field(default=0, ge=0)
    max_iterations: int = Field(default=1, ge=1)
    status: str = Field(default="running", min_length=1)
    active_parent_variant_id: str | None = None
    stop_decision: ResearchStopDecision | None = None
    recommendation_variant_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RecommendationBundle(ContractModel):
    workflow_id: str = Field(min_length=1)
    baseline_variant_id: str = Field(min_length=1)
    session_id: str | None = None
    workspace_id: str | None = None
    status: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    recommended_variant_id: str | None = None
    iteration_count: int = Field(default=0, ge=0)
    selected_variant: WorkflowVariant | None = None
    leaderboard: list[dict[str, Any]] = Field(default_factory=list)
    scorecards: list[BenchmarkScorecard] = Field(default_factory=list)
    review_decisions: list[ReviewDecision] = Field(default_factory=list)
    iterations: list[ResearchIterationRecord] = Field(default_factory=list)
    stop_decision: ResearchStopDecision | None = None
    artifacts: list[WorkflowArtifact] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ApprovalActivationRecord(ContractModel):
    variant_id: str = Field(min_length=1)
    approval_required: bool = True
    approval_granted: bool = False
    activation_requested: bool = False
    activation_allowed: bool = False
    status: ApprovalStatus
    rationale: str = Field(min_length=1)
    artifacts: list[WorkflowArtifact] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ApprovalConvergenceResult(ContractModel):
    workflow_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    workspace_id: str | None = None
    baseline_variant_id: str = Field(min_length=1)
    recommended_variant_id: str | None = None
    selected_variant: WorkflowVariant | None = None
    recommendation_bundle: RecommendationBundle
    benchmark_scorecard: BenchmarkScorecard | None = None
    review_decision: ReviewDecision | None = None
    approval: ApprovalActivationRecord
    status: ApprovalStatus
    summary: str = Field(min_length=1)
    artifacts: list[WorkflowArtifact] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkflowRunRequest(ContractModel):
    workflow_id: str = Field(min_length=1)
    session_id: str | None = None
    workspace_id: str | None = None
    wallet: str | None = None
    chain: str = Field(default="bsc", min_length=1)
    skill_name: str | None = None
    workspace_dir: str | None = None
    objective: str = Field(
        default="improve strategy quality while preserving style and execution discipline",
        min_length=1,
    )
    iteration_budget: int = Field(default=1, ge=1, le=10)
    max_variants: int = Field(default=2, ge=1, le=10)
    candidate_variants: list[dict[str, Any]] = Field(default_factory=list)
    data_source_adapter_id: str | None = None
    execution_adapter_id: str | None = None
    operator_hints: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_wallet_for_distillation(self) -> "WorkflowRunRequest":
        if self.workflow_id in {"distillation_seed", "autonomous_research"} and not self.wallet:
            raise ValueError("wallet is required for workflow execution")
        if self.workflow_id == "approval_convergence" and not self.session_id:
            raise ValueError("session_id is required for approval convergence")
        return self


class WorkflowRunResult(ContractModel):
    workflow_id: str = Field(min_length=1)
    session_id: str | None = None
    workspace_id: str | None = None
    baseline_variant: WorkflowVariant
    candidate_variants: list[WorkflowVariant] = Field(default_factory=list)
    scorecards: list[BenchmarkScorecard] = Field(default_factory=list)
    review_decisions: list[ReviewDecision] = Field(default_factory=list)
    iterations: list[ResearchIterationRecord] = Field(default_factory=list)
    recommendation_bundle: RecommendationBundle | None = None
    artifacts: list[WorkflowArtifact] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
