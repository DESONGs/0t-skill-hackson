from __future__ import annotations

import pytest

from ot_skill_enterprise.nextgen.plugins.models import WorkflowGraphSpec, WorkflowStepSpec
from ot_skill_enterprise.nextgen.plugins.registry import build_default_plugin_registry


def test_default_registry_exposes_builtin_plugins_and_workflows() -> None:
    registry = build_default_plugin_registry()

    plugin_ids = {item.plugin_id for item in registry.list_plugins(builtin_only=True)}
    workflow_ids = {item.workflow_id for item in registry.list_workflows(builtin_only=True)}

    assert plugin_ids == {
        "distillation",
        "skill-creation",
        "autoresearch",
        "benchmark",
        "review",
        "approval-convergence",
    }
    assert workflow_ids >= {"distillation_seed", "autonomous_research", "approval_convergence"}


def test_autonomous_research_workflow_composes_benchmark_and_review() -> None:
    registry = build_default_plugin_registry()

    workflow = registry.resolve_workflow("autonomous_research")
    autoresearch = registry.resolve_plugin("autoresearch")
    skill_creation = registry.resolve_plugin("skill-creation")

    assert workflow.iterative is True
    assert workflow.human_review_required is True
    assert workflow.plugin_ids() == ("distillation", "skill-creation", "benchmark", "autoresearch", "review")
    assert workflow.step("seed_baseline").plugin_id == "distillation"
    assert workflow.step("materialize_baseline").plugin_id == "skill-creation"
    assert workflow.step("benchmark_baseline").action_id() == "benchmark.score_baseline"
    assert workflow.step("plan_iteration").plugin_id == "autoresearch"
    assert workflow.step("plan_iteration").action_id() == "autoresearch.plan_iteration"
    assert workflow.step("plan_iteration").role_hint() == "planner"
    assert workflow.step("create_variants").plugin_id == "skill-creation"
    assert workflow.step("create_variants").action_id() == "skill_creation.create_variants"
    assert workflow.step("create_variants").role_hint() == "optimizer"
    assert workflow.step("benchmark_candidates").depends_on == ["create_variants"]
    assert workflow.step("benchmark_candidates").action_id() == "benchmark.score_candidates"
    assert workflow.step("benchmark_candidates").role_hint() == "reviewer"
    assert workflow.step("review_candidates").depends_on == ["benchmark_candidates"]
    assert workflow.step("review_candidates").action_id() == "review.evaluate_candidates"
    assert workflow.step("review_candidates").role_hint() == "reviewer"
    assert workflow.step("decide_next_iteration").plugin_id == "autoresearch"
    assert workflow.step("decide_next_iteration").action_id() == "autoresearch.decide_iteration"
    assert workflow.step("decide_next_iteration").role_hint() == "planner"
    assert workflow.step("decide_next_iteration").allow_reentry is True
    assert [item.step_id for item in workflow.downstream_steps("plan_iteration")] == ["create_variants"]
    assert workflow.metadata["iteration"]["decision_step_id"] == "decide_next_iteration"
    assert registry.resolve_step_action("autonomous_research", "create_variants") == "skill_creation.create_variants"
    assert autoresearch.search_space_fields == [
        "strategy_spec",
        "execution_intent",
        "risk_filters",
        "timing",
        "sizing",
        "pacing",
        "candidate_generation_thresholds",
    ]
    assert autoresearch.metadata["composes_with"] == ["skill-creation", "benchmark", "review"]
    assert autoresearch.worker_actions() == ("autoresearch.plan_iteration", "autoresearch.decide_iteration")
    assert autoresearch.action_contract("autoresearch.plan_iteration")["variant_templates"][0]["template_id"] == "risk-discipline"
    assert skill_creation.worker_actions() == ("skill_creation.materialize_baseline", "skill_creation.create_variants")


def test_registry_rejects_workflow_with_unknown_plugin_reference() -> None:
    registry = build_default_plugin_registry()

    workflow = WorkflowGraphSpec(
        workflow_id="invalid-workflow",
        title="Invalid Workflow",
        description="Uses a plugin that is not registered.",
        steps=[
            WorkflowStepSpec(
                step_id="missing",
                title="Missing Plugin Step",
                plugin_id="missing-plugin",
                stage="execute",
                description="This should fail at registration time.",
            )
        ],
    )

    with pytest.raises(KeyError, match="missing-plugin"):
        registry.register_workflow(workflow)


def test_registry_lists_workflows_for_plugin() -> None:
    registry = build_default_plugin_registry()

    workflow_ids = {item.workflow_id for item in registry.workflows_for_plugin("review")}

    assert workflow_ids == {"autonomous_research", "approval_convergence"}


def test_approval_convergence_workflow_uses_final_benchmark_and_review_actions() -> None:
    registry = build_default_plugin_registry()

    workflow = registry.resolve_workflow("approval_convergence")
    approval_convergence = registry.resolve_plugin("approval-convergence")
    benchmark = registry.resolve_plugin("benchmark")
    review = registry.resolve_plugin("review")

    assert workflow.iterative is False
    assert workflow.human_review_required is True
    assert workflow.plugin_ids() == ("benchmark", "review", "approval-convergence")
    assert workflow.step("benchmark_finalist").action_id() == "benchmark.score_finalist"
    assert workflow.step("review_finalist").action_id() == "review.finalize_candidate"
    assert workflow.step("converge_approval").plugin_id == "approval-convergence"
    assert workflow.step("converge_approval").action_id() == "approval_convergence.converge_approval"
    assert workflow.step("converge_approval").depends_on == ["review_finalist"]
    assert workflow.terminal_steps == ["converge_approval"]
    assert benchmark.worker_actions() == (
        "benchmark.score_baseline",
        "benchmark.score_candidates",
        "benchmark.score_finalist",
    )
    assert review.worker_actions() == ("review.evaluate_candidates", "review.finalize_candidate")
    assert approval_convergence.plugin_type == "approval-convergence"
    assert approval_convergence.compatible_workflows == ["approval_convergence"]
    assert approval_convergence.worker_actions() == ("approval_convergence.converge_approval",)
    assert approval_convergence.metadata["composes_with"] == ["benchmark", "review"]
    assert approval_convergence.action_contract("approval_convergence.converge_approval")["approval_policy"][
        "allowed_review_statuses"
    ] == ["recommended", "keep"]
    assert benchmark.action_contract("benchmark.score_finalist")["benchmark_profile"] == "final-approval"
    assert review.action_contract("review.finalize_candidate")["review_policy"]["min_quality_score"] == 0.6


def test_approval_convergence_plugin_is_first_class_and_attached_to_workflow() -> None:
    registry = build_default_plugin_registry()

    plugin = registry.resolve_plugin("approval-convergence")
    described = registry.describe()
    described_plugin_ids = {item["plugin_id"] for item in described["plugins"]}

    assert plugin.capabilities[0].capability_id == "converge_approval"
    assert plugin.artifact_kinds == [
        "approval_activation_bundle",
        "approval_recommendation",
        "kernel_handoff_payload",
    ]
    assert [item.workflow_id for item in registry.workflows_for_plugin("approval-convergence")] == ["approval_convergence"]
    assert "approval-convergence" in described_plugin_ids
