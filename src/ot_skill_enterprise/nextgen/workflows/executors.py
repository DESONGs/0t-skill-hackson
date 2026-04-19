from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable, Mapping

from ot_skill_enterprise.nextgen.adapters import AdapterRegistry
from ot_skill_enterprise.nextgen.plugins import WorkflowPluginRegistry
from ot_skill_enterprise.skills_compiler import SkillCandidate, SkillPackageCompiler

from .models import (
    ApprovalActivationRecord,
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
    WorkflowVariant,
)
from .store import ResearchLoopStore


def validate_workflow_support(registry: Any, *, workflow_id: str, required_plugins: Iterable[str]) -> None:
    workflow = registry.resolve_workflow(workflow_id)
    registered = set(workflow.plugin_ids())
    missing = [plugin_id for plugin_id in required_plugins if plugin_id not in registered]
    if missing:
        raise KeyError(f"workflow {workflow_id!r} is missing required plugins: {missing}")


def _string(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp_score(value: float) -> float:
    return max(0.0, min(1.0, round(float(value), 4)))


def _append_unique_strings(values: list[str], additions: list[str]) -> list[str]:
    merged = list(values)
    for item in additions:
        text = str(item or "").strip()
        if text and text not in merged:
            merged.append(text)
    return merged


def _artifact_payload(artifacts: list[WorkflowArtifact], kind: str) -> dict[str, Any]:
    for artifact in artifacts:
        if artifact.ref.kind == kind:
            return dict(artifact.payload)
    return {}


def _variant_candidate(
    request: WorkflowRunRequest,
    *,
    session_id: str,
    variant: WorkflowVariant,
    baseline_variant: WorkflowVariant,
) -> SkillCandidate:
    candidate_metadata = dict(variant.metadata.get("materialization") or {})
    lineage = dict(variant.metadata.get("lineage") or {})
    wallet = _string(variant.style_profile.get("wallet") or request.wallet)
    chain = _string(variant.style_profile.get("chain") or request.chain)
    return SkillCandidate.from_mapping(
        {
            "candidate_id": _string(candidate_metadata.get("candidate_id"), f"candidate-{variant.variant_id}"),
            "candidate_slug": _string(candidate_metadata.get("candidate_slug"), variant.variant_id),
            "runtime_session_id": _string(candidate_metadata.get("runtime_session_id"), session_id),
            "source_run_id": candidate_metadata.get("source_run_id") or session_id,
            "candidate_type": "script",
            "target_skill_name": _string(request.skill_name, baseline_variant.title or variant.title),
            "target_skill_kind": "wallet_style",
            "change_summary": _string(variant.metadata.get("change_summary") or variant.metadata.get("hypothesis"), variant.title),
            "generation_spec": {
                "wallet_style_profile": dict(variant.style_profile),
                "strategy_spec": dict(variant.strategy_spec),
                "execution_intent": dict(variant.execution_intent),
            },
            "metadata": {
                "wallet_address": wallet,
                "chain": chain,
                "research_session_id": session_id,
                "workflow_id": request.workflow_id,
                "variant_id": variant.variant_id,
                "lineage": lineage,
            },
        }
    )


def _derive_qa_payload(*, validation: dict[str, Any], backtest: dict[str, Any]) -> dict[str, Any]:
    phases = list(validation.get("phases") or [])
    checks = [
        {
            "check": _string(item.get("phase"), "unknown").replace(" ", "_"),
            "passed": bool(item.get("ok", False)),
            "detail": item,
        }
        for item in phases
    ]
    checks.append(
        {
            "check": "backtest_confidence_available",
            "passed": _safe_float(backtest.get("confidence_score"), 0.0) > 0.0,
            "detail": backtest,
        }
    )
    status = "passed" if validation.get("ok") and all(item["passed"] for item in checks) else "failed"
    return {
        "status": status,
        "checks": checks,
        "strategy_qa": {
            "status": "passed" if validation.get("ok") else "failed",
            "checks": checks[:-1],
        },
        "execution_qa": {
            "status": "passed" if checks[-1]["passed"] else "failed",
            "checks": [checks[-1]],
        },
    }


class SkillCreationPluginExecutor:
    def __init__(self, compiler: SkillPackageCompiler, store: ResearchLoopStore) -> None:
        self._compiler = compiler
        self._store = store

    def dispatch_action(self, action_id: str, **kwargs: Any):
        if action_id == "skill_creation.materialize_baseline":
            return self.materialize_baseline(**kwargs)
        if action_id == "skill_creation.create_variants":
            return self.create_variants(**kwargs)
        raise KeyError(f"unsupported skill-creation action {action_id!r}")

    def materialize_baseline(
        self,
        *,
        request: WorkflowRunRequest,
        session_id: str,
        baseline_variant: WorkflowVariant,
        raw_distillation_result: Mapping[str, Any],
    ) -> WorkflowVariant:
        build, validation = self._build_and_validate(
            request,
            session_id=session_id,
            variant=baseline_variant,
            baseline_variant=baseline_variant,
        )
        raw_result = dict(raw_distillation_result)
        backtest_payload = dict(raw_result.get("backtest") or baseline_variant.metadata.get("backtest") or {})
        qa_payload = dict(raw_result.get("qa") or {})
        if not qa_payload:
            qa_payload = _derive_qa_payload(validation=validation.to_dict(), backtest=backtest_payload)
        materialization_artifact = self._store.write_artifact(
            session_id,
            kind="baseline_materialization",
            label="baseline",
            filename="baseline-materialization.json",
            payload={
                "variant_id": baseline_variant.variant_id,
                "session_id": session_id,
                "action_id": "skill_creation.materialize_baseline",
                "package_root": str(build.package_root),
                "bundle_sha256": build.bundle_sha256,
                "generated_files": list(build.generated_files),
            },
            metadata={"variant_id": baseline_variant.variant_id},
        )
        package_artifact = self._store.write_artifact(
            session_id,
            kind="skill_package",
            label="baseline",
            filename="baseline-skill-package.json",
            payload={
                **build.to_dict(),
                "source_package": dict(raw_result.get("package") or {}),
            },
            metadata={"variant_id": baseline_variant.variant_id, "package_root": str(build.package_root)},
        )
        validation_artifact = self._store.write_artifact(
            session_id,
            kind="validation_report",
            label="baseline",
            filename="baseline-validation-report.json",
            payload=validation.to_dict(),
            metadata={"variant_id": baseline_variant.variant_id, "validation_ok": validation.ok},
        )
        qa_artifact = self._store.write_artifact(
            session_id,
            kind="qa_report",
            label="baseline",
            filename="baseline-qa-report.json",
            payload=qa_payload,
            metadata={"variant_id": baseline_variant.variant_id},
        )
        replay_artifact = self._store.write_artifact(
            session_id,
            kind="kernel_replay_payload",
            label="baseline",
            filename="baseline-kernel-replay.json",
            payload={
                "workflow_id": request.workflow_id,
                "session_id": session_id,
                "action_id": "benchmark.score_baseline",
                "variant_id": baseline_variant.variant_id,
                "package_root": str(build.package_root),
            },
            metadata={"variant_id": baseline_variant.variant_id, "role_hint": "optimizer"},
        )
        artifacts = [
            *baseline_variant.artifacts,
            materialization_artifact,
            package_artifact,
            validation_artifact,
            qa_artifact,
            replay_artifact,
        ]
        metadata = {
            **dict(baseline_variant.metadata),
            "materialization": {
                "source": "skill-creation",
                "candidate_id": f"candidate-{baseline_variant.variant_id}",
                "package_root": str(build.package_root),
                "bundle_sha256": build.bundle_sha256,
                "generated_files": list(build.generated_files),
                "validation": validation.to_dict(),
                "qa": qa_payload,
                "promotion": dict(raw_result.get("promotion") or {}),
            },
        }
        return baseline_variant.model_copy(update={"artifacts": artifacts, "metadata": metadata})

    def create_variants(
        self,
        *,
        request: WorkflowRunRequest,
        session_id: str,
        baseline_variant: WorkflowVariant,
        parent_variant: WorkflowVariant,
        plans: list[ResearchVariantPlan],
        iteration_index: int,
    ) -> list[WorkflowVariant]:
        variants: list[WorkflowVariant] = []
        for plan in plans:
            variants.append(
                self._apply_plan(
                    request,
                    session_id=session_id,
                    baseline_variant=baseline_variant,
                    parent_variant=parent_variant,
                    plan=plan,
                    iteration_index=iteration_index,
                )
            )
        return variants

    def _apply_plan(
        self,
        request: WorkflowRunRequest,
        *,
        session_id: str,
        baseline_variant: WorkflowVariant,
        parent_variant: WorkflowVariant,
        plan: ResearchVariantPlan,
        iteration_index: int,
    ) -> WorkflowVariant:
        lineage = {
            "session_id": session_id,
            "iteration_index": iteration_index,
            "baseline_variant_id": baseline_variant.variant_id,
            "parent_variant_id": parent_variant.variant_id,
            "variant_source": _string(plan.metadata.get("source"), "planned"),
            "plan_id": plan.plan_id,
            "template_id": plan.template_id,
            "target_fields": list(plan.target_fields),
        }
        if plan.metadata.get("source") == "supplied":
            strategy_spec = dict(plan.mutations.get("strategy_spec") or parent_variant.strategy_spec)
            execution_intent = dict(plan.mutations.get("execution_intent") or parent_variant.execution_intent)
            style_profile = dict(plan.mutations.get("style_profile") or baseline_variant.style_profile)
            variant_id = _string(plan.metadata.get("variant_id"), f"variant-supplied-i{iteration_index}")
        else:
            strategy_spec = deepcopy(parent_variant.strategy_spec)
            execution_intent = deepcopy(parent_variant.execution_intent)
            style_profile = deepcopy(baseline_variant.style_profile)
            strategy_mutations = dict(plan.mutations.get("strategy") or {})
            execution_mutations = dict(plan.mutations.get("execution") or {})
            summary_suffix = _string(strategy_mutations.get("summary_suffix"))
            if summary_suffix:
                strategy_spec["summary"] = f"{_string(strategy_spec.get('summary'), 'Baseline strategy')} {summary_suffix}".strip()
            risk_controls = list(strategy_spec.get("risk_controls") or [])
            strategy_spec["risk_controls"] = _append_unique_strings(risk_controls, list(strategy_mutations.get("risk_controls_append") or []))
            entry_conditions = list(strategy_spec.get("entry_conditions") or [])
            entry_conditions.extend(
                [dict(item) for item in list(strategy_mutations.get("entry_conditions_append") or []) if isinstance(item, dict)]
            )
            if entry_conditions:
                strategy_spec["entry_conditions"] = entry_conditions
            for key, value in dict(strategy_mutations.get("field_overrides") or {}).items():
                strategy_spec[key] = value
            scale = execution_mutations.get("max_position_pct_scale")
            if scale is not None:
                current = _safe_float(execution_intent.get("max_position_pct"), _safe_float(parent_variant.execution_intent.get("max_position_pct"), 0.12))
                execution_intent["max_position_pct"] = round(current * float(scale), 4)
            execution_intent["preflight_checks"] = _append_unique_strings(
                list(execution_intent.get("preflight_checks") or []),
                list(execution_mutations.get("preflight_checks_append") or []),
            )
            execution_metadata = dict(execution_intent.get("metadata") or {})
            execution_metadata.update(dict(execution_mutations.get("metadata") or {}))
            if execution_metadata:
                execution_intent["metadata"] = execution_metadata
            for key, value in dict(execution_mutations.get("field_overrides") or {}).items():
                execution_intent[key] = value
            variant_id = f"variant-{plan.template_id}-i{iteration_index}"
        variant = WorkflowVariant(
            variant_id=variant_id,
            title=plan.title,
            source="skill-creation",
            status="candidate",
            strategy_spec=strategy_spec,
            execution_intent=execution_intent,
            style_profile=style_profile,
            artifacts=list(plan.artifacts),
            metadata={
                "plan_id": plan.plan_id,
                "hypothesis": plan.hypothesis,
                "change_summary": plan.change_summary,
                "lineage": lineage,
            },
        )
        build, validation = self._build_and_validate(
            request,
            session_id=session_id,
            variant=variant,
            baseline_variant=baseline_variant,
        )
        candidate_artifact = self._store.write_artifact(
            session_id,
            kind="candidate_variant",
            label=variant_id,
            filename=f"{variant_id}-candidate.json",
            payload={
                "variant_id": variant_id,
                "plan_id": plan.plan_id,
                "iteration_index": iteration_index,
                "strategy_spec": strategy_spec,
                "execution_intent": execution_intent,
                "style_profile": style_profile,
                "lineage": lineage,
            },
            metadata={"variant_id": variant_id, "iteration_index": iteration_index},
        )
        materialization_artifact = self._store.write_artifact(
            session_id,
            kind="variant_materialization",
            label=variant_id,
            filename=f"{variant_id}-materialization.json",
            payload={
                "variant_id": variant_id,
                "plan_id": plan.plan_id,
                "iteration_index": iteration_index,
                "action_id": "skill_creation.create_variants",
                "package_root": str(build.package_root),
                "bundle_sha256": build.bundle_sha256,
            },
            metadata={"variant_id": variant_id, "plan_id": plan.plan_id},
        )
        package_artifact = self._store.write_artifact(
            session_id,
            kind="skill_package",
            label=variant_id,
            filename=f"{variant_id}-skill-package.json",
            payload=build.to_dict(),
            metadata={"variant_id": variant_id, "package_root": str(build.package_root)},
        )
        validation_artifact = self._store.write_artifact(
            session_id,
            kind="validation_report",
            label=variant_id,
            filename=f"{variant_id}-validation-report.json",
            payload=validation.to_dict(),
            metadata={"variant_id": variant_id, "validation_ok": validation.ok},
        )
        replay_artifact = self._store.write_artifact(
            session_id,
            kind="kernel_replay_payload",
            label=variant_id,
            filename=f"{variant_id}-kernel-replay.json",
            payload={
                "workflow_id": request.workflow_id,
                "session_id": session_id,
                "action_id": "benchmark.score_candidates",
                "variant_id": variant_id,
                "plan_id": plan.plan_id,
                "package_root": str(build.package_root),
            },
            metadata={"variant_id": variant_id, "plan_id": plan.plan_id, "role_hint": "optimizer"},
        )
        return variant.model_copy(
            update={
                "artifacts": [
                    *variant.artifacts,
                    candidate_artifact,
                    materialization_artifact,
                    package_artifact,
                    validation_artifact,
                    replay_artifact,
                ],
                "metadata": {
                    **dict(variant.metadata),
                    "materialization": {
                        "source": "skill-creation",
                        "candidate_id": f"candidate-{variant_id}",
                        "package_root": str(build.package_root),
                        "bundle_sha256": build.bundle_sha256,
                        "generated_files": list(build.generated_files),
                        "validation": validation.to_dict(),
                    },
                },
            }
        )

    def _build_and_validate(
        self,
        request: WorkflowRunRequest,
        *,
        session_id: str,
        variant: WorkflowVariant,
        baseline_variant: WorkflowVariant,
    ):
        output_root = self._store.session_dir(session_id) / "packages" / variant.variant_id
        candidate = _variant_candidate(
            request,
            session_id=session_id,
            variant=variant,
            baseline_variant=baseline_variant,
        )
        build = self._compiler.compile(candidate, output_root=output_root, package_kind="script", force=True)
        validation = self._compiler.validate(build.package_root, candidate=candidate)
        return build, validation


class AutoresearchPluginExecutor:
    def __init__(self, plugin_registry: WorkflowPluginRegistry, store: ResearchLoopStore) -> None:
        self._plugin_spec = plugin_registry.resolve_plugin("autoresearch")
        self._store = store

    def dispatch_action(self, action_id: str, **kwargs: Any):
        if action_id in {"autoresearch.plan_iteration", "autoresearch.generate_variants"}:
            return self.plan_iteration(**kwargs)
        if action_id in {"autoresearch.decide_iteration", "autoresearch.finalize_iteration"}:
            resolved_action = "autoresearch.decide_iteration" if action_id == "autoresearch.finalize_iteration" else action_id
            return self.finalize_iteration(action_id=resolved_action, **kwargs)
        raise KeyError(f"unsupported autoresearch action {action_id!r}")

    def plan_iteration(
        self,
        *,
        request: WorkflowRunRequest,
        baseline_variant: WorkflowVariant,
        parent_variant: WorkflowVariant,
        session_id: str,
        iteration_index: int,
    ) -> list[ResearchVariantPlan]:
        contract = self._plugin_spec.action_contract("autoresearch.plan_iteration")
        supplied = list(request.candidate_variants or [])
        if supplied and iteration_index == 1:
            return [
                self._supplied_plan(
                    request,
                    baseline_variant=baseline_variant,
                    parent_variant=parent_variant,
                    session_id=session_id,
                    iteration_index=iteration_index,
                    payload=item,
                    index=index,
                )
                for index, item in enumerate(supplied[: request.max_variants], start=1)
            ]
        templates = list(contract.get("variant_templates") or [])
        return [
            self._template_plan(
                request,
                baseline_variant=baseline_variant,
                parent_variant=parent_variant,
                session_id=session_id,
                iteration_index=iteration_index,
                template=dict(template or {}),
                index=index,
            )
            for index, template in enumerate(templates[: request.max_variants], start=1)
        ]

    def finalize_iteration(
        self,
        *,
        action_id: str,
        workflow_id: str,
        request: WorkflowRunRequest,
        session_id: str,
        iteration_index: int,
        baseline_variant: WorkflowVariant,
        variants: list[WorkflowVariant],
        scorecards: list[BenchmarkScorecard],
        review_decisions: list[ReviewDecision],
        iterations: list[ResearchIterationRecord],
        baseline_scorecard: BenchmarkScorecard,
    ) -> RecommendationBundle:
        contract = self._plugin_spec.action_contract(action_id)
        policy = dict(contract.get("recommendation_policy") or {})
        score_by_variant = {item.variant_id: item for item in scorecards}
        review_by_variant = {item.variant_id: item for item in review_decisions}
        ranked = sorted(
            variants,
            key=lambda item: (
                review_by_variant.get(item.variant_id, ReviewDecision(variant_id=item.variant_id, status="discard", reasoning="missing")).status in {"recommended", "keep"},
                score_by_variant.get(
                    item.variant_id,
                    BenchmarkScorecard(
                        variant_id=item.variant_id,
                        primary_quality_score=0.0,
                        backtest_confidence=0.0,
                        execution_readiness="unknown",
                        strategy_quality="weak",
                        style_distance=1.0,
                        risk_penalty=1.0,
                        confidence_vs_noise=-1.0,
                        hard_gates_passed=False,
                    ),
                ).primary_quality_score,
                -score_by_variant.get(
                    item.variant_id,
                    BenchmarkScorecard(
                        variant_id=item.variant_id,
                        primary_quality_score=0.0,
                        backtest_confidence=0.0,
                        execution_readiness="unknown",
                        strategy_quality="weak",
                        style_distance=1.0,
                        risk_penalty=1.0,
                        confidence_vs_noise=-1.0,
                        hard_gates_passed=False,
                    ),
                ).style_distance,
            ),
            reverse=True,
        )
        leaderboard: list[dict[str, Any]] = []
        selected: WorkflowVariant | None = None
        status = "review_required"
        for variant in ranked:
            score = score_by_variant.get(variant.variant_id)
            decision = review_by_variant.get(variant.variant_id)
            if score is None:
                continue
            quality_delta = round(score.primary_quality_score - baseline_scorecard.primary_quality_score, 4)
            noise_delta = round(score.confidence_vs_noise - baseline_scorecard.confidence_vs_noise, 4)
            leaderboard.append(
                {
                    "variant_id": variant.variant_id,
                    "title": variant.title,
                    "decision": decision.status if decision else "discard",
                    "primary_quality_score": score.primary_quality_score,
                    "style_distance": score.style_distance,
                    "risk_penalty": score.risk_penalty,
                    "quality_delta": quality_delta,
                    "noise_delta": noise_delta,
                    "hard_gates_passed": score.hard_gates_passed,
                }
            )
            if selected is None and decision is not None and decision.status in {"recommended", "keep"}:
                selected = variant
                status = "recommended" if decision.status == "recommended" else "keep"
        stop_decision = self._stop_decision(
            request=request,
            iteration_index=iteration_index,
            selected_variant=selected,
            selected_scorecard=score_by_variant.get(selected.variant_id) if selected is not None else None,
            baseline_scorecard=baseline_scorecard,
            status=status,
            policy=policy,
        )
        summary = (
            f"Selected {selected.title} for follow-up"
            if selected is not None
            else "No candidate cleared review; manual review required"
        )
        leaderboard_artifact = self._store.write_artifact(
            session_id,
            kind="leaderboard",
            label=f"iteration-{iteration_index}",
            filename=f"leaderboard-{iteration_index:03d}.json",
            payload={"session_id": session_id, "iteration_index": iteration_index, "leaderboard": leaderboard},
            metadata={"iteration_index": iteration_index},
        )
        recommendation_artifact = self._store.write_artifact(
            session_id,
            kind="recommendation_bundle",
            label=f"iteration-{iteration_index}",
            filename=f"recommendation-{iteration_index:03d}.json",
            payload={
                "workflow_id": workflow_id,
                "session_id": session_id,
                "iteration_index": iteration_index,
                "status": status,
                "selected_variant_id": selected.variant_id if selected else None,
                "stop_decision": stop_decision.model_dump(mode="json"),
            },
            metadata={"iteration_index": iteration_index},
        )
        handoff_artifact = self._store.write_artifact(
            session_id,
            kind="kernel_handoff_payload",
            label=f"iteration-{iteration_index}",
            filename=f"handoff-{iteration_index:03d}.json",
            payload={
                "workflow_id": workflow_id,
                "session_id": session_id,
                "action_id": action_id,
                "selected_variant_id": selected.variant_id if selected else None,
                "stop_decision": stop_decision.model_dump(mode="json"),
                "leaderboard": leaderboard,
            },
            metadata={"iteration_index": iteration_index, "role_hint": "planner"},
        )
        return RecommendationBundle(
            workflow_id=workflow_id,
            baseline_variant_id=baseline_variant.variant_id,
            session_id=session_id,
            workspace_id=request.workspace_id,
            status=status,
            summary=summary,
            recommended_variant_id=selected.variant_id if status == "recommended" and selected is not None else None,
            iteration_count=len(iterations),
            selected_variant=selected,
            leaderboard=leaderboard,
            scorecards=scorecards,
            review_decisions=review_decisions,
            iterations=iterations,
            stop_decision=stop_decision,
            artifacts=[leaderboard_artifact, recommendation_artifact, handoff_artifact],
            metadata={
                "source": "artifact-backed-autoresearch",
                "policy": policy,
                "iteration_index": iteration_index,
            },
        )

    def _template_plan(
        self,
        request: WorkflowRunRequest,
        *,
        baseline_variant: WorkflowVariant,
        parent_variant: WorkflowVariant,
        session_id: str,
        iteration_index: int,
        template: dict[str, Any],
        index: int,
    ) -> ResearchVariantPlan:
        template_id = _string(template.get("template_id"), f"template-{index}")
        plan_id = f"plan-{template_id}-i{iteration_index}"
        lineage = {
            "session_id": session_id,
            "iteration_index": iteration_index,
            "baseline_variant_id": baseline_variant.variant_id,
            "parent_variant_id": parent_variant.variant_id,
            "variant_source": "planned",
        }
        plan_artifact = self._store.write_artifact(
            session_id,
            kind="research_plan",
            label=plan_id,
            filename=f"{plan_id}.json",
            payload={
                "plan_id": plan_id,
                "template_id": template_id,
                "title": _string(template.get("title"), template_id),
                "objective": request.objective,
                "hypothesis": _string(template.get("hypothesis"), f"Plan {template_id}"),
                "change_summary": _string(template.get("change_summary"), f"Apply {template_id} mutation"),
                "target_fields": list(template.get("mutations", {}).get("target_fields") or []),
                "mutations": dict(template.get("mutations") or {}),
                "lineage": lineage,
            },
            metadata={"iteration_index": iteration_index, "plan_id": plan_id, "role_hint": "planner"},
        )
        replay_artifact = self._store.write_artifact(
            session_id,
            kind="kernel_replay_payload",
            label=f"{plan_id}-replay",
            filename=f"{plan_id}-kernel-replay.json",
            payload={
                "workflow_id": request.workflow_id,
                "session_id": session_id,
                "action_id": "skill_creation.create_variants",
                "plan_id": plan_id,
                "template_id": template_id,
                "iteration_index": iteration_index,
            },
            metadata={"iteration_index": iteration_index, "plan_id": plan_id, "role_hint": "planner"},
        )
        return ResearchVariantPlan(
            plan_id=plan_id,
            template_id=template_id,
            title=_string(template.get("title"), template_id),
            objective=request.objective,
            hypothesis=_string(template.get("hypothesis"), f"Plan {template_id}"),
            change_summary=_string(template.get("change_summary"), f"Apply {template_id} mutation"),
            target_fields=list(template.get("mutations", {}).get("target_fields") or []),
            mutations=dict(template.get("mutations") or {}),
            artifacts=[plan_artifact, replay_artifact],
            metadata={"source": "template", "lineage": lineage},
        )

    def _supplied_plan(
        self,
        request: WorkflowRunRequest,
        *,
        baseline_variant: WorkflowVariant,
        parent_variant: WorkflowVariant,
        session_id: str,
        iteration_index: int,
        payload: dict[str, Any],
        index: int,
    ) -> ResearchVariantPlan:
        variant_id = _string(payload.get("variant_id"), f"variant-supplied-{index}")
        plan_id = _string(payload.get("plan_id"), f"plan-supplied-{index}-i{iteration_index}")
        lineage = {
            "session_id": session_id,
            "iteration_index": iteration_index,
            "baseline_variant_id": baseline_variant.variant_id,
            "parent_variant_id": parent_variant.variant_id,
            "variant_source": "supplied",
        }
        mutations = {
            "strategy_spec": dict(payload.get("strategy_spec") or parent_variant.strategy_spec),
            "execution_intent": dict(payload.get("execution_intent") or parent_variant.execution_intent),
            "style_profile": dict(payload.get("style_profile") or baseline_variant.style_profile),
        }
        plan_artifact = self._store.write_artifact(
            session_id,
            kind="research_plan",
            label=plan_id,
            filename=f"{plan_id}.json",
            payload={
                "plan_id": plan_id,
                "template_id": "supplied",
                "variant_id": variant_id,
                "objective": request.objective,
                "hypothesis": _string(payload.get("hypothesis"), "user-supplied variant"),
                "change_summary": _string(payload.get("title"), variant_id),
                "mutations": mutations,
                "lineage": lineage,
            },
            metadata={"iteration_index": iteration_index, "plan_id": plan_id, "role_hint": "planner"},
        )
        replay_artifact = self._store.write_artifact(
            session_id,
            kind="kernel_replay_payload",
            label=f"{plan_id}-replay",
            filename=f"{plan_id}-kernel-replay.json",
            payload={
                "workflow_id": request.workflow_id,
                "session_id": session_id,
                "action_id": "skill_creation.create_variants",
                "plan_id": plan_id,
                "variant_id": variant_id,
                "iteration_index": iteration_index,
            },
            metadata={"iteration_index": iteration_index, "plan_id": plan_id, "role_hint": "planner"},
        )
        return ResearchVariantPlan(
            plan_id=plan_id,
            template_id="supplied",
            title=_string(payload.get("title"), variant_id),
            objective=request.objective,
            hypothesis=_string(payload.get("hypothesis"), "user-supplied variant"),
            change_summary=_string(payload.get("title"), variant_id),
            target_fields=[key for key in ("strategy_spec", "execution_intent", "style_profile") if key in payload],
            mutations=mutations,
            artifacts=[plan_artifact, replay_artifact],
            metadata={"source": "supplied", "variant_id": variant_id, "lineage": lineage},
        )

    def _stop_decision(
        self,
        *,
        request: WorkflowRunRequest,
        iteration_index: int,
        selected_variant: WorkflowVariant | None,
        selected_scorecard: BenchmarkScorecard | None,
        baseline_scorecard: BenchmarkScorecard,
        status: str,
        policy: dict[str, Any],
    ) -> ResearchStopDecision:
        quality_gap = (
            round(selected_scorecard.primary_quality_score - baseline_scorecard.primary_quality_score, 4)
            if selected_scorecard is not None
            else 0.0
        )
        if status == "recommended":
            return ResearchStopDecision(
                decision="stop",
                reason="variant cleared benchmark and review thresholds",
                selected_variant_id=selected_variant.variant_id if selected_variant else None,
                next_parent_variant_id=selected_variant.variant_id if selected_variant else None,
                human_review_required=True,
                metadata={"quality_gap": quality_gap, "iteration_index": iteration_index, "policy": policy},
            )
        if iteration_index >= request.iteration_budget:
            return ResearchStopDecision(
                decision="stop",
                reason="iteration budget exhausted",
                selected_variant_id=selected_variant.variant_id if selected_variant else None,
                next_parent_variant_id=selected_variant.variant_id if selected_variant else None,
                human_review_required=status == "review_required",
                metadata={"quality_gap": quality_gap, "iteration_index": iteration_index, "policy": policy},
            )
        return ResearchStopDecision(
            decision="continue",
            reason="continue searching for a stronger candidate",
            selected_variant_id=selected_variant.variant_id if selected_variant else None,
            next_parent_variant_id=selected_variant.variant_id if selected_variant else None,
            human_review_required=False,
            metadata={"quality_gap": quality_gap, "iteration_index": iteration_index, "policy": policy},
        )


class BenchmarkPluginExecutor:
    def __init__(
        self,
        plugin_registry: WorkflowPluginRegistry,
        adapter_registry: AdapterRegistry,
        compiler: SkillPackageCompiler,
        store: ResearchLoopStore,
    ) -> None:
        self._plugin_spec = plugin_registry.resolve_plugin("benchmark")
        self._adapter_registry = adapter_registry
        self._compiler = compiler
        self._store = store

    def dispatch_action(self, action_id: str, **kwargs: Any):
        if action_id in {"benchmark.score_baseline", "benchmark.score_candidates", "benchmark.score_finalist"}:
            return self.execute(action_id=action_id, **kwargs)
        raise KeyError(f"unsupported benchmark action {action_id!r}")

    def execute(
        self,
        *,
        action_id: str,
        request: WorkflowRunRequest,
        session: ResearchSessionState,
        iteration_index: int,
        baseline_variant: WorkflowVariant,
        variants: list[WorkflowVariant],
        persist_record: bool = True,
        artifact_suffix: str | None = None,
    ) -> list[BenchmarkScorecard]:
        execution_registration = self._adapter_registry.resolve_registration(
            "execution",
            adapter_id=request.execution_adapter_id,
            required_capabilities=("execution_prepare_only",),
        )
        data_registration = self._adapter_registry.resolve_registration(
            "data_source",
            adapter_id=request.data_source_adapter_id,
            capability_id="market_context",
        )
        return [
            self._score_variant(
                action_id=action_id,
                request=request,
                session=session,
                iteration_index=iteration_index,
                variant=variant,
                baseline_variant=baseline_variant,
                execution_adapter_id=execution_registration.manifest.adapter_id,
                data_source_adapter_id=data_registration.manifest.adapter_id,
                persist_record=persist_record,
                artifact_suffix=artifact_suffix,
            )
            for variant in variants
        ]

    def _score_variant(
        self,
        *,
        action_id: str,
        request: WorkflowRunRequest,
        session: ResearchSessionState,
        iteration_index: int,
        variant: WorkflowVariant,
        baseline_variant: WorkflowVariant,
        execution_adapter_id: str,
        data_source_adapter_id: str,
        persist_record: bool,
        artifact_suffix: str | None,
    ) -> BenchmarkScorecard:
        contract = self._plugin_spec.action_contract(action_id)
        profile_name = _string(contract.get("benchmark_profile"), "default-research")
        weights = dict(self._plugin_spec.metadata.get("benchmark_profiles", {}).get(profile_name, {}).get("score_weights") or {})
        gate_names = list(contract.get("gate_profile") or [])
        gate_policy = dict(self._plugin_spec.metadata.get("gate_policies") or {})
        bundle = self._resolve_materialization_bundle(
            request,
            session=session,
            iteration_index=iteration_index,
            variant=variant,
            baseline_variant=baseline_variant,
        )
        strategy = dict(variant.strategy_spec or {})
        baseline_strategy = dict(baseline_variant.strategy_spec or {})
        execution_intent = dict(variant.execution_intent or {})
        baseline_execution = dict(baseline_variant.execution_intent or {})
        entry_count = len(strategy.get("entry_conditions") or [])
        baseline_entry_count = len(baseline_strategy.get("entry_conditions") or [])
        risk_controls = list(strategy.get("risk_controls") or [])
        preferred_tokens = set(variant.style_profile.get("preferred_tokens") or [])
        baseline_tokens = set(baseline_variant.style_profile.get("preferred_tokens") or [])
        overlap = len(preferred_tokens & baseline_tokens)
        union = max(1, len(preferred_tokens | baseline_tokens))
        token_overlap_score = overlap / union
        style_distance = _clamp_score(1.0 - token_overlap_score)
        if strategy.get("setup_label") == baseline_strategy.get("setup_label"):
            style_distance = _clamp_score(style_distance * 0.75)
        if style_distance == 1.0:
            style_distance = _clamp_score(_safe_float(dict(variant.metadata.get("lineage") or {}).get("style_distance"), style_distance))
        max_position_pct = execution_intent.get("max_position_pct")
        baseline_max_position_pct = baseline_execution.get("max_position_pct")
        risk_penalty = 0.06 if not execution_intent.get("preflight_checks") else 0.0
        if max_position_pct is not None and baseline_max_position_pct is not None:
            risk_penalty += max(0.0, float(max_position_pct) - float(baseline_max_position_pct)) * 0.8
        risk_penalty += 0.04 if len(risk_controls) < len(baseline_strategy.get("risk_controls") or []) else 0.0
        risk_penalty = _clamp_score(risk_penalty)
        validation_payload = dict(bundle["validation"] or {})
        qa_payload = dict(bundle["qa"] or {})
        backtest_payload = dict(bundle["backtest"] or {})
        validation_ok = bool(validation_payload.get("ok"))
        qa_status = _string(qa_payload.get("status"), "failed")
        execution_readiness = "ready" if execution_intent.get("adapter") == execution_adapter_id else "adapter_mismatch"
        score_components = {
            "entry_signal": min(entry_count / max(1, max(entry_count, 5)), 1.0),
            "risk_controls": min(len(risk_controls) / 4, 1.0),
            "style_overlap": token_overlap_score,
            "adapter_alignment": 1.0 if execution_intent.get("adapter") == execution_adapter_id else 0.0,
            "validation": 1.0 if validation_ok else 0.0,
            "qa": 1.0 if qa_status == "passed" else 0.0,
            "backtest": _clamp_score(
                _safe_float(
                    variant.metadata.get("backtest_confidence")
                    or backtest_payload.get("confidence_score")
                    or baseline_variant.metadata.get("backtest", {}).get("confidence_score")
                    or 0.62
                )
            ),
        }
        weighted_total = 0.0
        weight_sum = 0.0
        for key, component in score_components.items():
            weight = _safe_float(weights.get(key), 0.0)
            weighted_total += component * weight
            weight_sum += weight
        base_score = weighted_total / weight_sum if weight_sum else sum(score_components.values()) / len(score_components)
        primary_quality_score = _clamp_score(base_score - risk_penalty * 0.35)
        backtest_confidence = _clamp_score(score_components["backtest"] + min(max(entry_count - baseline_entry_count, 0), 3) * 0.02)
        confidence_vs_noise = round(primary_quality_score - 0.5 * risk_penalty - (style_distance * 0.1), 4)
        if primary_quality_score >= 0.72:
            strategy_quality = "strong"
        elif primary_quality_score >= 0.58:
            strategy_quality = "acceptable"
        else:
            strategy_quality = "weak"
        gate_results = []
        for gate_name in gate_names:
            policy = dict(gate_policy.get(gate_name) or {})
            if gate_name == "style-drift":
                threshold = _safe_float(policy.get("max_style_distance"), 0.45)
                passed = style_distance <= threshold
                observed = style_distance
            elif gate_name == "risk-floor":
                threshold = _safe_float(policy.get("max_risk_penalty"), 0.3)
                passed = risk_penalty <= threshold
                observed = risk_penalty
            elif gate_name == "readiness":
                threshold = _string(policy.get("required_execution_readiness"), "ready")
                passed = execution_readiness == threshold
                observed = execution_readiness
            elif gate_name == "validation":
                threshold = bool(policy.get("required_ok", True))
                passed = validation_ok is threshold
                observed = validation_ok
            elif gate_name == "qa":
                threshold = _string(policy.get("required_status"), "passed")
                passed = qa_status == threshold
                observed = qa_status
            else:
                threshold = None
                passed = True
                observed = None
            gate_results.append(
                {
                    "gate_id": gate_name,
                    "passed": passed,
                    "threshold": threshold,
                    "observed": observed,
                }
            )
        hard_gates_passed = all(bool(item.get("passed")) for item in gate_results)
        notes = [
            f"profile={profile_name}",
            f"execution_adapter={execution_adapter_id}",
            f"data_source_adapter={data_source_adapter_id}",
            f"package_root={bundle['build'].get('package_root')}",
        ]
        notes.extend([f"failed_gate={item['gate_id']}" for item in gate_results if not item["passed"]])
        suffix = f"-{_string(artifact_suffix)}" if _string(artifact_suffix) else ""
        gate_artifact = self._store.write_artifact(
            session.session_id,
            kind="gate_result",
            label=f"{variant.variant_id}{suffix}-gates",
            filename=f"{variant.variant_id}{suffix}-gate-result.json",
            payload={"variant_id": variant.variant_id, "profile": profile_name, "gates": gate_results},
            metadata={"variant_id": variant.variant_id, "iteration_index": iteration_index},
        )
        scorecard_artifact = self._store.write_artifact(
            session.session_id,
            kind="benchmark_scorecard",
            label=f"{variant.variant_id}{suffix}-scorecard",
            filename=f"{variant.variant_id}{suffix}-benchmark-scorecard.json",
            payload={
                "variant_id": variant.variant_id,
                "profile": profile_name,
                "primary_quality_score": primary_quality_score,
                "backtest_confidence": backtest_confidence,
                "style_distance": style_distance,
                "risk_penalty": risk_penalty,
                "confidence_vs_noise": confidence_vs_noise,
                "score_components": score_components,
            },
            metadata={"variant_id": variant.variant_id, "iteration_index": iteration_index},
        )
        bundle_artifact = self._store.write_artifact(
            session.session_id,
            kind="benchmark_artifact_bundle",
            label=f"{variant.variant_id}{suffix}-bundle",
            filename=f"{variant.variant_id}{suffix}-benchmark-bundle.json",
            payload={
                "variant_id": variant.variant_id,
                "action_id": action_id,
                "profile": profile_name,
                "artifacts": [artifact.ref.model_dump(mode="json") for artifact in bundle["artifacts"]],
            },
            metadata={"variant_id": variant.variant_id, "iteration_index": iteration_index},
        )
        replay_artifact = self._store.write_artifact(
            session.session_id,
            kind="kernel_replay_payload",
            label=f"{variant.variant_id}{suffix}-benchmark-replay",
            filename=f"{variant.variant_id}{suffix}-benchmark-replay.json",
            payload={
                "workflow_id": request.workflow_id,
                "session_id": session.session_id,
                "action_id": action_id,
                "variant_id": variant.variant_id,
                "profile": profile_name,
                "package_root": bundle["build"].get("package_root"),
            },
            metadata={
                "variant_id": variant.variant_id,
                "iteration_index": iteration_index,
                "role_hint": "reviewer",
                "artifact_suffix": _string(artifact_suffix),
            },
        )
        scorecard = BenchmarkScorecard(
            variant_id=variant.variant_id,
            primary_quality_score=primary_quality_score,
            backtest_confidence=backtest_confidence,
            execution_readiness=execution_readiness,
            strategy_quality=strategy_quality,
            style_distance=style_distance,
            risk_penalty=risk_penalty,
            confidence_vs_noise=confidence_vs_noise,
            hard_gates_passed=hard_gates_passed,
            notes=notes,
            artifacts=bundle["artifacts"] + [gate_artifact, scorecard_artifact, bundle_artifact, replay_artifact],
            metadata={
                "source": "artifact-backed-benchmark",
                "action_id": action_id,
                "profile": profile_name,
                "iteration_index": iteration_index,
                "score_components": score_components,
                "gates": gate_results,
            },
        )
        if persist_record:
            self._store.save_scorecard(session.session_id, scorecard)
        return scorecard

    def _resolve_materialization_bundle(
        self,
        request: WorkflowRunRequest,
        *,
        session: ResearchSessionState,
        iteration_index: int,
        variant: WorkflowVariant,
        baseline_variant: WorkflowVariant,
    ) -> dict[str, Any]:
        build_payload = _artifact_payload(variant.artifacts, "skill_package")
        validation_payload = _artifact_payload(variant.artifacts, "validation_report")
        qa_payload = _artifact_payload(variant.artifacts, "qa_report")
        artifacts = [
            artifact
            for artifact in variant.artifacts
            if artifact.ref.kind
            in {
                "skill_package",
                "validation_report",
                "qa_report",
                "kernel_replay_payload",
                "candidate_variant",
                "variant_materialization",
                "baseline_materialization",
            }
        ]
        if not build_payload or not validation_payload:
            candidate = _variant_candidate(
                request,
                session_id=session.session_id,
                variant=variant,
                baseline_variant=baseline_variant,
            )
            output_root = self._store.session_dir(session.session_id) / "benchmark-packages" / variant.variant_id
            build = self._compiler.compile(candidate, output_root=output_root, package_kind="script", force=True)
            validation = self._compiler.validate(build.package_root, candidate=candidate)
            build_payload = build.to_dict()
            validation_payload = validation.to_dict()
            build_artifact = self._store.write_artifact(
                session.session_id,
                kind="skill_package",
                label=f"{variant.variant_id}-fallback",
                filename=f"{variant.variant_id}-fallback-skill-package.json",
                payload=build_payload,
                metadata={"variant_id": variant.variant_id, "iteration_index": iteration_index},
            )
            validation_artifact = self._store.write_artifact(
                session.session_id,
                kind="validation_report",
                label=f"{variant.variant_id}-fallback",
                filename=f"{variant.variant_id}-fallback-validation-report.json",
                payload=validation_payload,
                metadata={"variant_id": variant.variant_id, "iteration_index": iteration_index},
            )
            artifacts.extend([build_artifact, validation_artifact])
        backtest_payload = dict(variant.metadata.get("backtest") or {})
        if not backtest_payload:
            baseline_backtest = dict(baseline_variant.metadata.get("backtest") or {})
            if baseline_backtest:
                backtest_payload = {
                    **baseline_backtest,
                    "metadata": {
                        **dict(baseline_backtest.get("metadata") or {}),
                        "baseline_reused": True,
                        "derived_for_variant": variant.variant_id,
                    },
                }
            else:
                backtest_payload = {
                    "confidence_score": 0.62,
                    "confidence_label": "medium",
                    "metadata": {"derived_for_variant": variant.variant_id},
                }
        if not qa_payload:
            qa_payload = _derive_qa_payload(validation=validation_payload, backtest=backtest_payload)
            qa_artifact = self._store.write_artifact(
                session.session_id,
                kind="qa_report",
                label=variant.variant_id,
                filename=f"{variant.variant_id}-qa-report.json",
                payload=qa_payload,
                metadata={"variant_id": variant.variant_id, "iteration_index": iteration_index},
            )
            artifacts.append(qa_artifact)
        return {
            "build": build_payload,
            "validation": validation_payload,
            "qa": qa_payload,
            "backtest": backtest_payload,
            "artifacts": artifacts,
        }


class ReviewPluginExecutor:
    def __init__(self, plugin_registry: WorkflowPluginRegistry, store: ResearchLoopStore) -> None:
        self._plugin_spec = plugin_registry.resolve_plugin("review")
        self._store = store

    def dispatch_action(self, action_id: str, **kwargs: Any):
        if action_id in {"review.evaluate_candidates", "review.finalize_candidate"}:
            return self.execute(action_id=action_id, **kwargs)
        raise KeyError(f"unsupported review action {action_id!r}")

    def execute(
        self,
        *,
        action_id: str,
        request: WorkflowRunRequest,
        session: ResearchSessionState,
        iteration_index: int,
        baseline_scorecard: BenchmarkScorecard,
        scorecards: list[BenchmarkScorecard],
        persist_record: bool = True,
        artifact_suffix: str | None = None,
    ) -> list[ReviewDecision]:
        contract = self._plugin_spec.action_contract(action_id)
        policy = dict(contract.get("review_policy") or {})
        decisions: list[ReviewDecision] = []
        suffix = f"-{_string(artifact_suffix)}" if _string(artifact_suffix) else ""
        for scorecard in scorecards:
            gates = list(dict(_artifact_payload(scorecard.artifacts, "gate_result")).get("gates") or [])
            failed_gates = [item for item in gates if not bool(item.get("passed"))]
            quality_delta = round(scorecard.primary_quality_score - baseline_scorecard.primary_quality_score, 4)
            noise_delta = round(scorecard.confidence_vs_noise - baseline_scorecard.confidence_vs_noise, 4)
            if failed_gates:
                status = "discard"
                reasoning = "Variant failed one or more hard gates."
            elif (
                quality_delta >= _safe_float(policy.get("recommended_quality_delta"), 0.03)
                and noise_delta >= _safe_float(policy.get("recommended_noise_delta"), 0.0)
                and scorecard.style_distance <= _safe_float(policy.get("max_style_distance_for_auto"), 0.35)
                and scorecard.risk_penalty <= _safe_float(policy.get("max_risk_penalty_for_auto"), 0.3)
                and scorecard.primary_quality_score >= _safe_float(policy.get("min_quality_score"), 0.58)
            ):
                status = "recommended"
                reasoning = "Variant improved the benchmark with acceptable drift, risk, and replay readiness."
            elif quality_delta >= _safe_float(policy.get("keep_quality_delta"), 0.0):
                status = "keep"
                reasoning = "Variant is directionally better but not yet strong enough for automatic recommendation."
            else:
                status = "review_required"
                reasoning = "Variant remains inside the noise band and needs a human decision."
            blocking_findings = [f"{item['gate_id']} threshold not met" for item in failed_gates]
            if not blocking_findings and status == "review_required":
                blocking_findings.append("benchmark improvement is inside the current noise band")
            retry_focus = "strategy_spec"
            failed_gate_ids = {str(item.get("gate_id") or "") for item in failed_gates}
            if "risk-floor" in failed_gate_ids:
                retry_focus = "risk_filters"
            elif "style-drift" in failed_gate_ids:
                retry_focus = "style_profile"
            elif "readiness" in failed_gate_ids:
                retry_focus = "execution_intent"
            follow_up_actions = [
                "request human approval" if status == "recommended" else f"iterate on {retry_focus}",
            ]
            governance = ReviewArtifact(
                variant_id=scorecard.variant_id,
                governance_status=status,
                approval_required=status in {"recommended", "review_required"},
                activation_allowed=status == "recommended",
                rationale=reasoning,
                blocking_findings=blocking_findings,
                follow_up_actions=follow_up_actions,
                metadata={
                    "iteration_index": iteration_index,
                    "primary_quality_score": scorecard.primary_quality_score,
                    "style_distance": scorecard.style_distance,
                    "risk_penalty": scorecard.risk_penalty,
                    "quality_delta": quality_delta,
                    "noise_delta": noise_delta,
                },
            )
            governance_artifact = self._store.write_artifact(
                session.session_id,
                kind="review_governance",
                label=f"{scorecard.variant_id}{suffix}",
                filename=f"{scorecard.variant_id}{suffix}-review-governance.json",
                payload=governance.model_dump(mode="json"),
                metadata={"variant_id": scorecard.variant_id, "iteration_index": iteration_index},
            )
            notes_artifact = self._store.write_artifact(
                session.session_id,
                kind="review_notes",
                label=f"{scorecard.variant_id}{suffix}",
                filename=f"{scorecard.variant_id}{suffix}-review-notes.json",
                payload={
                    "variant_id": scorecard.variant_id,
                    "objective": request.objective,
                    "quality_delta": quality_delta,
                    "noise_delta": noise_delta,
                    "gates": gates,
                },
                metadata={"variant_id": scorecard.variant_id, "iteration_index": iteration_index},
            )
            retry_artifact = self._store.write_artifact(
                session.session_id,
                kind="retry_suggestion",
                label=f"{scorecard.variant_id}{suffix}",
                filename=f"{scorecard.variant_id}{suffix}-retry-suggestion.json",
                payload={
                    "variant_id": scorecard.variant_id,
                    "status": status,
                    "retry_focus": retry_focus,
                    "follow_up_actions": follow_up_actions,
                },
                metadata={"variant_id": scorecard.variant_id, "iteration_index": iteration_index},
            )
            handoff_artifact = self._store.write_artifact(
                session.session_id,
                kind="kernel_handoff_payload",
                label=f"{scorecard.variant_id}{suffix}",
                filename=f"{scorecard.variant_id}{suffix}-review-handoff.json",
                payload={
                    "workflow_id": request.workflow_id,
                    "session_id": session.session_id,
                    "action_id": action_id,
                    "variant_id": scorecard.variant_id,
                    "status": status,
                    "reasoning": reasoning,
                },
                metadata={"variant_id": scorecard.variant_id, "iteration_index": iteration_index, "role_hint": "reviewer"},
            )
            decision = ReviewDecision(
                variant_id=scorecard.variant_id,
                status=status,
                reasoning=reasoning,
                review_notes=[
                    f"objective={request.objective}",
                    f"quality_delta={quality_delta}",
                    f"noise_delta={noise_delta}",
                    f"style_distance={scorecard.style_distance}",
                    f"risk_penalty={scorecard.risk_penalty}",
                ],
                governance=governance.model_copy(update={"artifacts": [governance_artifact]}),
                artifacts=[governance_artifact, notes_artifact, retry_artifact, handoff_artifact],
                metadata={
                    "source": "artifact-backed-review",
                    "iteration_index": iteration_index,
                    "policy": policy,
                    "artifact_suffix": _string(artifact_suffix),
                },
            )
            if persist_record:
                self._store.save_review(session.session_id, decision)
            decisions.append(decision)
        return decisions


class ApprovalConvergencePluginExecutor:
    def __init__(self, plugin_registry: WorkflowPluginRegistry, store: ResearchLoopStore) -> None:
        self._plugin_spec = plugin_registry.resolve_plugin("approval-convergence")
        self._store = store

    def dispatch_action(self, action_id: str, **kwargs: Any) -> ApprovalConvergenceResult:
        if action_id == "approval_convergence.converge_approval":
            return self.execute(action_id=action_id, **kwargs)
        raise KeyError(f"unsupported approval-convergence action {action_id!r}")

    def execute(
        self,
        *,
        action_id: str,
        request: WorkflowRunRequest,
        session: ResearchSessionState,
        baseline_variant: WorkflowVariant,
        recommendation_bundle: RecommendationBundle,
        scorecards: list[BenchmarkScorecard],
        review_decisions: list[ReviewDecision],
    ) -> ApprovalConvergenceResult:
        contract = self._plugin_spec.action_contract(action_id)
        approval_policy = dict(contract.get("approval_policy") or {})
        selected_variant = recommendation_bundle.selected_variant
        selected_variant_id = _string(
            recommendation_bundle.recommended_variant_id
            or (selected_variant.variant_id if selected_variant is not None else "")
        )
        finalist_scorecard = next(
            (item for item in scorecards if item.variant_id == selected_variant_id),
            scorecards[0] if scorecards else None,
        )
        finalist_review = next(
            (item for item in review_decisions if item.variant_id == selected_variant_id),
            review_decisions[0] if review_decisions else None,
        )
        if finalist_scorecard is None or finalist_review is None:
            raise RuntimeError("approval convergence requires finalist benchmark and review artifacts")
        governance = finalist_review.governance
        allowed_statuses = {
            _string(item)
            for item in list(approval_policy.get("allowed_review_statuses") or ["recommended", "keep"])
            if _string(item)
        }
        approval_required = True if governance is None else governance.approval_required
        approval_granted = bool(
            dict(request.operator_hints).get("approval_granted")
            or dict(request.metadata).get("approval_granted")
        )
        activation_requested = bool(
            dict(request.operator_hints).get("activation_requested")
            or dict(request.metadata).get("activation_requested")
            or dict(request.metadata).get("activate")
        )
        activation_allowed = False if governance is None else governance.activation_allowed
        if finalist_review.status not in allowed_statuses:
            status = "blocked"
            rationale = "Finalist review did not clear the approval policy."
        elif approval_granted and activation_requested and activation_allowed:
            status = "activated"
            rationale = "Human approval granted and the finalist cleared activation readiness."
        elif approval_granted:
            status = "approved"
            rationale = "Human approval granted; finalist is approved for the next activation step."
        else:
            status = "review_required"
            rationale = "Finalist is ready for a human approval decision before activation."

        approval_recommendation_artifact = self._store.write_artifact(
            session.session_id,
            kind="approval_recommendation",
            label=f"{selected_variant_id}-approval",
            filename=f"{selected_variant_id}-approval-recommendation.json",
            payload={
                "workflow_id": request.workflow_id,
                "session_id": session.session_id,
                "variant_id": selected_variant_id,
                "recommended_variant_id": selected_variant_id,
                "status": status,
                "rationale": rationale,
                "review_status": finalist_review.status,
                "approval_required": approval_required,
                "approval_granted": approval_granted,
                "activation_requested": activation_requested,
                "activation_allowed": activation_allowed,
            },
            metadata={"variant_id": selected_variant_id, "workflow_id": request.workflow_id},
        )
        approval_decision_artifact = self._store.write_artifact(
            session.session_id,
            kind="approval_decision",
            label=f"{selected_variant_id}-approval",
            filename=f"{selected_variant_id}-approval-decision.json",
            payload={
                "workflow_id": request.workflow_id,
                "session_id": session.session_id,
                "variant_id": selected_variant_id,
                "status": status,
                "approval_required": approval_required,
                "approval_granted": approval_granted,
                "activation_requested": activation_requested,
                "activation_allowed": activation_allowed,
                "rationale": rationale,
            },
            metadata={"variant_id": selected_variant_id, "workflow_id": request.workflow_id},
        )
        activation_record_artifact = self._store.write_artifact(
            session.session_id,
            kind="activation_record",
            label=f"{selected_variant_id}-activation",
            filename=f"{selected_variant_id}-activation-record.json",
            payload={
                "workflow_id": request.workflow_id,
                "session_id": session.session_id,
                "variant_id": selected_variant_id,
                "status": status,
                "activation_requested": activation_requested,
                "activation_allowed": activation_allowed,
                "activated": status == "activated",
            },
            metadata={"variant_id": selected_variant_id, "workflow_id": request.workflow_id},
        )
        handoff_artifact = self._store.write_artifact(
            session.session_id,
            kind="kernel_handoff_payload",
            label=f"{selected_variant_id}-approval",
            filename=f"{selected_variant_id}-approval-handoff.json",
            payload={
                "workflow_id": request.workflow_id,
                "session_id": session.session_id,
                "action_id": action_id,
                "variant_id": selected_variant_id,
                "approval_required": approval_required,
                "approval_granted": approval_granted,
                "activation_requested": activation_requested,
                "status": status,
                "rationale": rationale,
            },
            metadata={"variant_id": selected_variant_id, "workflow_id": request.workflow_id, "role_hint": "reviewer"},
        )
        approval = ApprovalActivationRecord(
            variant_id=selected_variant_id,
            approval_required=approval_required,
            approval_granted=approval_granted,
            activation_requested=activation_requested,
            activation_allowed=activation_allowed,
            status=status,
            rationale=rationale,
            artifacts=[approval_decision_artifact, activation_record_artifact, handoff_artifact],
            metadata={
                "source": "artifact-backed-approval-convergence",
                "approval_policy": approval_policy,
                "review_status": finalist_review.status,
            },
        )
        approval_bundle_artifact = self._store.write_artifact(
            session.session_id,
            kind="approval_activation_bundle",
            label=f"{selected_variant_id}-approval",
            filename=f"{selected_variant_id}-approval-convergence.json",
            payload={
                "workflow_id": request.workflow_id,
                "session_id": session.session_id,
                "baseline_variant_id": baseline_variant.variant_id,
                "recommended_variant_id": selected_variant_id,
                "approval": approval.model_dump(mode="json"),
            },
            metadata={"variant_id": selected_variant_id, "workflow_id": request.workflow_id},
        )
        approval_convergence_artifact = self._store.write_artifact(
            session.session_id,
            kind="approval_convergence_bundle",
            label=f"{selected_variant_id}-approval",
            filename=f"{selected_variant_id}-approval-convergence-bundle.json",
            payload={
                "workflow_id": request.workflow_id,
                "session_id": session.session_id,
                "baseline_variant_id": baseline_variant.variant_id,
                "recommended_variant_id": selected_variant_id,
                "approval": approval.model_dump(mode="json"),
            },
            metadata={"variant_id": selected_variant_id, "workflow_id": request.workflow_id},
        )
        return ApprovalConvergenceResult(
            workflow_id=request.workflow_id,
            session_id=session.session_id,
            workspace_id=session.workspace_id,
            baseline_variant_id=baseline_variant.variant_id,
            recommended_variant_id=selected_variant_id,
            selected_variant=selected_variant,
            recommendation_bundle=recommendation_bundle,
            benchmark_scorecard=finalist_scorecard,
            review_decision=finalist_review,
            approval=approval.model_copy(
                update={"artifacts": [*approval.artifacts, approval_bundle_artifact, approval_convergence_artifact]}
            ),
            status=status,
            summary=rationale,
            artifacts=[
                *finalist_scorecard.artifacts,
                *finalist_review.artifacts,
                approval_decision_artifact,
                activation_record_artifact,
                approval_recommendation_artifact,
                handoff_artifact,
                approval_bundle_artifact,
                approval_convergence_artifact,
            ],
            metadata={
                "source": "artifact-backed-approval-convergence",
                "approval_policy": approval_policy,
            },
        )
