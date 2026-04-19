from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ot_skill_enterprise.nextgen.adapters import AdapterRegistry
from ot_skill_enterprise.nextgen.plugins import WorkflowPluginRegistry
from ot_skill_enterprise.nextgen.worker_bridge import (
    DistillationWorkerHandler,
    WorkerBridgeInvocationRequest,
    WorkflowWorkerRuntime,
    build_worker_runtime,
    build_distillation_worker_handler,
    load_distillation_worker_protocol,
)
from ot_skill_enterprise.skills_compiler import build_skill_package_compiler
from ot_skill_enterprise.style_distillation import build_wallet_style_distillation_service

from .executors import (
    AutoresearchPluginExecutor,
    BenchmarkPluginExecutor,
    ReviewPluginExecutor,
    SkillCreationPluginExecutor,
)
from .models import (
    ApprovalConvergenceResult,
    BenchmarkScorecard,
    RecommendationBundle,
    ResearchIterationRecord,
    ResearchSessionState,
    ReviewDecision,
    WorkflowArtifact,
    WorkflowRunRequest,
    WorkflowRunResult,
    WorkflowVariant,
)
from .store import ResearchLoopStore


class PythonCompatWorkflowRunner:
    def __init__(
        self,
        *,
        project_root: Path,
        workspace_root: Path,
        plugin_registry: WorkflowPluginRegistry,
        adapter_registry: AdapterRegistry,
        distillation_worker_handler: DistillationWorkerHandler | None,
        skill_creation_executor: SkillCreationPluginExecutor,
        autoresearch_executor: AutoresearchPluginExecutor,
        benchmark_executor: BenchmarkPluginExecutor,
        review_executor: ReviewPluginExecutor,
        worker_runtime: WorkflowWorkerRuntime,
        reflection_service: Any | None = None,
        workflow_data_source_adapter_id: str | None = None,
        store: ResearchLoopStore | None = None,
    ) -> None:
        self.project_root = project_root
        self.workspace_root = workspace_root
        self.plugin_registry = plugin_registry
        self.adapter_registry = adapter_registry
        self.store = store or ResearchLoopStore(workspace_root)
        self.distillation_worker_handler = distillation_worker_handler
        self._distillation_handler_adapter_id = workflow_data_source_adapter_id if distillation_worker_handler is not None else None
        self.reflection_service = reflection_service
        self.workflow_data_source_adapter_id = workflow_data_source_adapter_id
        self.skill_creation_executor = skill_creation_executor
        self.autoresearch_executor = autoresearch_executor
        self.benchmark_executor = benchmark_executor
        self.review_executor = review_executor
        self.worker_runtime = worker_runtime

    def workflow_action(self, workflow_id: str, step_id: str) -> str:
        return self.plugin_registry.resolve_step_action(workflow_id, step_id)

    def request_with_resolved_adapters(
        self,
        request: WorkflowRunRequest,
        *,
        workspace_adapter_ids: Mapping[str, str],
        require_execution: bool,
    ) -> WorkflowRunRequest:
        data_source_adapter_id = (
            str(request.data_source_adapter_id or "").strip()
            or str(self.workflow_data_source_adapter_id or "").strip()
            or str(workspace_adapter_ids.get("data_source") or "").strip()
        )
        execution_adapter_id = (
            str(request.execution_adapter_id or "").strip()
            or str(workspace_adapter_ids.get("execution") or "").strip()
        )
        if not data_source_adapter_id:
            raise ValueError("workflow execution requires an explicit or workspace-derived data_source_adapter_id")
        if require_execution and not execution_adapter_id:
            raise ValueError("workflow execution requires an explicit or workspace-derived execution_adapter_id")
        if (
            data_source_adapter_id == (request.data_source_adapter_id or "")
            and execution_adapter_id == (request.execution_adapter_id or "")
        ):
            return request
        return request.model_copy(
            update={
                "data_source_adapter_id": data_source_adapter_id,
                "execution_adapter_id": execution_adapter_id or None,
            }
        )

    def distillation_worker_handler_for(self, request: WorkflowRunRequest) -> DistillationWorkerHandler:
        if (
            self.distillation_worker_handler is not None
            and (
                self._distillation_handler_adapter_id is None
                or self._distillation_handler_adapter_id == request.data_source_adapter_id
            )
        ):
            return self.distillation_worker_handler
        if not request.data_source_adapter_id:
            raise ValueError("workflow execution requires an explicit or workspace-derived data_source_adapter_id")
        service = build_wallet_style_distillation_service(
            project_root=self.project_root,
            workspace_root=self.workspace_root,
            reflection_service=self.reflection_service,
            adapter_registry=self.adapter_registry,
            data_source_adapter_id=request.data_source_adapter_id,
            require_explicit_data_source_adapter=True,
            allow_builtin_adapter_registry_fallback=False,
        )
        handler = build_distillation_worker_handler(service)
        self.distillation_worker_handler = handler
        self._distillation_handler_adapter_id = request.data_source_adapter_id
        self.workflow_data_source_adapter_id = request.data_source_adapter_id
        return handler

    def _invoke_worker_action(
        self,
        *,
        action_id: str,
        workflow_id: str,
        workflow_step_id: str,
        request: WorkflowRunRequest,
        state: Mapping[str, Any],
    ) -> dict[str, Any]:
        invocation = WorkerBridgeInvocationRequest.model_validate(
            {
                "bridge_id": "python-worker-bridge",
                "bridge_version": "1.0.0",
                "action_id": action_id,
                "workflow_id": workflow_id,
                "workflow_step_id": workflow_step_id,
                "workspace_dir": str(
                    Path(request.workspace_dir).expanduser().resolve()
                    if request.workspace_dir
                    else self.workspace_root
                ),
                "request": request.model_dump(mode="json"),
                "state": dict(state),
                "metadata": {"session_id": request.session_id},
            }
        )
        response = self.worker_runtime.invoke(invocation)
        if not response.ok:
            message = response.error.message if response.error is not None else f"worker action {action_id} failed"
            raise RuntimeError(message)
        return {
            "outputs": dict(response.outputs),
            "state_patch": dict(response.state_patch),
            "artifacts": list(response.artifacts),
            "metadata": dict(response.metadata),
            "status": response.status,
        }

    def run_distillation_seed_payload(
        self,
        payload: Mapping[str, Any],
        *,
        workspace_adapter_ids: Mapping[str, str],
    ) -> dict[str, Any]:
        request = WorkflowRunRequest.model_validate(payload)
        return self.run_distillation_seed(
            request,
            workspace_adapter_ids=workspace_adapter_ids,
        ).model_dump(mode="json")

    def run_autonomous_research_payload(
        self,
        payload: Mapping[str, Any],
        *,
        workspace_adapter_ids: Mapping[str, str],
    ) -> dict[str, Any]:
        request = WorkflowRunRequest.model_validate(payload)
        return self.run_autonomous_research(
            request,
            workspace_adapter_ids=workspace_adapter_ids,
        ).model_dump(mode="json")

    def run_approval_convergence_payload(
        self,
        payload: Mapping[str, Any],
        *,
        workspace_adapter_ids: Mapping[str, str],
    ) -> dict[str, Any]:
        request = WorkflowRunRequest.model_validate(payload)
        return self.run_approval_convergence(
            request,
            workspace_adapter_ids=workspace_adapter_ids,
        ).model_dump(mode="json")

    def run_distillation_seed(
        self,
        request: WorkflowRunRequest,
        *,
        workspace_adapter_ids: Mapping[str, str],
    ) -> WorkflowRunResult:
        request = self.request_with_resolved_adapters(
            request,
            workspace_adapter_ids=workspace_adapter_ids,
            require_execution=False,
        )
        protocol = load_distillation_worker_protocol(
            self.plugin_registry,
            workflow_id="distillation_seed",
            step_id="distill_baseline",
        )
        protocol_result = self.distillation_worker_handler_for(request).run_protocol(
            workflow_request=request.model_copy(update={"workflow_id": "distillation_seed"}),
            protocol=protocol,
        )
        if protocol_result.baseline_variant is None:
            raise RuntimeError("distillation worker bridge did not return a baseline variant")
        session_id = request.session_id or self.store.resolve_session_id(request.model_dump(mode="json"))
        materialize_action = self.workflow_action("distillation_seed", "materialize_baseline")
        baseline_variant = self.skill_creation_executor.dispatch_action(
            materialize_action,
            request=request.model_copy(update={"workflow_id": "distillation_seed", "session_id": session_id}),
            session_id=session_id,
            baseline_variant=protocol_result.baseline_variant,
            raw_distillation_result=protocol_result.raw_result,
        )
        return WorkflowRunResult(
            workflow_id="distillation_seed",
            session_id=session_id,
            baseline_variant=baseline_variant,
            artifacts=[*protocol_result.artifacts, *baseline_variant.artifacts],
            metadata={
                "project_root": str(self.project_root),
                "raw_distillation_result": protocol_result.raw_result,
                "workflow_summary": "baseline seed generated and materialized",
                "distillation_protocol": protocol.model_dump(mode="json"),
                "worker_bridge": protocol_result.model_dump(mode="json"),
                "materialization_action": materialize_action,
                "selected_adapters": {
                    "data_source": request.data_source_adapter_id,
                    "execution": request.execution_adapter_id,
                },
            },
        )

    def run_autonomous_research(
        self,
        request: WorkflowRunRequest,
        *,
        workspace_adapter_ids: Mapping[str, str],
    ) -> RecommendationBundle:
        request = self.request_with_resolved_adapters(
            request,
            workspace_adapter_ids=workspace_adapter_ids,
            require_execution=True,
        )
        session = self._load_or_create_session(request)
        existing_recommendation = self.store.load_recommendation(session.session_id)
        if existing_recommendation is not None and session.status == "completed":
            existing_recommendation.metadata = {
                **dict(existing_recommendation.metadata),
                "project_root": str(self.project_root),
                "selected_adapters": {
                    "data_source": request.data_source_adapter_id,
                    "execution": request.execution_adapter_id,
                },
                "replayed": True,
            }
            return existing_recommendation

        baseline_result = self.run_distillation_seed(
            request.model_copy(update={"workflow_id": "distillation_seed", "session_id": session.session_id}),
            workspace_adapter_ids=workspace_adapter_ids,
        )
        baseline_variant = baseline_result.baseline_variant
        baseline_artifacts = list(baseline_result.artifacts)
        self.store.save_variant(session.session_id, baseline_variant)
        baseline_scorecard = self._load_or_benchmark_baseline(session, request, baseline_variant)
        parent_variant = self._resolve_parent_variant(session, baseline_variant)
        final_recommendation: RecommendationBundle | None = existing_recommendation

        plan_action = self.workflow_action("autonomous_research", "plan_iteration")
        create_action = self.workflow_action("autonomous_research", "create_variants")
        benchmark_action = self.workflow_action("autonomous_research", "benchmark_candidates")
        review_action = self.workflow_action("autonomous_research", "review_candidates")
        decide_action = self.workflow_action("autonomous_research", "decide_next_iteration")

        for iteration_index in range(session.current_iteration + 1, session.max_iterations + 1):
            plans = self.autoresearch_executor.dispatch_action(
                plan_action,
                request=request,
                baseline_variant=baseline_variant,
                parent_variant=parent_variant,
                session_id=session.session_id,
                iteration_index=iteration_index,
            )
            candidate_variants = self.skill_creation_executor.dispatch_action(
                create_action,
                request=request,
                session_id=session.session_id,
                baseline_variant=baseline_variant,
                parent_variant=parent_variant,
                plans=plans,
                iteration_index=iteration_index,
            )
            for variant in candidate_variants:
                self.store.save_variant(session.session_id, variant)
            candidate_scorecards = self.benchmark_executor.dispatch_action(
                benchmark_action,
                request=request,
                session=session,
                iteration_index=iteration_index,
                baseline_variant=baseline_variant,
                variants=candidate_variants,
            )
            review_decisions = self.review_executor.dispatch_action(
                review_action,
                request=request,
                session=session,
                iteration_index=iteration_index,
                baseline_scorecard=baseline_scorecard,
                scorecards=candidate_scorecards,
            )
            all_variants = self._candidate_variants_for_session(session.session_id)
            all_scorecards = [
                item
                for item in self.store.list_scorecards(session.session_id)
                if item.variant_id != baseline_variant.variant_id
            ]
            all_reviews = self.store.list_reviews(session.session_id)
            current_iterations = self.store.list_iterations(session.session_id)
            final_recommendation = self.autoresearch_executor.dispatch_action(
                decide_action,
                workflow_id="autonomous_research",
                request=request,
                session_id=session.session_id,
                iteration_index=iteration_index,
                baseline_variant=baseline_variant,
                variants=all_variants,
                scorecards=all_scorecards,
                review_decisions=all_reviews,
                iterations=current_iterations,
                baseline_scorecard=baseline_scorecard,
            )
            iteration_record = ResearchIterationRecord(
                session_id=session.session_id,
                iteration_index=iteration_index,
                parent_variant_id=parent_variant.variant_id,
                plan_ids=[item.plan_id for item in plans],
                generated_variant_ids=[item.variant_id for item in candidate_variants],
                benchmarked_variant_ids=[item.variant_id for item in candidate_scorecards],
                reviewed_variant_ids=[item.variant_id for item in review_decisions],
                selected_variant_id=final_recommendation.selected_variant.variant_id if final_recommendation.selected_variant else None,
                recommendation_status=final_recommendation.status,
                stop_decision=final_recommendation.stop_decision,
                artifacts=[
                    self.store.write_artifact(
                        session.session_id,
                        kind="iteration_summary",
                        label=f"iteration-{iteration_index}",
                        filename=f"iteration-{iteration_index:03d}.json",
                        payload={
                            "iteration_index": iteration_index,
                            "plan_ids": [item.plan_id for item in plans],
                            "generated_variant_ids": [item.variant_id for item in candidate_variants],
                            "benchmarked_variant_ids": [item.variant_id for item in candidate_scorecards],
                            "reviewed_variant_ids": [item.variant_id for item in review_decisions],
                            "selected_variant_id": final_recommendation.selected_variant.variant_id if final_recommendation.selected_variant else None,
                            "status": final_recommendation.status,
                            "stop_decision": final_recommendation.stop_decision.model_dump(mode="json")
                            if final_recommendation.stop_decision
                            else None,
                        },
                        metadata={"iteration_index": iteration_index},
                    )
                ],
                metadata={"objective": request.objective},
            )
            self.store.save_iteration(iteration_record)
            iterations = self.store.list_iterations(session.session_id)
            final_recommendation = final_recommendation.model_copy(
                update={
                    "iterations": iterations,
                    "iteration_count": len(iterations),
                    "artifacts": [*final_recommendation.artifacts, *baseline_artifacts],
                    "metadata": {
                        **dict(final_recommendation.metadata),
                        "project_root": str(self.project_root),
                        "selected_adapters": {
                            "data_source": request.data_source_adapter_id,
                            "execution": request.execution_adapter_id,
                        },
                        "candidate_variant_count": len(all_variants),
                        "baseline_scorecard": baseline_scorecard.model_dump(mode="json"),
                        "session_root": str(self.store.session_dir(session.session_id)),
                    },
                }
            )
            self.store.save_leaderboard(session.session_id, final_recommendation.leaderboard)
            self.store.save_recommendation(final_recommendation)
            stop_decision = final_recommendation.stop_decision
            session = session.model_copy(
                update={
                    "current_iteration": iteration_index,
                    "status": "completed" if stop_decision and stop_decision.decision == "stop" else "running",
                    "active_parent_variant_id": (stop_decision.next_parent_variant_id if stop_decision else None) or parent_variant.variant_id,
                    "stop_decision": stop_decision,
                    "recommendation_variant_id": final_recommendation.recommended_variant_id
                    or (final_recommendation.selected_variant.variant_id if final_recommendation.selected_variant else None),
                }
            )
            self.store.save_session(session)
            if stop_decision and stop_decision.decision == "stop":
                break
            if final_recommendation.selected_variant is not None:
                parent_variant = final_recommendation.selected_variant

        if final_recommendation is None:
            raise RuntimeError("autoresearch did not produce a recommendation bundle")
        return final_recommendation

    def run_approval_convergence(
        self,
        request: WorkflowRunRequest,
        *,
        workspace_adapter_ids: Mapping[str, str],
    ) -> ApprovalConvergenceResult:
        request = self.request_with_resolved_adapters(
            request,
            workspace_adapter_ids=workspace_adapter_ids,
            require_execution=True,
        )
        if not request.session_id:
            raise ValueError("approval_convergence requires request.session_id")
        request_hints = {
            "approval_granted": bool(dict(request.operator_hints).get("approval_granted") or dict(request.metadata).get("approval_granted")),
            "activation_requested": bool(dict(request.operator_hints).get("activation_requested") or dict(request.metadata).get("activation_requested") or dict(request.metadata).get("activate")),
        }
        existing = self.store.load_approval(request.session_id)
        if existing is not None and dict(existing.metadata.get("request_hints") or {}) == request_hints:
            existing.metadata = {
                **dict(existing.metadata),
                "project_root": str(self.project_root),
                "default_adapters": self.adapter_registry.defaults(),
                "session_root": str(self.store.session_dir(request.session_id)),
                "replayed": True,
            }
            return existing

        session = self.store.load_session(request.session_id)
        if session is None:
            raise RuntimeError(f"unknown research session {request.session_id!r}")
        recommendation = self.store.load_recommendation(request.session_id)
        if recommendation is None:
            raise RuntimeError(f"research session {request.session_id!r} has no recommendation bundle")
        selected_variant_id = (
            recommendation.recommended_variant_id
            or (recommendation.selected_variant.variant_id if recommendation.selected_variant is not None else None)
            or session.recommendation_variant_id
        )
        if not selected_variant_id:
            raise RuntimeError("recommendation bundle does not identify a finalist variant")
        finalist_variant = self.store.load_variant(request.session_id, selected_variant_id) or recommendation.selected_variant
        if finalist_variant is None:
            raise RuntimeError(f"unable to resolve finalist variant {selected_variant_id!r}")
        baseline_variant = self.store.load_variant(request.session_id, recommendation.baseline_variant_id or session.baseline_variant_id)
        if baseline_variant is None:
            raise RuntimeError("approval convergence requires the baseline variant to be present")
        baseline_scorecard = self._load_or_benchmark_baseline(session, request, baseline_variant)
        benchmark_action = self.workflow_action("approval_convergence", "benchmark_finalist")
        review_action = self.workflow_action("approval_convergence", "review_finalist")
        workflow_request = request.model_copy(update={"workflow_id": "approval_convergence"})
        benchmark_runtime = self._invoke_worker_action(
            action_id=benchmark_action,
            workflow_id="approval_convergence",
            workflow_step_id="benchmark_finalist",
            request=workflow_request,
            state={
                "baseline_variant": baseline_variant.model_dump(mode="json"),
                "selected_variant": finalist_variant.model_dump(mode="json"),
                "baseline_scorecard": baseline_scorecard.model_dump(mode="json"),
                "iteration_index": max(1, session.current_iteration or 1),
            },
        )
        finalist_scorecard = BenchmarkScorecard.model_validate(benchmark_runtime["state_patch"]["benchmark_scorecard"])
        review_runtime = self._invoke_worker_action(
            action_id=review_action,
            workflow_id="approval_convergence",
            workflow_step_id="review_finalist",
            request=workflow_request,
            state={
                "baseline_variant": baseline_variant.model_dump(mode="json"),
                "selected_variant": finalist_variant.model_dump(mode="json"),
                "baseline_scorecard": baseline_scorecard.model_dump(mode="json"),
                "candidate_scorecards": benchmark_runtime["state_patch"]["candidate_scorecards"],
                "benchmark_scorecard": benchmark_runtime["state_patch"]["benchmark_scorecard"],
                "iteration_index": max(1, session.current_iteration or 1),
            },
        )
        finalist_review = ReviewDecision.model_validate(review_runtime["state_patch"]["review_decision"])
        converge_runtime = self._invoke_worker_action(
            action_id="approval_convergence.converge_approval",
            workflow_id="approval_convergence",
            workflow_step_id="converge_approval",
            request=workflow_request,
            state={
                "baseline_variant": baseline_variant.model_dump(mode="json"),
                "selected_variant": finalist_variant.model_dump(mode="json"),
                "baseline_scorecard": baseline_scorecard.model_dump(mode="json"),
                "recommendation_bundle": recommendation.model_dump(mode="json"),
                "candidate_scorecards": benchmark_runtime["state_patch"]["candidate_scorecards"],
                "benchmark_scorecard": benchmark_runtime["state_patch"]["benchmark_scorecard"],
                "review_decisions": review_runtime["state_patch"]["review_decisions"],
                "review_decision": review_runtime["state_patch"]["review_decision"],
                "iteration_index": max(1, session.current_iteration or 1),
            },
        )
        result = ApprovalConvergenceResult.model_validate(
            converge_runtime["state_patch"].get("approval_convergence_result")
            or converge_runtime["outputs"].get("approval_activation_bundle")
        )
        result.metadata = {
            **dict(result.metadata),
            "project_root": str(self.project_root),
            "default_adapters": self.adapter_registry.defaults(),
            "session_root": str(self.store.session_dir(request.session_id)),
            "source": "python-worker-bridge",
            "request_hints": request_hints,
            "selected_adapters": {
                "data_source": request.data_source_adapter_id,
                "execution": request.execution_adapter_id,
            },
        }
        self.store.save_approval(result)
        updated_session = session.model_copy(
            update={"status": result.status, "recommendation_variant_id": result.recommended_variant_id}
        )
        self.store.save_session(updated_session)
        return result

    def _load_or_create_session(self, request: WorkflowRunRequest) -> ResearchSessionState:
        request_payload = request.model_dump(mode="json")
        session_id = self.store.resolve_session_id(request_payload)
        existing = self.store.load_session(session_id)
        if existing is not None:
            return existing
        workspace_dir = (
            Path(request.workspace_dir).expanduser().resolve()
            if request.workspace_dir
            else self.workspace_root
        )
        session = ResearchSessionState(
            session_id=session_id,
            workflow_id="autonomous_research",
            workspace_id=request.workspace_id,
            workspace_dir=str(workspace_dir),
            wallet=str(request.wallet or ""),
            chain=request.chain,
            skill_name=request.skill_name,
            objective=request.objective,
            baseline_variant_id="baseline",
            current_iteration=0,
            max_iterations=request.iteration_budget,
            status="running",
            active_parent_variant_id="baseline",
            metadata={
                "operator_hints": dict(request.operator_hints),
                "request_metadata": dict(request.metadata),
                "selected_adapters": {
                    "data_source": request.data_source_adapter_id,
                    "execution": request.execution_adapter_id,
                },
            },
        )
        return self.store.save_session(session)

    def _load_or_benchmark_baseline(
        self,
        session: ResearchSessionState,
        request: WorkflowRunRequest,
        baseline_variant: WorkflowVariant,
    ) -> BenchmarkScorecard:
        existing = next(
            (
                item
                for item in self.store.list_scorecards(session.session_id)
                if item.variant_id == baseline_variant.variant_id
            ),
            None,
        )
        if existing is not None:
            return existing
        benchmark_action = self.workflow_action("autonomous_research", "benchmark_baseline")
        return self.benchmark_executor.dispatch_action(
            benchmark_action,
            request=request,
            session=session,
            iteration_index=0,
            baseline_variant=baseline_variant,
            variants=[baseline_variant],
        )[0]

    def _resolve_parent_variant(self, session: ResearchSessionState, baseline_variant: WorkflowVariant) -> WorkflowVariant:
        if session.active_parent_variant_id:
            resolved = self.store.load_variant(session.session_id, session.active_parent_variant_id)
            if resolved is not None:
                return resolved
        return baseline_variant

    def _candidate_variants_for_session(self, session_id: str) -> list[WorkflowVariant]:
        return [item for item in self.store.list_variants(session_id) if item.variant_id != "baseline"]


def build_python_compat_runner(
    *,
    project_root: Path,
    workspace_root: Path,
    plugin_registry: WorkflowPluginRegistry,
    adapter_registry: AdapterRegistry,
    distillation_service: Any | None = None,
    reflection_service: Any | None = None,
    workflow_data_source_adapter_id: str | None = None,
) -> PythonCompatWorkflowRunner:
    resolved_distillation_handler: DistillationWorkerHandler | None = None
    resolved_distillation_service = distillation_service
    if resolved_distillation_service is None and workflow_data_source_adapter_id:
        resolved_distillation_service = build_wallet_style_distillation_service(
            project_root=project_root,
            workspace_root=workspace_root,
            reflection_service=reflection_service,
            adapter_registry=adapter_registry,
            data_source_adapter_id=workflow_data_source_adapter_id,
            require_explicit_data_source_adapter=True,
            allow_builtin_adapter_registry_fallback=False,
        )
    if resolved_distillation_service is not None:
        resolved_distillation_handler = build_distillation_worker_handler(resolved_distillation_service)
    store = ResearchLoopStore(workspace_root)
    compiler = build_skill_package_compiler(project_root, workspace_root)
    worker_runtime = build_worker_runtime(
        project_root=project_root,
        workspace_root=workspace_root,
        plugin_registry=plugin_registry,
        adapter_registry=adapter_registry,
        distillation_service=resolved_distillation_service,
    )
    return PythonCompatWorkflowRunner(
        project_root=project_root,
        workspace_root=workspace_root,
        plugin_registry=plugin_registry,
        adapter_registry=adapter_registry,
        distillation_worker_handler=resolved_distillation_handler,
        skill_creation_executor=SkillCreationPluginExecutor(compiler, store),
        autoresearch_executor=AutoresearchPluginExecutor(plugin_registry, store),
        benchmark_executor=BenchmarkPluginExecutor(plugin_registry, adapter_registry, compiler, store),
        review_executor=ReviewPluginExecutor(plugin_registry, store),
        worker_runtime=worker_runtime,
        reflection_service=reflection_service,
        workflow_data_source_adapter_id=workflow_data_source_adapter_id,
        store=store,
    )
