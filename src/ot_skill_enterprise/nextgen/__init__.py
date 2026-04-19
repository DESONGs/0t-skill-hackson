from .adapters import (
    AVE_DATA_MANIFEST,
    ONCHAINOS_EXECUTION_MANIFEST,
    AdapterCapability,
    AdapterCapabilityError,
    AdapterContract,
    AdapterManifest,
    AdapterRegistration,
    AdapterRegistry,
    AdapterRegistryError,
    AdapterType,
    AveDataSourceAdapterWrapper,
    DataSourceAdapter,
    ExecutionAdapter,
    OnchainOSExecutionAdapterWrapper,
    build_ave_data_source_adapter,
    build_builtin_adapter_registry,
    build_onchainos_execution_adapter,
    register_builtin_adapters,
)
from .execution_dispatch import execute_skill_action
from .kernel_bridge import WorkflowKernelBridge, build_nextgen_kernel_bridge
from .plugins import (
    PluginCapabilitySpec,
    PluginRegistration,
    WorkflowGraphSpec,
    WorkflowPluginRegistry,
    WorkflowPluginSpec,
    WorkflowRegistration,
    WorkflowStage,
    WorkflowStepSpec,
    build_default_plugin_registry,
)
from .provider_compat import DataSourceProviderCompat, build_provider_compat

__all__ = [
    "AVE_DATA_MANIFEST",
    "ONCHAINOS_EXECUTION_MANIFEST",
    "AdapterCapability",
    "AdapterCapabilityError",
    "AdapterContract",
    "AdapterManifest",
    "AdapterRegistration",
    "AdapterRegistry",
    "AdapterRegistryError",
    "AdapterType",
    "AveDataSourceAdapterWrapper",
    "DataSourceAdapter",
    "DataSourceProviderCompat",
    "ExecutionAdapter",
    "NextgenWorkflowService",
    "OnchainOSExecutionAdapterWrapper",
    "RecommendationBundle",
    "ResearchIterationRecord",
    "ResearchSessionState",
    "ResearchStopDecision",
    "ReviewArtifact",
    "ReviewDecision",
    "ReviewPluginExecutor",
    "SkillCreationPluginExecutor",
    "WorkflowKernelBridge",
    "WorkflowArtifact",
    "PluginCapabilitySpec",
    "PluginRegistration",
    "WorkflowGraphSpec",
    "WorkflowPluginRegistry",
    "WorkflowPluginSpec",
    "WorkflowRegistration",
    "WorkflowRunRequest",
    "WorkflowRunResult",
    "WorkflowStage",
    "WorkflowStepSpec",
    "WorkflowVariant",
    "BenchmarkPluginExecutor",
    "BenchmarkScorecard",
    "AutoresearchPluginExecutor",
    "ApprovalConvergenceResult",
    "SkillCreationPluginExecutor",
    "build_ave_data_source_adapter",
    "build_builtin_adapter_registry",
    "build_default_plugin_registry",
    "build_nextgen_kernel_bridge",
    "build_nextgen_workflow_service",
    "build_onchainos_execution_adapter",
    "build_provider_compat",
    "execute_skill_action",
    "register_builtin_adapters",
]

_LAZY_WORKFLOW_EXPORTS = {
    "AutoresearchPluginExecutor",
    "ApprovalConvergenceResult",
    "BenchmarkPluginExecutor",
    "BenchmarkScorecard",
    "NextgenWorkflowService",
    "RecommendationBundle",
    "ResearchIterationRecord",
    "ResearchSessionState",
    "ResearchStopDecision",
    "ReviewArtifact",
    "ReviewDecision",
    "ReviewPluginExecutor",
    "SkillCreationPluginExecutor",
    "WorkflowArtifact",
    "WorkflowRunRequest",
    "WorkflowRunResult",
    "WorkflowVariant",
    "build_nextgen_workflow_service",
    "validate_workflow_support",
}

_LAZY_SERVICE_EXPORTS = {
    "NextArchitectureService",
    "build_next_architecture_service",
}

_LAZY_KERNEL_EXPORTS = {
    "WorkflowKernelBridge",
    "build_nextgen_kernel_bridge",
}

_LAZY_EXECUTION_EXPORTS = {
    "execute_skill_action",
}


def __getattr__(name: str):
    if name in _LAZY_WORKFLOW_EXPORTS:
        from . import workflows as workflow_exports

        return getattr(workflow_exports, name)
    if name in _LAZY_SERVICE_EXPORTS:
        from . import service as service_exports

        return getattr(service_exports, name)
    if name in _LAZY_KERNEL_EXPORTS:
        from . import kernel_bridge as kernel_exports

        return getattr(kernel_exports, name)
    if name in _LAZY_EXECUTION_EXPORTS:
        from . import execution_dispatch as execution_exports

        return getattr(execution_exports, name)
    raise AttributeError(name)
