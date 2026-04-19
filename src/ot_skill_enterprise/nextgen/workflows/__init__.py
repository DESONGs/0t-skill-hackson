from .executors import (
    AutoresearchPluginExecutor,
    BenchmarkPluginExecutor,
    ReviewPluginExecutor,
    SkillCreationPluginExecutor,
    validate_workflow_support,
)
from .models import (
    ApprovalConvergenceResult,
    BenchmarkScorecard,
    RecommendationBundle,
    ResearchIterationRecord,
    ResearchSessionState,
    ResearchStopDecision,
    ResearchVariantPlan,
    ReviewArtifact,
    ReviewDecision,
    WorkflowArtifact,
    WorkflowRunRequest,
    WorkflowRunResult,
    WorkflowVariant,
)

__all__ = [
    "AutoresearchPluginExecutor",
    "ApprovalConvergenceResult",
    "BenchmarkPluginExecutor",
    "BenchmarkScorecard",
    "NextgenWorkflowService",
    "RecommendationBundle",
    "ResearchIterationRecord",
    "ResearchSessionState",
    "ResearchStopDecision",
    "ResearchVariantPlan",
    "ReviewArtifact",
    "ReviewDecision",
    "ReviewPluginExecutor",
    "SkillCreationPluginExecutor",
    "WorkflowArtifact",
    "WorkflowRunRequest",
    "WorkflowRunResult",
    "WorkflowVariant",
    "NextgenWorkflowService",
    "build_nextgen_workflow_service",
    "validate_workflow_support",
]


def __getattr__(name: str):
    if name in {"NextgenWorkflowService", "build_nextgen_workflow_service"}:
        from .service import NextgenWorkflowService, build_nextgen_workflow_service

        return {
            "NextgenWorkflowService": NextgenWorkflowService,
            "build_nextgen_workflow_service": build_nextgen_workflow_service,
        }[name]
    raise AttributeError(name)
