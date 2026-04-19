from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ot_skill_enterprise.nextgen.adapters import AdapterRegistry, build_builtin_adapter_registry
from ot_skill_enterprise.nextgen.kernel_bridge import (
    WorkflowFallback,
    WorkflowKernelBridge,
    build_nextgen_kernel_bridge,
    configured_workflow_runtime,
)
from ot_skill_enterprise.nextgen.plugins import WorkflowPluginRegistry, build_default_plugin_registry
from ot_skill_enterprise.style_distillation import build_wallet_style_distillation_service

from .executors import validate_workflow_support
from .models import (
    ApprovalConvergenceResult,
    RecommendationBundle,
    WorkflowRunResult,
    WorkflowRunRequest,
)
from .python_compat import PythonCompatWorkflowRunner, build_python_compat_runner


def _default_project_root() -> Path:
    return Path(__file__).resolve().parents[3]


class NextgenWorkflowService:
    def __init__(
        self,
        *,
        project_root: Path,
        workspace_root: Path,
        plugin_registry: WorkflowPluginRegistry,
        adapter_registry: AdapterRegistry,
        kernel_bridge: WorkflowKernelBridge,
        python_compat_runner: PythonCompatWorkflowRunner | None = None,
    ) -> None:
        self.project_root = project_root
        self.workspace_root = workspace_root
        self.plugin_registry = plugin_registry
        self.adapter_registry = adapter_registry
        self.kernel_bridge = kernel_bridge
        self.python_compat_runner = python_compat_runner

    @property
    def distillation_worker_handler(self):
        if self.python_compat_runner is None:
            return None
        return self.python_compat_runner.distillation_worker_handler

    def _fallback_runner(self, runner: WorkflowFallback | None) -> WorkflowFallback | None:
        if self.kernel_bridge.runtime_mode == "python-compat" and runner is not None:
            return runner
        return None

    def _workflow_action(self, workflow_id: str, step_id: str) -> str:
        return self.plugin_registry.resolve_step_action(workflow_id, step_id)

    def _workspace_adapter_ids(self, request: WorkflowRunRequest) -> dict[str, str]:
        metadata = dict(request.metadata or {})
        workspace_adapters = metadata.get("workspace_adapters")
        adapter_ids = metadata.get("adapter_ids")
        resolved: dict[str, str] = {}
        for payload in (workspace_adapters, adapter_ids, metadata):
            if not isinstance(payload, Mapping):
                continue
            data_source = str(payload.get("data_source") or payload.get("data_source_adapter_id") or "").strip()
            execution = str(payload.get("execution") or payload.get("execution_adapter_id") or "").strip()
            if data_source and "data_source" not in resolved:
                resolved["data_source"] = data_source
            if execution and "execution" not in resolved:
                resolved["execution"] = execution
        return resolved

    def run_distillation_seed(self, request: WorkflowRunRequest) -> WorkflowRunResult:
        workspace_adapters = self._workspace_adapter_ids(request)
        validate_workflow_support(
            self.plugin_registry,
            workflow_id="distillation_seed",
            required_plugins=("distillation", "skill-creation"),
        )
        dispatch = self.kernel_bridge.dispatch(
            workflow_id="distillation_seed",
            request_payload=request.model_dump(mode="json"),
            fallback_runner=self._fallback_runner(
                None
                if self.python_compat_runner is None
                else lambda payload: self.python_compat_runner.run_distillation_seed_payload(
                    payload,
                    workspace_adapter_ids=workspace_adapters,
                )
            ),
        )
        final_result = dict(dispatch.get("final_result") or {})
        result = WorkflowRunResult.model_validate(final_result)
        result.metadata = {
            **dict(result.metadata),
            "kernel_dispatch": {key: value for key, value in dispatch.items() if key != "final_result"},
        }
        return result

    def run_autonomous_research(self, request: WorkflowRunRequest) -> RecommendationBundle:
        workspace_adapters = self._workspace_adapter_ids(request)
        validate_workflow_support(
            self.plugin_registry,
            workflow_id="autonomous_research",
            required_plugins=("distillation", "skill-creation", "autoresearch", "benchmark", "review"),
        )
        dispatch = self.kernel_bridge.dispatch(
            workflow_id="autonomous_research",
            request_payload=request.model_dump(mode="json"),
            fallback_runner=self._fallback_runner(
                None
                if self.python_compat_runner is None
                else lambda payload: self.python_compat_runner.run_autonomous_research_payload(
                    payload,
                    workspace_adapter_ids=workspace_adapters,
                )
            ),
        )
        final_result = dict(dispatch.get("final_result") or {})
        recommendation = RecommendationBundle.model_validate(final_result)
        recommendation.metadata = {
            **dict(recommendation.metadata),
            "kernel_dispatch": {key: value for key, value in dispatch.items() if key != "final_result"},
        }
        return recommendation

    def run_approval_convergence(self, request: WorkflowRunRequest) -> ApprovalConvergenceResult:
        workspace_adapters = self._workspace_adapter_ids(request)
        validate_workflow_support(
            self.plugin_registry,
            workflow_id="approval_convergence",
            required_plugins=("benchmark", "review", "approval-convergence"),
        )
        dispatch = self.kernel_bridge.dispatch(
            workflow_id="approval_convergence",
            request_payload=request.model_dump(mode="json"),
            fallback_runner=self._fallback_runner(
                None
                if self.python_compat_runner is None
                else lambda payload: self.python_compat_runner.run_approval_convergence_payload(
                    payload,
                    workspace_adapter_ids=workspace_adapters,
                )
            ),
        )
        final_result = dict(dispatch.get("final_result") or {})
        result = ApprovalConvergenceResult.model_validate(final_result)
        result.metadata = {
            **dict(result.metadata),
            "kernel_dispatch": {key: value for key, value in dispatch.items() if key != "final_result"},
        }
        return result


def build_nextgen_workflow_service(
    *,
    project_root: Path | None = None,
    workspace_root: Path | None = None,
    data_source_adapter_id: str | None = None,
    plugin_registry: WorkflowPluginRegistry | None = None,
    adapter_registry: AdapterRegistry | None = None,
    kernel_bridge: WorkflowKernelBridge | None = None,
    distillation_service: Any | None = None,
    reflection_service: Any | None = None,
    runtime_mode: str | None = None,
) -> NextgenWorkflowService:
    resolved_root = (project_root or _default_project_root()).expanduser().resolve()
    resolved_workspace_root = (
        Path(workspace_root).expanduser().resolve()
        if workspace_root is not None
        else (resolved_root / ".ot-workspace").resolve()
    )
    resolved_workspace_root.mkdir(parents=True, exist_ok=True)
    resolved_plugins = plugin_registry or build_default_plugin_registry(project_root=resolved_root)
    resolved_adapters = adapter_registry or build_builtin_adapter_registry(project_root=resolved_root)
    resolved_kernel_bridge = kernel_bridge or build_nextgen_kernel_bridge(
        project_root=resolved_root,
        workspace_root=resolved_workspace_root,
        runtime_mode=runtime_mode,
    )
    resolved_runtime_mode = configured_workflow_runtime(resolved_kernel_bridge.runtime_mode)
    python_compat_runner = (
        build_python_compat_runner(
            project_root=resolved_root,
            workspace_root=resolved_workspace_root,
            plugin_registry=resolved_plugins,
            adapter_registry=resolved_adapters,
            distillation_service=distillation_service,
            reflection_service=reflection_service,
            workflow_data_source_adapter_id=data_source_adapter_id,
        )
        if (resolved_runtime_mode or "").strip().lower() == "python-compat"
        else None
    )
    return NextgenWorkflowService(
        project_root=resolved_root,
        workspace_root=resolved_workspace_root,
        plugin_registry=resolved_plugins,
        adapter_registry=resolved_adapters,
        kernel_bridge=resolved_kernel_bridge,
        python_compat_runner=python_compat_runner,
    )
