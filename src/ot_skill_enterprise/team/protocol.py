from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import yaml

from ot_skill_enterprise.service_locator import project_root

from .models import TeamRoleSpec, WorkflowDefinition, WorkflowModuleSpec

PROTOCOL_BUNDLE_DIR = "0t-protocol"


@dataclass(frozen=True, slots=True)
class TeamProtocolBundle:
    root: Path
    manifest: dict
    roles: dict[str, TeamRoleSpec]
    role_docs: dict[str, str]
    workflows: dict[str, WorkflowDefinition]
    modules: dict[str, WorkflowModuleSpec]

    def workflow(self, workflow_id: str) -> WorkflowDefinition:
        if workflow_id not in self.workflows:
            raise KeyError(f"unknown workflow: {workflow_id}")
        return self.workflows[workflow_id]

    def resolve_workflow(self, workflow_ref: str) -> WorkflowDefinition:
        candidate = str(workflow_ref or "").strip()
        if not candidate:
            raise KeyError("unknown workflow: ")
        if candidate in self.workflows:
            return self.workflows[candidate]
        for workflow in self.workflows.values():
            invocation_ids = _string_list(
                workflow.workflow_id,
                workflow.metadata.get("invocation_ids"),
                workflow.metadata.get("aliases"),
                workflow.metadata.get("kernel_workflow_id"),
            )
            if candidate in invocation_ids:
                return workflow
        raise KeyError(f"unknown workflow: {workflow_ref}")

    def module(self, module_id: str) -> WorkflowModuleSpec:
        if module_id not in self.modules:
            raise KeyError(f"unknown module: {module_id}")
        return self.modules[module_id]

    def role(self, role_id: str) -> TeamRoleSpec:
        if role_id not in self.roles:
            raise KeyError(f"unknown role: {role_id}")
        return self.roles[role_id]

    @property
    def entrypoint_path(self) -> Path:
        return self.root / "ENTRYPOINT.md"


def _load_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"expected object payload in {path}")
    return payload


def _load_yaml(path: Path) -> dict:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"expected mapping payload in {path}")
    return payload


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


def _normalize_workflow(payload: dict) -> WorkflowDefinition:
    stages = payload.get("stages") if isinstance(payload.get("stages"), list) else []
    declared_roles = payload.get("roles") if isinstance(payload.get("roles"), list) else []
    roles = _string_list(
        [str(stage.get("role")) for stage in stages if isinstance(stage, dict) and str(stage.get("role") or "").strip()],
        declared_roles,
    )
    default_adapter_family = payload.get("default_adapter_family") or "codex"
    invocation_ids = _string_list(
        payload.get("id") or payload.get("workflow") or payload.get("name"),
        payload.get("workflow_id"),
        payload.get("invocation_ids"),
        payload.get("aliases"),
    )
    metadata_payload = dict(payload.get("metadata") or {})
    kernel_workflow_id = (
        str(
            payload.get("kernel_workflow_id")
            or payload.get("canonical_workflow_id")
            or payload.get("nextgen_workflow_id")
            or metadata_payload.get("kernel_workflow_id")
            or payload.get("id")
            or payload.get("workflow_id")
            or payload.get("workflow")
            or payload.get("name")
            or ""
        ).strip()
        or None
    )
    normalized = {
        "workflow_id": payload.get("workflow_id") or payload.get("id") or payload.get("workflow") or payload.get("name"),
        "title": payload.get("title") or payload.get("name") or payload.get("workflow_id") or payload.get("id"),
        "description": payload.get("description") or payload.get("summary") or payload.get("purpose") or "",
        "module_id": payload.get("module") or payload.get("module_id") or payload.get("id"),
        "default_adapter_family": default_adapter_family,
        "team_topology": payload.get("team_topology") or "homogeneous",
        "roles": roles,
        "search_space": list(payload.get("search_space") or payload.get("inputs") or []),
        "hard_gates": list(payload.get("hard_gates") or payload.get("stop_conditions") or []),
        "metadata": {
            **metadata_payload,
            "entry_role": payload.get("entry_role"),
            "loop": payload.get("loop"),
            "handoff_format": payload.get("handoff_format"),
            "protocol_namespace": "0t",
            "protocol_bundle_dir": PROTOCOL_BUNDLE_DIR,
            "kernel_workflow_id": kernel_workflow_id,
            "invocation_ids": invocation_ids,
            "supported_adapter_families": _string_list(
                payload.get("supported_adapter_families"),
                payload.get("adapter_families"),
                default_adapter_family,
            ),
            "role_ids": _string_list(roles),
        },
    }
    return WorkflowDefinition.model_validate(normalized)


def _normalize_module(payload: dict) -> WorkflowModuleSpec:
    if "module_id" in payload:
        return WorkflowModuleSpec.model_validate(payload)
    normalized = {
        "module_id": payload.get("id") or payload.get("module_id") or payload.get("title"),
        "module_version": payload.get("version") or "1.0.0",
        "capability_type": payload.get("capability_type") or "workflow_optimizer",
        "target_subjects": list(payload.get("target_subjects") or ["skill"]),
        "search_space_schema": payload.get("search_space_schema")
        or {
            "allowed_fields": [
                "strategy_spec",
                "execution_intent",
                "risk_filters",
                "timing",
                "sizing",
                "pacing",
                "candidate_generation_thresholds",
            ]
        },
        "benchmark_profiles": list(payload.get("benchmark_profiles") or [{"profile_id": "autoresearch-default"}]),
        "gate_profiles": list(payload.get("gate_profiles") or [{"profile_id": "autoresearch-hard-gates", "rules": payload.get("quality_gates") or []}]),
        "decision_policy": payload.get("decision_policy")
        or {
            "max_style_distance": 0.35,
            "min_confidence_vs_noise": 0.0,
            "routing": payload.get("routing") or {},
        },
        "supported_team_topologies": list(payload.get("supported_team_topologies") or ["homogeneous"]),
        "workspace_compatibility": payload.get("workspace_compatibility") or {"mode": "multi-workspace"},
        "metadata": {
            "purpose": payload.get("purpose"),
            "artifact_type": payload.get("artifact_type"),
            "inputs": payload.get("inputs"),
            "outputs": payload.get("outputs"),
        },
    }
    return WorkflowModuleSpec.model_validate(normalized)


def load_team_protocol_bundle(root: Path | None = None) -> TeamProtocolBundle:
    protocol_root = (Path(root).expanduser().resolve() if root is not None else project_root()) / PROTOCOL_BUNDLE_DIR
    manifest = _load_json(protocol_root / "manifest.json")
    workflow_manifest_entries = {
        str(item.get("id") or "").strip(): dict(item)
        for item in list(manifest.get("workflows") or [])
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }

    roles_dir = protocol_root / "roles"
    roles: dict[str, TeamRoleSpec] = {}
    role_docs: dict[str, str] = {}
    for path in sorted(roles_dir.glob("*.md")):
        role_id = path.stem
        text = path.read_text(encoding="utf-8")
        title = role_id.replace("-", " ").title()
        description = text.strip().splitlines()[0].lstrip("# ").strip() if text.strip() else title
        roles[role_id] = TeamRoleSpec(role_id=role_id, title=title, description=description)
        role_docs[role_id] = text

    workflows_dir = protocol_root / "workflows"
    workflows: dict[str, WorkflowDefinition] = {}
    for path in sorted(workflows_dir.glob("*.yaml")):
        payload = _load_yaml(path)
        workflow = _normalize_workflow(payload)
        manifest_entry = workflow_manifest_entries.get(workflow.workflow_id, {})
        if manifest_entry:
            workflow = workflow.model_copy(
                update={
                    "metadata": {
                        **dict(workflow.metadata),
                        "kernel_workflow_id": manifest_entry.get("kernel_workflow_id")
                        or workflow.metadata.get("kernel_workflow_id"),
                        "invocation_ids": _string_list(
                            workflow.metadata.get("invocation_ids"),
                            manifest_entry.get("invocation_ids"),
                            manifest_entry.get("aliases"),
                        ),
                        "supported_adapter_families": _string_list(
                            workflow.metadata.get("supported_adapter_families"),
                            manifest_entry.get("supported_adapter_families"),
                            manifest_entry.get("adapter_families"),
                        ),
                        "default_entry_role": manifest_entry.get("default_entry_role")
                        or workflow.metadata.get("entry_role"),
                    }
                }
            )
        workflows[workflow.workflow_id] = workflow

    modules_dir = protocol_root / "modules"
    modules: dict[str, WorkflowModuleSpec] = {}
    for path in sorted(modules_dir.glob("*.json")):
        payload = _load_json(path)
        module = _normalize_module(payload)
        modules[module.module_id] = module

    return TeamProtocolBundle(
        root=protocol_root,
        manifest=manifest,
        roles=roles,
        role_docs=role_docs,
        workflows=workflows,
        modules=modules,
    )
