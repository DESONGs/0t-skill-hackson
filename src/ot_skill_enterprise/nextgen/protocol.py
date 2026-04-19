from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import Field, model_validator

from ot_skill_enterprise.shared.contracts.common import ContractModel

from .adapters.models import AdapterCapability, AdapterManifest
from .plugins.models import WorkflowGraphSpec, WorkflowPluginSpec, WorkflowStepSpec


def _default_project_root() -> Path:
    return Path(__file__).resolve().parents[3]


PROTOCOL_BUNDLE_DIR = "0t-protocol"
NEXTGEN_PROTOCOL_MANIFEST = Path(PROTOCOL_BUNDLE_DIR) / "nextgen" / "manifest.json"


def _string_list(*values: object) -> list[str]:
    seen: set[str] = set()
    resolved: list[str] = []
    for value in values:
        if isinstance(value, str):
            text = value.strip()
            if text and text not in seen:
                seen.add(text)
                resolved.append(text)
            continue
        if isinstance(value, list):
            for item in value:
                text = str(item or "").strip()
                if text and text not in seen:
                    seen.add(text)
                    resolved.append(text)
    return resolved


class ProtocolBundleReference(ContractModel):
    adapter_id: str | None = None
    plugin_id: str | None = None
    workflow_id: str | None = None
    bridge_id: str | None = None
    display_id: str | None = None
    aliases: list[str] = Field(default_factory=list)
    path: str = Field(min_length=1)


class ProtocolBundleManifest(ContractModel):
    bundle_id: str = Field(min_length=1)
    bundle_version: str = Field(min_length=1)
    contract_version: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    default_bridge_id: str = Field(min_length=1)
    adapters: list[ProtocolBundleReference] = Field(default_factory=list)
    plugins: list[ProtocolBundleReference] = Field(default_factory=list)
    workflows: list[ProtocolBundleReference] = Field(default_factory=list)
    worker_bridges: list[ProtocolBundleReference] = Field(default_factory=list)
    defaults: dict[str, Any] = Field(default_factory=dict)


class ProtocolAdapterCapability(ContractModel):
    capability_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    required_payload_keys: list[str] = Field(default_factory=list)
    optional_payload_keys: list[str] = Field(default_factory=list)
    normalized_result_keys: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProtocolAdapterManifest(ContractModel):
    adapter_id: str = Field(min_length=1)
    adapter_type: str = Field(min_length=1)
    adapter_version: str = Field(min_length=1)
    title: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    capabilities: list[ProtocolAdapterCapability] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    wraps: list[str] = Field(default_factory=list)
    is_builtin: bool = False
    workspace_compatibility: list[str] = Field(default_factory=lambda: ["local"])
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProtocolPluginManifest(ContractModel):
    plugin_id: str = Field(min_length=1)
    plugin_version: str = Field(min_length=1)
    plugin_type: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    supported_subjects: list[str] = Field(default_factory=list)
    input_schema_version: str = Field(default="v1", min_length=1)
    output_schema_version: str = Field(default="v1", min_length=1)
    artifact_kinds: list[str] = Field(default_factory=list)
    worker_actions: list[str] = Field(default_factory=list)
    default_benchmark_profiles: list[str] = Field(default_factory=list)
    default_gate_profiles: list[str] = Field(default_factory=list)
    search_space_fields: list[str] = Field(default_factory=list)
    compatible_workflows: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkflowIterationSpec(ContractModel):
    budget_field: str = Field(default="iteration_budget", min_length=1)
    decision_step_id: str = Field(min_length=1)
    reentry_step_id: str = Field(min_length=1)


class ProtocolWorkflowStep(ContractModel):
    step_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    plugin_id: str = Field(min_length=1)
    action_id: str = Field(min_length=1)
    stage: str = Field(min_length=1)
    description: str = Field(min_length=1)
    depends_on: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    optional: bool = False
    allow_reentry: bool = False
    loop_scope: str | None = None
    skip_if_session_has: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProtocolWorkflowManifest(ContractModel):
    workflow_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    display_id: str | None = None
    aliases: list[str] = Field(default_factory=list)
    subject_kind: str = Field(default="skill", min_length=1)
    iterative: bool = False
    human_review_required: bool = False
    entry_steps: list[str] = Field(default_factory=list)
    terminal_steps: list[str] = Field(default_factory=list)
    iteration: WorkflowIterationSpec | None = None
    steps: list[ProtocolWorkflowStep] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_iteration(self) -> "ProtocolWorkflowManifest":
        if self.iterative and self.iteration is None:
            raise ValueError(f"workflow {self.workflow_id!r} is iterative but does not define iteration metadata")
        return self


class BridgeLauncherSpec(ContractModel):
    launcher_id: str = Field(min_length=1)
    argv: list[str] = Field(default_factory=list)


class BridgeIoSpec(ContractModel):
    request_file_arg: str = Field(min_length=1)
    response_file_arg: str = Field(min_length=1)
    request_contract_version: str = Field(min_length=1)
    response_contract_version: str = Field(min_length=1)


class BridgeActionSpec(ContractModel):
    plugin_id: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    required_request_fields: list[str] = Field(default_factory=list)
    required_inputs: list[str] = Field(default_factory=list)
    produces: list[str] = Field(default_factory=list)
    control_fields: list[str] = Field(default_factory=list)


class WorkerBridgeManifest(ContractModel):
    bridge_id: str = Field(min_length=1)
    bridge_version: str = Field(min_length=1)
    contract_version: str = Field(min_length=1)
    description: str = Field(min_length=1)
    runtime_kind: str = Field(min_length=1)
    preferred_launchers: list[BridgeLauncherSpec] = Field(default_factory=list)
    io: BridgeIoSpec
    actions: dict[str, BridgeActionSpec] = Field(default_factory=dict)


class NextgenProtocolBundle(ContractModel):
    root: str = Field(min_length=1)
    manifest: ProtocolBundleManifest
    adapters: dict[str, ProtocolAdapterManifest] = Field(default_factory=dict)
    plugins: dict[str, ProtocolPluginManifest] = Field(default_factory=dict)
    workflows: dict[str, ProtocolWorkflowManifest] = Field(default_factory=dict)
    bridges: dict[str, WorkerBridgeManifest] = Field(default_factory=dict)

    def default_bridge(self) -> WorkerBridgeManifest:
        return self.bridges[self.manifest.default_bridge_id]

    def resolve_workflow(self, workflow_ref: str) -> ProtocolWorkflowManifest:
        candidate = str(workflow_ref or "").strip()
        if candidate in self.workflows:
            return self.workflows[candidate]
        for workflow in self.workflows.values():
            invocation_ids = _string_list(
                workflow.workflow_id,
                workflow.display_id,
                workflow.aliases,
                dict(workflow.metadata).get("invocation_ids"),
                dict(workflow.metadata).get("aliases"),
            )
            if candidate in invocation_ids:
                return workflow
        raise KeyError(f"unknown nextgen workflow reference: {workflow_ref!r}")


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_nextgen_protocol_bundle(*, project_root: Path | None = None) -> NextgenProtocolBundle:
    resolved_root = (project_root or _default_project_root()).expanduser().resolve()
    manifest_path = resolved_root / NEXTGEN_PROTOCOL_MANIFEST
    manifest = ProtocolBundleManifest.model_validate(_load_json(manifest_path))
    adapters: dict[str, ProtocolAdapterManifest] = {}
    plugins: dict[str, ProtocolPluginManifest] = {}
    workflows: dict[str, ProtocolWorkflowManifest] = {}
    bridges: dict[str, WorkerBridgeManifest] = {}
    for reference in manifest.adapters:
        adapters[str(reference.adapter_id)] = ProtocolAdapterManifest.model_validate(
            _load_json(resolved_root / reference.path)
        )
    for reference in manifest.plugins:
        plugins[str(reference.plugin_id)] = ProtocolPluginManifest.model_validate(
            _load_json(resolved_root / reference.path)
        )
    for reference in manifest.workflows:
        workflow_payload = _load_json(resolved_root / reference.path)
        workflow_metadata = dict(workflow_payload.get("metadata") or {})
        workflow_payload["display_id"] = workflow_payload.get("display_id") or reference.display_id
        workflow_payload["aliases"] = _string_list(
            workflow_payload.get("aliases"),
            reference.aliases,
            reference.display_id,
        )
        workflow_payload["metadata"] = {
            **workflow_metadata,
            "protocol_namespace": "0t",
            "protocol_bundle_dir": PROTOCOL_BUNDLE_DIR,
            "canonical_workflow_id": workflow_payload.get("workflow_id") or reference.workflow_id,
            "display_workflow_id": workflow_payload.get("display_id") or reference.display_id or reference.workflow_id,
            "invocation_ids": _string_list(
                workflow_payload.get("workflow_id") or reference.workflow_id,
                workflow_payload.get("display_id") or reference.display_id,
                workflow_payload.get("aliases"),
                reference.aliases,
            ),
        }
        workflows[str(reference.workflow_id)] = ProtocolWorkflowManifest.model_validate(workflow_payload)
    for reference in manifest.worker_bridges:
        bridges[str(reference.bridge_id)] = WorkerBridgeManifest.model_validate(
            _load_json(resolved_root / reference.path)
        )
    return NextgenProtocolBundle(
        root=str(resolved_root),
        manifest=manifest,
        adapters=adapters,
        plugins=plugins,
        workflows=workflows,
        bridges=bridges,
    )


def build_adapter_manifests(bundle: NextgenProtocolBundle) -> dict[str, AdapterManifest]:
    manifests: dict[str, AdapterManifest] = {}
    for adapter in bundle.adapters.values():
        manifests[adapter.adapter_id] = AdapterManifest(
            adapter_id=adapter.adapter_id,
            adapter_type=adapter.adapter_type,  # type: ignore[arg-type]
            adapter_version=adapter.adapter_version,
            title=adapter.title,
            summary=adapter.summary,
            capabilities=tuple(
                AdapterCapability(
                    capability_id=item.capability_id,
                    display_name=item.display_name,
                    description=item.description,
                    tags=tuple(item.tags),
                    required_payload_keys=tuple(item.required_payload_keys),
                    optional_payload_keys=tuple(item.optional_payload_keys),
                    normalized_result_keys=tuple(item.normalized_result_keys),
                    metadata=dict(item.metadata),
                )
                for item in adapter.capabilities
            ),
            tags=tuple(adapter.tags),
            wraps=tuple(adapter.wraps),
            is_builtin=adapter.is_builtin,
            workspace_compatibility=tuple(adapter.workspace_compatibility),
            metadata=dict(adapter.metadata),
        )
    return manifests


def build_plugin_registry_specs(bundle: NextgenProtocolBundle) -> tuple[list[WorkflowPluginSpec], list[WorkflowGraphSpec]]:
    plugin_specs = []
    for plugin in bundle.plugins.values():
        metadata = {**dict(plugin.metadata), "worker_actions": list(plugin.worker_actions)}
        if plugin.plugin_id == "distillation":
            metadata["worker_bridge"] = {
                "protocol_id": "distillation.wallet_style",
                "schema_version": "distillation-worker-bridge.v1alpha1",
                "operation_order": ["plan", "execute", "validate", "summarize"],
                "capability_bindings": {
                    "plan": "distill_wallet_style",
                    "execute": "emit_seed_skill",
                    "validate": "emit_seed_skill",
                    "summarize": "emit_seed_skill",
                },
                "baseline_artifact_kinds": [
                    "style_profile",
                    "strategy_spec",
                    "execution_intent",
                    "distillation_report",
                    "seed_skill_package",
                ],
                "compat_result_key": "raw_distillation_result",
            }
        plugin_specs.append(
            WorkflowPluginSpec(
                plugin_id=plugin.plugin_id,
                plugin_version=plugin.plugin_version,
                plugin_type=plugin.plugin_type,  # type: ignore[arg-type]
                display_name=plugin.display_name,
                summary=plugin.summary,
                supported_subjects=list(plugin.supported_subjects),
                input_schema_version=plugin.input_schema_version,
                output_schema_version=plugin.output_schema_version,
                artifact_kinds=list(plugin.artifact_kinds),
                default_benchmark_profiles=list(plugin.default_benchmark_profiles),
                default_gate_profiles=list(plugin.default_gate_profiles),
                search_space_fields=list(plugin.search_space_fields),
                compatible_workflows=list(plugin.compatible_workflows),
                metadata=metadata,
            )
        )
    workflow_specs = []
    for workflow in bundle.workflows.values():
        workflow_specs.append(
            WorkflowGraphSpec(
                workflow_id=workflow.workflow_id,
                title=workflow.title,
                description=workflow.description,
                subject_kind=workflow.subject_kind,
                steps=[
                    WorkflowStepSpec(
                        step_id=step.step_id,
                        title=step.title,
                        plugin_id=step.plugin_id,
                        stage=step.stage,  # type: ignore[arg-type]
                        description=step.description,
                        depends_on=list(step.depends_on),
                        optional=step.optional,
                        allow_reentry=step.allow_reentry,
                        metadata={
                            **dict(step.metadata),
                            "action_id": step.action_id,
                            "outputs": list(step.outputs),
                            "loop_scope": step.loop_scope,
                            "skip_if_session_has": list(step.skip_if_session_has),
                            **(
                                {
                                    "worker_bridge": {
                                        "protocol_id": "distillation.wallet_style",
                                        "operation_order": ["plan", "execute", "validate", "summarize"],
                                    }
                                }
                                if step.plugin_id == "distillation"
                                else {}
                            ),
                        },
                    )
                    for step in workflow.steps
                ],
                entry_steps=list(workflow.entry_steps),
                terminal_steps=list(workflow.terminal_steps),
                iterative=workflow.iterative,
                human_review_required=workflow.human_review_required,
                metadata={
                    "iteration": workflow.iteration.model_dump(mode="json") if workflow.iteration is not None else None,
                    **dict(workflow.metadata),
                    "display_workflow_id": workflow.display_id or dict(workflow.metadata).get("display_workflow_id"),
                    "aliases": list(workflow.aliases),
                },
            )
        )
    return plugin_specs, workflow_specs
