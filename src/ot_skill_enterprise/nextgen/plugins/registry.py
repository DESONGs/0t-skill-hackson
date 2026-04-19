from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ot_skill_enterprise.nextgen.protocol import build_plugin_registry_specs, load_nextgen_protocol_bundle

from .models import PluginCapabilitySpec, WorkflowGraphSpec, WorkflowPluginSpec, WorkflowStepSpec


@dataclass(slots=True)
class PluginRegistration:
    plugin_id: str
    spec: WorkflowPluginSpec
    builtin: bool = False
    tags: tuple[str, ...] = field(default_factory=tuple)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class WorkflowRegistration:
    workflow_id: str
    spec: WorkflowGraphSpec
    builtin: bool = False
    tags: tuple[str, ...] = field(default_factory=tuple)
    metadata: dict[str, Any] = field(default_factory=dict)


class WorkflowPluginRegistry:
    def __init__(self) -> None:
        self._plugins: dict[str, PluginRegistration] = {}
        self._workflows: dict[str, WorkflowRegistration] = {}

    def register_plugin(
        self,
        spec: WorkflowPluginSpec,
        *,
        builtin: bool = False,
        tags: tuple[str, ...] = (),
        metadata: dict[str, Any] | None = None,
    ) -> PluginRegistration:
        registration = PluginRegistration(
            plugin_id=spec.plugin_id,
            spec=spec,
            builtin=builtin,
            tags=tuple(tags),
            metadata=dict(metadata or {}),
        )
        self._plugins[spec.plugin_id] = registration
        return registration

    def register_workflow(
        self,
        spec: WorkflowGraphSpec,
        *,
        builtin: bool = False,
        tags: tuple[str, ...] = (),
        metadata: dict[str, Any] | None = None,
    ) -> WorkflowRegistration:
        missing_plugins = sorted(set(spec.plugin_ids()) - set(self._plugins))
        if missing_plugins:
            raise KeyError(
                f"workflow {spec.workflow_id!r} references unregistered plugins {missing_plugins}"
            )
        registration = WorkflowRegistration(
            workflow_id=spec.workflow_id,
            spec=spec,
            builtin=builtin,
            tags=tuple(tags),
            metadata=dict(metadata or {}),
        )
        self._workflows[spec.workflow_id] = registration
        return registration

    def get_plugin(self, plugin_id: str) -> PluginRegistration | None:
        return self._plugins.get(plugin_id)

    def get_workflow(self, workflow_id: str) -> WorkflowRegistration | None:
        return self._workflows.get(workflow_id)

    def resolve_plugin(self, plugin_id: str) -> WorkflowPluginSpec:
        registration = self.get_plugin(plugin_id)
        if registration is None:
            raise KeyError(f"no plugin registered for {plugin_id!r}")
        return registration.spec

    def resolve_workflow(self, workflow_id: str) -> WorkflowGraphSpec:
        registration = self.get_workflow(workflow_id)
        if registration is None:
            raise KeyError(f"no workflow registered for {workflow_id!r}")
        return registration.spec

    def resolve_step(self, workflow_id: str, step_id: str) -> WorkflowStepSpec:
        return self.resolve_workflow(workflow_id).step(step_id)

    def resolve_step_action(self, workflow_id: str, step_id: str) -> str:
        action_id = self.resolve_step(workflow_id, step_id).action_id()
        if action_id is None:
            raise KeyError(f"workflow {workflow_id!r} step {step_id!r} does not declare an action_id")
        return action_id

    def list_plugins(self, *, builtin_only: bool = False) -> list[PluginRegistration]:
        registrations = list(self._plugins.values())
        if builtin_only:
            registrations = [item for item in registrations if item.builtin]
        return registrations

    def list_workflows(self, *, builtin_only: bool = False) -> list[WorkflowRegistration]:
        registrations = list(self._workflows.values())
        if builtin_only:
            registrations = [item for item in registrations if item.builtin]
        return registrations

    def workflows_for_plugin(self, plugin_id: str) -> list[WorkflowGraphSpec]:
        return [
            registration.spec
            for registration in self.list_workflows()
            if plugin_id in registration.spec.plugin_ids()
        ]

    def describe(self) -> dict[str, Any]:
        return {
            "plugins": [
                {
                    "plugin_id": registration.plugin_id,
                    "builtin": registration.builtin,
                    "tags": list(registration.tags),
                    "spec": registration.spec.model_dump(mode="json"),
                    "metadata": dict(registration.metadata),
                }
                for registration in self.list_plugins()
            ],
            "workflows": [
                {
                    "workflow_id": registration.workflow_id,
                    "builtin": registration.builtin,
                    "tags": list(registration.tags),
                    "spec": registration.spec.model_dump(mode="json"),
                    "metadata": dict(registration.metadata),
                }
                for registration in self.list_workflows()
            ],
        }


def _merge_unique_strings(*values: list[str] | tuple[str, ...]) -> list[str]:
    ordered: list[str] = []
    for group in values:
        for item in group:
            text = str(item or "").strip()
            if text and text not in ordered:
                ordered.append(text)
    return ordered


def _merge_metadata(base: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in defaults.items():
        if isinstance(value, dict):
            current = merged.get(key)
            merged[key] = _merge_metadata(dict(current) if isinstance(current, dict) else {}, value)
            continue
        if isinstance(value, list):
            current = merged.get(key)
            current_items = list(current) if isinstance(current, list) else []
            if all(not isinstance(item, (dict, list)) for item in [*current_items, *value]):
                merged[key] = _merge_unique_strings(current_items, value)
            elif key not in merged:
                merged[key] = [
                    _merge_metadata(dict(item), {}) if isinstance(item, dict) else item
                    for item in value
                ]
            continue
        merged.setdefault(key, value)
    return merged


def _plugin_capability_defaults() -> dict[str, list[PluginCapabilitySpec]]:
    return {
        "skill-creation": [
            PluginCapabilitySpec(
                capability_id="materialize_baseline",
                description="Normalize a distilled baseline into replayable skill-creation artifacts.",
                consumes=["baseline_variant", "distillation_result"],
                produces=["baseline_variant", "baseline_materialization_bundle"],
            ),
            PluginCapabilitySpec(
                capability_id="create_variants",
                description="Materialize planned variant mutations into runnable candidate artifacts.",
                consumes=["variant_plans", "parent_variant", "baseline_variant"],
                produces=["candidate_variants", "variant_materialization_bundle"],
            ),
        ],
        "autoresearch": [
            PluginCapabilitySpec(
                capability_id="plan_iteration",
                description="Plan the next research iteration and emit artifact-backed variant plans.",
                consumes=["baseline_variant", "parent_variant", "objective", "iteration_budget"],
                produces=["variant_plans"],
            ),
            PluginCapabilitySpec(
                capability_id="decide_iteration",
                description="Rank reviewed variants and decide whether the loop should continue.",
                consumes=["benchmark_scorecards", "review_decisions", "baseline_scorecard"],
                produces=["recommendation_bundle", "stop_decision"],
            ),
        ],
        "benchmark": [
            PluginCapabilitySpec(
                capability_id="score_baseline",
                description="Benchmark the baseline variant to establish the session floor.",
                consumes=["baseline_variant"],
                produces=["baseline_scorecard", "benchmark_artifact_bundle"],
            ),
            PluginCapabilitySpec(
                capability_id="score_candidates",
                description="Benchmark materialized candidate variants with declared profiles and gates.",
                consumes=["candidate_variants", "benchmark_profile", "gate_profile"],
                produces=["candidate_scorecards", "benchmark_artifact_bundle"],
            ),
        ],
        "review": [
            PluginCapabilitySpec(
                capability_id="evaluate_candidates",
                description="Evaluate benchmarked variants against governance policy and replay expectations.",
                consumes=["candidate_scorecards", "baseline_scorecard", "review_policy"],
                produces=["review_decisions", "review_artifact_bundle"],
            ),
        ],
        "approval-convergence": [
            PluginCapabilitySpec(
                capability_id="converge_approval",
                description="Aggregate final benchmark and review outputs into an approval-ready governance bundle.",
                consumes=["candidate_scorecards", "review_decisions", "recommendation_bundle", "approval_policy"],
                produces=["approval_activation_bundle", "approval_recommendation", "kernel_handoff_payload"],
            ),
        ],
    }


def _plugin_metadata_defaults() -> dict[str, dict[str, Any]]:
    return {
        "skill-creation": {
            "worker_tier": "python-domain-worker",
            "worker_actions": [
                "skill_creation.materialize_baseline",
                "skill_creation.create_variants",
            ],
            "action_contracts": {
                "skill_creation.materialize_baseline": {
                    "artifact_kinds": [
                        "baseline_materialization",
                        "skill_package",
                        "validation_report",
                        "qa_report",
                        "kernel_replay_payload",
                    ],
                },
                "skill_creation.create_variants": {
                    "artifact_kinds": [
                        "research_plan",
                        "candidate_variant",
                        "variant_materialization",
                        "skill_package",
                        "validation_report",
                        "kernel_replay_payload",
                    ],
                },
            },
        },
        "autoresearch": {
            "worker_actions": [
                "autoresearch.plan_iteration",
                "autoresearch.decide_iteration",
            ],
            "action_contracts": {
                "autoresearch.plan_iteration": {
                    "artifact_kinds": ["research_plan", "kernel_replay_payload"],
                    "variant_templates": [
                        {
                            "template_id": "risk-discipline",
                            "title": "risk-discipline",
                            "hypothesis": "Tighter risk controls should improve readiness without excessive drift.",
                            "change_summary": "Tighten sizing, add stricter invalidation, and preserve the current style focus.",
                            "mutations": {
                                "target_fields": ["risk_filters", "sizing"],
                                "strategy": {
                                    "summary_suffix": "Tighten sizing and add stricter invalidation.",
                                    "risk_controls_append": ["size discipline", "invalidation discipline"],
                                },
                                "execution": {
                                    "max_position_pct_scale": 0.9,
                                    "preflight_checks_append": ["balance-check"],
                                    "metadata": {"research_focus": "risk discipline"},
                                },
                            },
                        },
                        {
                            "template_id": "conviction-overlay",
                            "title": "conviction-overlay",
                            "hypothesis": "A stronger conviction overlay may raise quality without breaking style lineage.",
                            "change_summary": "Add an archetype-aligned conviction overlay while preserving baseline risk controls.",
                            "mutations": {
                                "target_fields": ["timing", "strategy_spec"],
                                "strategy": {
                                    "summary_suffix": "Allow higher conviction entries when the archetype bias aligns.",
                                    "entry_conditions_append": [
                                        {
                                            "condition": "archetype bias aligned",
                                            "data_source": "research_overlay",
                                            "weight": 0.65,
                                            "rationale": "Prefer entries when variant conviction matches baseline token preference.",
                                        }
                                    ],
                                },
                                "execution": {
                                    "preflight_checks_append": ["route-check"],
                                    "metadata": {"research_focus": "conviction overlay"},
                                },
                            },
                        },
                        {
                            "template_id": "pace-layering",
                            "title": "pace-layering",
                            "hypothesis": "A staged pace profile can improve confidence without increasing size risk.",
                            "change_summary": "Introduce staged pacing and confirmation layering to reduce noise in entry timing.",
                            "mutations": {
                                "target_fields": ["timing", "pacing", "execution_intent"],
                                "strategy": {
                                    "summary_suffix": "Add staged confirmations before triggering the full position.",
                                    "entry_conditions_append": [
                                        {
                                            "condition": "confirmation leg fills cleanly",
                                            "data_source": "research_overlay",
                                            "weight": 0.55,
                                            "rationale": "Require a staged confirmation before sizing into the full setup.",
                                        }
                                    ],
                                },
                                "execution": {
                                    "field_overrides": {"split_legs": True, "leg_count": 3},
                                    "metadata": {"research_focus": "pace layering"},
                                },
                            },
                        },
                    ],
                },
                "autoresearch.decide_iteration": {
                    "artifact_kinds": [
                        "recommendation_bundle",
                        "leaderboard",
                        "stop_decision",
                        "kernel_handoff_payload",
                    ],
                    "recommendation_policy": {
                        "recommended_quality_delta": 0.03,
                        "keep_quality_delta": 0.0,
                        "recommended_noise_delta": 0.0,
                        "max_style_distance_for_auto": 0.35,
                        "max_risk_penalty_for_auto": 0.3,
                        "min_quality_score": 0.58,
                    },
                },
            },
        },
        "benchmark": {
            "action_contracts": {
                "benchmark.score_baseline": {
                    "benchmark_profile": "default-research",
                    "gate_profile": ["readiness", "validation", "qa"],
                },
                "benchmark.score_candidates": {
                    "benchmark_profile": "default-research",
                    "gate_profile": ["style-drift", "risk-floor", "readiness", "validation", "qa"],
                },
                "benchmark.score_finalist": {
                    "benchmark_profile": "final-approval",
                    "gate_profile": ["style-drift", "risk-floor", "readiness", "validation", "qa"],
                },
            },
            "benchmark_profiles": {
                "default-research": {
                    "score_weights": {
                        "entry_signal": 0.2,
                        "risk_controls": 0.14,
                        "style_overlap": 0.12,
                        "adapter_alignment": 0.1,
                        "validation": 0.14,
                        "qa": 0.1,
                        "backtest": 0.2,
                    }
                },
                "final-approval": {
                    "score_weights": {
                        "entry_signal": 0.16,
                        "risk_controls": 0.16,
                        "style_overlap": 0.12,
                        "adapter_alignment": 0.1,
                        "validation": 0.16,
                        "qa": 0.14,
                        "backtest": 0.16,
                    }
                },
            },
            "gate_policies": {
                "style-drift": {"max_style_distance": 0.45},
                "risk-floor": {"max_risk_penalty": 0.3},
                "readiness": {"required_execution_readiness": "ready"},
                "validation": {"required_ok": True},
                "qa": {"required_status": "passed"},
            },
        },
        "review": {
            "action_contracts": {
                "review.evaluate_candidates": {
                    "artifact_kinds": [
                        "review_governance",
                        "review_notes",
                        "retry_suggestion",
                        "kernel_handoff_payload",
                    ],
                    "review_policy": {
                        "recommended_quality_delta": 0.03,
                        "keep_quality_delta": 0.0,
                        "recommended_noise_delta": 0.0,
                        "max_style_distance_for_auto": 0.35,
                        "max_risk_penalty_for_auto": 0.3,
                        "min_quality_score": 0.58,
                    },
                },
                "review.finalize_candidate": {
                    "artifact_kinds": [
                        "review_governance",
                        "review_notes",
                        "retry_suggestion",
                        "kernel_handoff_payload",
                    ],
                    "review_policy": {
                        "recommended_quality_delta": 0.02,
                        "keep_quality_delta": 0.0,
                        "recommended_noise_delta": -0.02,
                        "max_style_distance_for_auto": 0.35,
                        "max_risk_penalty_for_auto": 0.28,
                        "min_quality_score": 0.6,
                    },
                },
            }
        },
        "approval-convergence": {
            "composes_with": ["benchmark", "review"],
            "worker_actions": [
                "approval_convergence.converge_approval",
            ],
            "action_contracts": {
                "approval_convergence.converge_approval": {
                    "artifact_kinds": [
                        "approval_activation_bundle",
                        "approval_recommendation",
                        "kernel_handoff_payload",
                    ],
                    "approval_policy": {
                        "requires_recommendation": True,
                        "allowed_review_statuses": ["recommended", "keep"],
                        "activation_gate": "review.governance.activation_allowed",
                    },
                },
            },
        },
    }


def _workflow_defaults() -> dict[str, WorkflowGraphSpec]:
    return {
        "distillation_seed": WorkflowGraphSpec(
            workflow_id="distillation_seed",
            title="Distillation Seed Workflow",
            description="Produce a distilled baseline and normalize it into a replayable seed package.",
            steps=[
                WorkflowStepSpec(
                    step_id="distill_baseline",
                    title="Distill Baseline",
                    plugin_id="distillation",
                    stage="seed",
                    description="Generate the initial wallet-style baseline from source wallet context.",
                    metadata={
                        "action_id": "distillation.execute",
                        "role_hint": "planner",
                        "outputs": ["baseline_variant", "baseline_artifacts"],
                        "worker_bridge": {
                            "protocol_id": "distillation.wallet_style",
                            "operation_order": ["plan", "execute", "validate", "summarize"],
                        },
                    },
                ),
                WorkflowStepSpec(
                    step_id="materialize_baseline",
                    title="Materialize Baseline",
                    plugin_id="skill-creation",
                    stage="execute",
                    description="Convert the distilled baseline into replayable package, validation, and QA artifacts.",
                    depends_on=["distill_baseline"],
                    metadata={
                        "action_id": "skill_creation.materialize_baseline",
                        "role_hint": "optimizer",
                        "outputs": ["baseline_variant", "baseline_materialization_bundle"],
                    },
                ),
            ],
            terminal_steps=["materialize_baseline"],
            human_review_required=False,
            metadata={"publish_action": "publish_seed"},
        ),
        "autonomous_research": WorkflowGraphSpec(
            workflow_id="autonomous_research",
            title="Autonomous Research Workflow",
            description="Run an explicit plan -> variant -> benchmark -> review -> decide loop on top of a distilled baseline.",
            steps=[
                WorkflowStepSpec(
                    step_id="seed_baseline",
                    title="Seed Baseline",
                    plugin_id="distillation",
                    stage="seed",
                    description="Generate the baseline skill seed unless the request already provides one.",
                    metadata={
                        "action_id": "distillation.execute",
                        "role_hint": "planner",
                        "outputs": ["baseline_variant", "baseline_artifacts"],
                        "skip_if_session_has": ["baseline_variant"],
                        "worker_bridge": {
                            "protocol_id": "distillation.wallet_style",
                            "operation_order": ["plan", "execute", "validate", "summarize"],
                        },
                    },
                ),
                WorkflowStepSpec(
                    step_id="materialize_baseline",
                    title="Materialize Baseline",
                    plugin_id="skill-creation",
                    stage="execute",
                    description="Normalize the baseline into replayable materialization artifacts before benchmark starts.",
                    depends_on=["seed_baseline"],
                    metadata={
                        "action_id": "skill_creation.materialize_baseline",
                        "role_hint": "optimizer",
                        "outputs": ["baseline_variant", "baseline_materialization_bundle"],
                    },
                ),
                WorkflowStepSpec(
                    step_id="benchmark_baseline",
                    title="Benchmark Baseline",
                    plugin_id="benchmark",
                    stage="benchmark",
                    description="Benchmark the baseline variant to establish the comparison floor.",
                    depends_on=["materialize_baseline"],
                    metadata={
                        "action_id": "benchmark.score_baseline",
                        "role_hint": "reviewer",
                        "outputs": ["baseline_scorecard"],
                        "loop_scope": "workflow",
                    },
                ),
                WorkflowStepSpec(
                    step_id="plan_iteration",
                    title="Plan Iteration",
                    plugin_id="autoresearch",
                    stage="plan",
                    description="Plan the current iteration and emit artifact-backed variant plans.",
                    depends_on=["benchmark_baseline"],
                    metadata={
                        "action_id": "autoresearch.plan_iteration",
                        "role_hint": "planner",
                        "outputs": ["variant_plans"],
                        "loop_scope": "iteration",
                    },
                ),
                WorkflowStepSpec(
                    step_id="create_variants",
                    title="Create Variants",
                    plugin_id="skill-creation",
                    stage="execute",
                    description="Materialize planned variant mutations into replayable candidate artifacts.",
                    depends_on=["plan_iteration"],
                    metadata={
                        "action_id": "skill_creation.create_variants",
                        "role_hint": "optimizer",
                        "outputs": ["candidate_variants", "variant_materialization_bundle"],
                        "loop_scope": "iteration",
                    },
                ),
                WorkflowStepSpec(
                    step_id="benchmark_candidates",
                    title="Benchmark Candidates",
                    plugin_id="benchmark",
                    stage="benchmark",
                    description="Benchmark candidate variants with the standard profiles and hard gates.",
                    depends_on=["create_variants"],
                    metadata={
                        "action_id": "benchmark.score_candidates",
                        "role_hint": "reviewer",
                        "outputs": ["candidate_scorecards"],
                        "loop_scope": "iteration",
                    },
                ),
                WorkflowStepSpec(
                    step_id="review_candidates",
                    title="Review Candidates",
                    plugin_id="review",
                    stage="review",
                    description="Review benchmarked candidates against baseline style, risk, and replay expectations.",
                    depends_on=["benchmark_candidates"],
                    metadata={
                        "action_id": "review.evaluate_candidates",
                        "role_hint": "reviewer",
                        "outputs": ["review_decisions"],
                        "loop_scope": "iteration",
                    },
                ),
                WorkflowStepSpec(
                    step_id="decide_next_iteration",
                    title="Decide Next Iteration",
                    plugin_id="autoresearch",
                    stage="finalize",
                    description="Rank reviewed variants and decide whether to stop or loop again.",
                    depends_on=["review_candidates"],
                    allow_reentry=True,
                    metadata={
                        "action_id": "autoresearch.decide_iteration",
                        "role_hint": "planner",
                        "outputs": ["recommendation_bundle", "stop_decision"],
                        "loop_scope": "iteration",
                    },
                ),
            ],
            terminal_steps=["decide_next_iteration"],
            iterative=True,
            human_review_required=True,
            metadata={
                "iteration": {
                    "budget_field": "iteration_budget",
                    "decision_step_id": "decide_next_iteration",
                    "reentry_step_id": "plan_iteration",
                }
            },
        ),
        "approval_convergence": WorkflowGraphSpec(
            workflow_id="approval_convergence",
            title="Approval Convergence Workflow",
            description="Run final benchmark, review, and approval convergence passes on the selected recommendation.",
            steps=[
                WorkflowStepSpec(
                    step_id="benchmark_finalist",
                    title="Benchmark Finalist",
                    plugin_id="benchmark",
                    stage="benchmark",
                    description="Re-score the recommended finalist with the stricter approval profile.",
                    metadata={
                        "action_id": "benchmark.score_finalist",
                        "role_hint": "reviewer",
                        "outputs": ["candidate_scorecards"],
                    },
                ),
                WorkflowStepSpec(
                    step_id="review_finalist",
                    title="Review Finalist",
                    plugin_id="review",
                    stage="review",
                    description="Produce the final approval-ready review decision and governance bundle.",
                    depends_on=["benchmark_finalist"],
                    metadata={
                        "action_id": "review.finalize_candidate",
                        "role_hint": "reviewer",
                        "outputs": ["review_decisions", "review_decision"],
                    },
                ),
                WorkflowStepSpec(
                    step_id="converge_approval",
                    title="Converge Approval",
                    plugin_id="approval-convergence",
                    stage="finalize",
                    description="Aggregate final benchmark and review outputs into an approval-ready governance bundle.",
                    depends_on=["review_finalist"],
                    metadata={
                        "action_id": "approval_convergence.converge_approval",
                        "role_hint": "reviewer",
                        "outputs": [
                            "approval_activation_bundle",
                            "approval_recommendation",
                            "kernel_handoff_payload",
                        ],
                    },
                ),
            ],
            entry_steps=["benchmark_finalist"],
            terminal_steps=["converge_approval"],
            iterative=False,
            human_review_required=True,
            metadata={
                "approval": {
                    "requires_recommendation": True,
                    "status_field": "approval.status",
                    "activation_gate": "review.governance.activation_allowed",
                }
            },
        ),
    }


def _enrich_plugin_spec(spec: WorkflowPluginSpec) -> WorkflowPluginSpec:
    metadata_defaults = _plugin_metadata_defaults().get(spec.plugin_id, {})
    capability_defaults = _plugin_capability_defaults().get(spec.plugin_id, [])
    compatible_workflows = spec.compatible_workflows
    artifact_kinds = spec.artifact_kinds
    if spec.plugin_id == "skill-creation":
        compatible_workflows = ["distillation_seed", "autonomous_research"]
        artifact_kinds = [
            "baseline_materialization",
            "variant_materialization",
            "skill_package",
            "validation_report",
            "qa_report",
            "research_plan",
            "candidate_variant",
            "kernel_replay_payload",
        ]
    elif spec.plugin_id == "autoresearch":
        artifact_kinds = _merge_unique_strings(
            spec.artifact_kinds,
            ["research_plan", "candidate_variant", "recommendation_bundle", "leaderboard", "kernel_handoff_payload"],
        )
    elif spec.plugin_id == "benchmark":
        artifact_kinds = _merge_unique_strings(
            spec.artifact_kinds,
            ["benchmark_scorecard", "gate_result", "benchmark_artifact_bundle", "kernel_replay_payload"],
        )
    elif spec.plugin_id == "review":
        artifact_kinds = _merge_unique_strings(
            spec.artifact_kinds,
            ["review_governance", "review_notes", "retry_suggestion", "kernel_handoff_payload"],
        )
    elif spec.plugin_id == "approval-convergence":
        compatible_workflows = _merge_unique_strings(spec.compatible_workflows, ["approval_convergence"])
        artifact_kinds = _merge_unique_strings(
            spec.artifact_kinds,
            ["approval_activation_bundle", "approval_recommendation", "kernel_handoff_payload"],
        )
    metadata = _merge_metadata(spec.metadata, metadata_defaults)
    capabilities = spec.capabilities or capability_defaults
    return spec.model_copy(
        update={
            "capabilities": capabilities,
            "artifact_kinds": artifact_kinds,
            "compatible_workflows": compatible_workflows,
            "metadata": metadata,
        }
    )


def _skill_creation_plugin_spec() -> WorkflowPluginSpec:
    return WorkflowPluginSpec(
        plugin_id="skill-creation",
        plugin_version="v1alpha1",
        plugin_type="skill-creation",
        display_name="Skill Creation Plugin",
        summary="Materializes baseline and variant plans into replayable skill packages and validation artifacts.",
        supported_subjects=["skill", "skill-variant"],
        capabilities=_plugin_capability_defaults()["skill-creation"],
        artifact_kinds=[
            "baseline_materialization",
            "variant_materialization",
            "skill_package",
            "validation_report",
            "qa_report",
            "research_plan",
            "candidate_variant",
            "kernel_replay_payload",
        ],
        compatible_workflows=["distillation_seed", "autonomous_research"],
        metadata=_plugin_metadata_defaults()["skill-creation"],
    )


def _approval_convergence_plugin_spec() -> WorkflowPluginSpec:
    return WorkflowPluginSpec(
        plugin_id="approval-convergence",
        plugin_version="v1alpha1",
        plugin_type="approval-convergence",
        display_name="Approval Convergence Plugin",
        summary="Aggregates final benchmark and review outputs into an approval-ready governance bundle.",
        supported_subjects=["skill-variant", "promotion-candidate"],
        capabilities=_plugin_capability_defaults()["approval-convergence"],
        artifact_kinds=[
            "approval_activation_bundle",
            "approval_recommendation",
            "kernel_handoff_payload",
        ],
        compatible_workflows=["approval_convergence"],
        metadata=_plugin_metadata_defaults()["approval-convergence"],
    )


def _enrich_workflow_spec(spec: WorkflowGraphSpec) -> WorkflowGraphSpec:
    default = _workflow_defaults().get(spec.workflow_id)
    if default is None:
        return spec
    return default


def build_default_plugin_registry(*, project_root: Path | None = None) -> WorkflowPluginRegistry:
    registry = WorkflowPluginRegistry()
    bundle = load_nextgen_protocol_bundle(project_root=project_root)
    plugin_specs, workflow_specs = build_plugin_registry_specs(bundle)
    seen_plugins: set[str] = set()
    for spec in plugin_specs:
        enriched = _enrich_plugin_spec(spec)
        registry.register_plugin(enriched, builtin=True, tags=("nextgen", enriched.plugin_type))
        seen_plugins.add(enriched.plugin_id)
    if "skill-creation" not in seen_plugins:
        skill_creation = _skill_creation_plugin_spec()
        registry.register_plugin(skill_creation, builtin=True, tags=("nextgen", skill_creation.plugin_type))
    if "approval-convergence" not in seen_plugins:
        approval_convergence = _approval_convergence_plugin_spec()
        registry.register_plugin(
            approval_convergence,
            builtin=True,
            tags=("nextgen", approval_convergence.plugin_type),
        )
    for spec in workflow_specs:
        enriched = _enrich_workflow_spec(spec)
        registry.register_workflow(enriched, builtin=True, tags=("nextgen", "workflow"))
    return registry
