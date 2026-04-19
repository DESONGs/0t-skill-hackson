from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from ot_skill_enterprise.nextgen.workflows.models import WorkflowArtifact, WorkflowVariant
from ot_skill_enterprise.shared.contracts.common import ContractModel


DistillationWorkerSchemaVersion = Literal["distillation-worker-bridge.v1alpha1"]
DistillationWorkerOperation = Literal["plan", "execute", "validate", "summarize"]
DistillationWorkerResponseStatus = Literal["planned", "succeeded", "validated", "summarized", "failed"]

_REQUIRED_OPERATIONS = ("plan", "execute", "validate", "summarize")


class DistillationWorkerBridgeEvent(ContractModel):
    event_id: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    operation: DistillationWorkerOperation
    status: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    artifact_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DistillationWorkerProtocol(ContractModel):
    protocol_id: str = Field(min_length=1)
    schema_version: DistillationWorkerSchemaVersion = "distillation-worker-bridge.v1alpha1"
    plugin_id: Literal["distillation"] = "distillation"
    plugin_version: str = Field(min_length=1)
    workflow_id: str = Field(min_length=1)
    workflow_step_id: str = Field(min_length=1)
    operation_order: list[DistillationWorkerOperation] = Field(default_factory=lambda: list(_REQUIRED_OPERATIONS))
    capability_bindings: dict[DistillationWorkerOperation, str] = Field(default_factory=dict)
    baseline_artifact_kinds: list[str] = Field(default_factory=list)
    compat_result_key: str = Field(default="raw_distillation_result", min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_protocol(self) -> "DistillationWorkerProtocol":
        if len(self.operation_order) != len(set(self.operation_order)):
            raise ValueError("operation_order must not contain duplicates")
        missing_operations = [item for item in _REQUIRED_OPERATIONS if item not in self.operation_order]
        if missing_operations:
            raise ValueError(f"protocol is missing required operations {missing_operations}")
        missing_bindings = [item for item in _REQUIRED_OPERATIONS if item not in self.capability_bindings]
        if missing_bindings:
            raise ValueError(f"protocol is missing capability bindings for {missing_bindings}")
        return self


class DistillationWorkerBridgeRequest(ContractModel):
    schema_version: DistillationWorkerSchemaVersion = "distillation-worker-bridge.v1alpha1"
    plugin_id: Literal["distillation"] = "distillation"
    plugin_version: str = Field(min_length=1)
    workflow_id: str = Field(min_length=1)
    workflow_step_id: str = Field(min_length=1)
    operation: DistillationWorkerOperation
    capability_id: str = Field(min_length=1)
    wallet: str = Field(min_length=1)
    chain: str = Field(default="bsc", min_length=1)
    skill_name: str | None = None
    workspace_dir: str | None = None
    operator_hints: dict[str, Any] = Field(default_factory=dict)
    protocol: DistillationWorkerProtocol
    state: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DistillationWorkerBridgeResponse(ContractModel):
    schema_version: DistillationWorkerSchemaVersion = "distillation-worker-bridge.v1alpha1"
    plugin_id: Literal["distillation"] = "distillation"
    plugin_version: str = Field(min_length=1)
    workflow_id: str = Field(min_length=1)
    workflow_step_id: str = Field(min_length=1)
    operation: DistillationWorkerOperation
    status: DistillationWorkerResponseStatus
    summary: str = Field(min_length=1)
    baseline_variant: WorkflowVariant | None = None
    artifacts: list[WorkflowArtifact] = Field(default_factory=list)
    raw_result: dict[str, Any] = Field(default_factory=dict)
    compat_payload: dict[str, Any] = Field(default_factory=dict)
    events: list[DistillationWorkerBridgeEvent] = Field(default_factory=list)
    state: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
