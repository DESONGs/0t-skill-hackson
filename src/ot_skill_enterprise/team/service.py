from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from ot_skill_enterprise.agents.models import AgentAdapter
from ot_skill_enterprise.registry import build_evolution_registry
from ot_skill_enterprise.service_locator import project_root
from ot_skill_enterprise.shared.contracts.common import utc_now

from .bridge import AgentTeamBridge
from .models import (
    OptimizationActivation,
    OptimizationDecision,
    OptimizationRecommendation,
    OptimizationRun,
    OptimizationScorecard,
    OptimizationSession,
    OptimizationVariant,
    WorkItem,
)
from .protocol import TeamProtocolBundle, load_team_protocol_bundle
from .store import TeamStateStore


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "item"


def _now_iso() -> str:
    return utc_now().isoformat()


def _latest_by_variant(decisions: list[OptimizationDecision]) -> dict[str, OptimizationDecision]:
    latest: dict[str, OptimizationDecision] = {}
    for decision in decisions:
        latest[decision.variant_id] = decision
    return latest


@dataclass(slots=True)
class AgentTeamService:
    project_root: Path
    workspace_root: Path
    protocol: TeamProtocolBundle
    store: TeamStateStore
    bridge: AgentTeamBridge = field(init=False)

    def __post_init__(self) -> None:
        self.bridge = AgentTeamBridge(store=self.store, protocol=self.protocol)

    def _registry(self):
        return build_evolution_registry(self.workspace_root / "evolution-registry")

    def _resolve_skill_subject(self, skill_ref: str) -> tuple[str, str | None]:
        value = skill_ref.strip()
        candidate_paths = [
            Path(value).expanduser(),
            self.project_root / "skills" / value,
        ]
        for path in candidate_paths:
            resolved = path.resolve() if path.exists() else None
            if resolved is not None:
                return resolved.name, str(resolved)
        return _slugify(value), None

    def _build_session_brief(self, session: OptimizationSession, workflow: Any, module: Any, skill_path: str | None) -> str:
        search_space = module.search_space_schema.get("allowed_fields") or workflow.search_space
        return "\n".join(
            [
                f"# Optimization Session `{session.session_id}`",
                "",
                f"- workspace_id: `{session.workspace_id}`",
                f"- workflow: `{session.workflow_id}`",
                f"- module: `{session.module_id}`",
                f"- adapter_family: `{session.adapter_family}`",
                f"- team_topology: `{session.team_topology}`",
                f"- subject_id: `{session.subject_id}`",
                f"- source_skill_path: `{skill_path or 'unresolved'}`",
                "",
                "## Objective",
                session.objective,
                "",
                "## Search Space",
                *[f"- {item}" for item in search_space],
                "",
                "## Hard Gates",
                *[f"- {item}" for item in session.hard_gates],
                "",
                "## Constraints",
                "```json",
                json.dumps(session.constraints, ensure_ascii=False, indent=2),
                "```",
            ]
        ).strip() + "\n"

    def _make_work_item(
        self,
        *,
        session_id: str,
        role_id: str,
        adapter_id: str,
        title: str,
        kind: str,
        depends_on: list[str] | None = None,
        input_refs: list[str] | None = None,
        status: str = "queued",
        metadata: dict[str, Any] | None = None,
    ) -> WorkItem:
        return WorkItem(
            work_item_id=f"work-{uuid4().hex[:12]}",
            session_id=session_id,
            role_id=role_id,
            title=title,
            kind=kind,
            status=status,
            adapter_id=adapter_id,
            depends_on=list(depends_on or []),
            input_refs=list(input_refs or []),
            metadata=dict(metadata or {}),
        )

    def _refresh_leaderboard(self, session_id: str) -> list[dict[str, Any]]:
        variants = self.store.list_variants(session_id)
        latest_decisions = _latest_by_variant(self.store.list_decisions(session_id))

        def rank(item: OptimizationVariant) -> tuple[float, float]:
            score = item.scorecard.primary_quality_score if item.scorecard and item.scorecard.primary_quality_score is not None else float("-inf")
            confidence = item.scorecard.confidence_vs_noise if item.scorecard and item.scorecard.confidence_vs_noise is not None else float("-inf")
            return (score, confidence)

        ordered = sorted(variants, key=rank, reverse=True)
        payload: list[dict[str, Any]] = []
        for variant in ordered:
            decision = latest_decisions.get(variant.variant_id)
            payload.append(
                {
                    "variant_id": variant.variant_id,
                    "title": variant.title,
                    "kind": variant.kind,
                    "status": variant.status,
                    "summary": variant.summary,
                    "primary_quality_score": variant.scorecard.primary_quality_score if variant.scorecard else None,
                    "confidence_vs_noise": variant.scorecard.confidence_vs_noise if variant.scorecard else None,
                    "hard_gates_passed": variant.scorecard.hard_gates_passed if variant.scorecard else None,
                    "decision": decision.decision if decision is not None else None,
                }
            )
        self.store.save_leaderboard(session_id, payload)
        return payload

    def _recompute_recommendation(self, session: OptimizationSession) -> OptimizationRecommendation | None:
        module = self.protocol.module(session.module_id)
        latest_decisions = _latest_by_variant(self.store.list_decisions(session.session_id))
        variants = {item.variant_id: item for item in self.store.list_variants(session.session_id)}
        candidates: list[tuple[OptimizationVariant, OptimizationDecision]] = []
        for variant_id, decision in latest_decisions.items():
            variant = variants.get(variant_id)
            if variant is None or variant.kind == "baseline":
                continue
            if decision.decision not in {"keep", "recommended"}:
                continue
            candidates.append((variant, decision))
        if not candidates:
            self.store.clear_recommendation(session.session_id)
            return None

        max_style_distance = float(module.decision_policy.get("max_style_distance", 0.35))
        min_confidence_vs_noise = float(module.decision_policy.get("min_confidence_vs_noise", 0.0))

        def sort_key(item: tuple[OptimizationVariant, OptimizationDecision]) -> tuple[float, float]:
            variant, _decision = item
            scorecard = variant.scorecard or OptimizationScorecard()
            primary = scorecard.primary_quality_score if scorecard.primary_quality_score is not None else float("-inf")
            confidence = scorecard.confidence_vs_noise if scorecard.confidence_vs_noise is not None else float("-inf")
            return (primary, confidence)

        variant, decision = sorted(candidates, key=sort_key, reverse=True)[0]
        scorecard = variant.scorecard or OptimizationScorecard()
        status = "recommended"
        summary = decision.summary
        if scorecard.hard_gates_passed is False:
            status = "review_required"
            summary = f"{variant.title} requires review because hard gates did not pass."
        if scorecard.hard_gates_passed is None or scorecard.primary_quality_score is None:
            status = "review_required"
            summary = f"{variant.title} requires review because benchmark evidence is incomplete."
        if scorecard.style_distance is not None and scorecard.style_distance > max_style_distance:
            status = "review_required"
            summary = f"{variant.title} requires review because style drift exceeded policy."
        if scorecard.confidence_vs_noise is not None and scorecard.confidence_vs_noise < min_confidence_vs_noise:
            status = "review_required"
            summary = f"{variant.title} requires review because confidence remains inside the noise band."

        recommendation = OptimizationRecommendation(
            recommendation_id=f"rec-{uuid4().hex[:12]}",
            session_id=session.session_id,
            workspace_id=session.workspace_id,
            variant_id=variant.variant_id,
            status=status,
            summary=summary,
            decision_ids=[decision.decision_id],
            scorecard=scorecard,
            metadata={"module_id": session.module_id},
        )
        self.store.save_recommendation(recommendation)
        session.status = status
        session.updated_at = utc_now()
        self.store.save_session(session)
        self.store.append_journal(
            session.session_id,
            {
                "timestamp": _now_iso(),
                "event": "recommendation.updated",
                "variant_id": variant.variant_id,
                "status": status,
                "summary": summary,
            },
        )
        return recommendation

    def doctor(self) -> dict[str, Any]:
        sessions = self.store.list_sessions()
        return {
            "status": "ready",
            "project_root": str(self.project_root),
            "workspace_root": str(self.workspace_root),
            "protocol_root": str(self.protocol.root),
            "workflow_count": len(self.protocol.workflows),
            "module_count": len(self.protocol.modules),
            "role_count": len(self.protocol.roles),
            "adapters": self.bridge.report_capabilities()["supported_adapters"],
            "session_count": len(sessions),
        }

    def start_session(
        self,
        workflow_id: str,
        *,
        workspace_id: str,
        skill_ref: str,
        adapter_family: str = "codex",
        objective: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        workflow = self.protocol.workflow(workflow_id)
        module = self.protocol.module(workflow.module_id)
        if adapter_family not in self.bridge.adapters:
            raise ValueError(f"unsupported adapter family: {adapter_family}")
        skill_slug, skill_path = self._resolve_skill_subject(skill_ref)
        resolved_session_id = session_id or f"team-{workflow_id}-{uuid4().hex[:8]}"
        session = OptimizationSession(
            session_id=resolved_session_id,
            workspace_id=workspace_id,
            workflow_id=workflow_id,
            module_id=workflow.module_id,
            status="running",
            adapter_family=adapter_family,
            team_topology=workflow.team_topology,
            subject_kind="skill",
            subject_id=skill_slug,
            source_skill_path=skill_path,
            baseline_variant_id=f"baseline-{skill_slug}",
            title=f"{workflow.title}: {skill_slug}",
            objective=objective or f"Optimize the generated skill `{skill_slug}` using the {workflow.module_id} workflow.",
            constraints={
                "optimization_scope": ["strategy_spec", "execution_intent", "risk_filters", "timing", "sizing", "pacing", "candidate_generation_thresholds"],
                "forbidden_layers": [
                    "ave_provider_layer",
                    "pi_runtime_internals",
                    "compile_validate_promote_contract",
                    "live_execution_pipeline",
                    "onchain_broadcast_behavior",
                ],
            },
            hard_gates=list(workflow.hard_gates),
            enabled_roles=list(workflow.roles),
            metadata={"protocol_entrypoint": str(self.protocol.entrypoint_path)},
        )
        self.store.save_session(session)
        brief = self._build_session_brief(session, workflow, module, skill_path)
        brief_path = self.store.save_brief(session.session_id, brief)

        baseline_variant = OptimizationVariant(
            variant_id=session.baseline_variant_id or f"baseline-{skill_slug}",
            session_id=session.session_id,
            workspace_id=workspace_id,
            module_id=workflow.module_id,
            subject_id=skill_slug,
            title=f"Baseline {skill_slug}",
            summary="Original promoted skill used as the optimization baseline.",
            kind="baseline",
            source_skill_ref=skill_path,
            created_by_role="system",
            lineage={"source_skill_ref": skill_path, "lineage_mode": "baseline"},
            status="baseline",
            metadata={"workspace_id": workspace_id},
        )
        self.store.save_variant(baseline_variant)

        planner_item = self._make_work_item(
            session_id=session.session_id,
            role_id="planner",
            adapter_id=adapter_family,
            title=f"Plan session {session.session_id}",
            kind="plan_session",
            input_refs=[brief_path],
        )
        optimizer_item = self._make_work_item(
            session_id=session.session_id,
            role_id="optimizer",
            adapter_id=adapter_family,
            title=f"Generate variants for {skill_slug}",
            kind="generate_variant",
            depends_on=[planner_item.work_item_id],
            input_refs=[brief_path],
            status="blocked",
        )
        for item in (planner_item, optimizer_item):
            self.store.save_work_item(item)

        registry = self._registry()
        registry.record_agent(
            AgentAdapter(
                agent_id=adapter_family,
                display_name=self.bridge.adapters[adapter_family].display_name,
                capabilities=[],
                metadata={"kind": "team-adapter", "session_id": session.session_id},
            )
        )

        self.store.append_journal(
            session.session_id,
            {
                "timestamp": _now_iso(),
                "event": "session.started",
                "session_id": session.session_id,
                "workflow_id": workflow_id,
                "module_id": workflow.module_id,
                "adapter_family": adapter_family,
                "subject_id": skill_slug,
            },
        )
        self.store.append_journal(
            session.session_id,
            {
                "timestamp": _now_iso(),
                "event": "variant.baseline",
                "variant_id": baseline_variant.variant_id,
                "subject_id": skill_slug,
            },
        )
        leaderboard = self._refresh_leaderboard(session.session_id)
        return {
            "session": session.model_dump(mode="json"),
            "baseline_variant": baseline_variant.model_dump(mode="json"),
            "work_items": [planner_item.model_dump(mode="json"), optimizer_item.model_dump(mode="json")],
            "leaderboard": leaderboard,
            "next_steps": [
                f"ot-team handoff --session-id {session.session_id} --role planner",
                f"ot-team submit-work --session-id {session.session_id} --work-item-id {planner_item.work_item_id} --payload-file <planner.json> --agent-id {adapter_family}/planner-1",
            ],
        }

    def _resolve_work_item(self, session_id: str, work_item_id: str | None = None, role_id: str | None = None) -> WorkItem:
        if work_item_id:
            item = self.store.get_work_item(session_id, work_item_id)
            if item is None:
                raise ValueError(f"unknown work item: {work_item_id}")
            return item
        if not role_id:
            raise ValueError("provide --work-item-id or --role")
        matching = [item for item in self.store.list_work_items(session_id) if item.role_id == role_id]
        for preferred_status in ("in_progress", "queued", "blocked"):
            for item in matching:
                if item.status == preferred_status:
                    return item
        raise ValueError(f"no pending work item for role: {role_id}")

    def handoff(self, session_id: str, *, role_id: str, adapter_family: str | None = None) -> dict[str, Any]:
        session = self.store.get_session(session_id)
        if session is None:
            raise ValueError(f"unknown session: {session_id}")
        item = self._resolve_work_item(session_id, role_id=role_id)
        if item.status == "blocked":
            raise ValueError(f"work item {item.work_item_id} is still blocked on dependencies")
        return self.bridge.start_agent_session(session, item, adapter_id=adapter_family or item.adapter_id)

    def submit_work(
        self,
        session_id: str,
        *,
        payload: dict[str, Any],
        work_item_id: str | None = None,
        role_id: str | None = None,
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        session = self.store.get_session(session_id)
        if session is None:
            raise ValueError(f"unknown session: {session_id}")
        item = self._resolve_work_item(session_id, work_item_id=work_item_id, role_id=role_id)
        item.status = "completed"
        item.updated_at = utc_now()
        item.result_path = self.store.save_work_item_result(session_id, item.work_item_id, payload)
        self.store.save_work_item(item)

        created: dict[str, Any] = {"work_item": item.model_dump(mode="json")}

        if item.role_id == "planner":
            if isinstance(payload.get("constraints"), dict):
                session.constraints.update(payload["constraints"])
            planned_hard_gates = payload.get("hard_gates")
            if isinstance(planned_hard_gates, list) and planned_hard_gates:
                session.hard_gates = [str(entry) for entry in planned_hard_gates if str(entry).strip()]
            session.updated_at = utc_now()
            self.store.save_session(session)
            for blocked in self.store.list_work_items(session_id):
                if blocked.status == "blocked" and item.work_item_id in blocked.depends_on:
                    blocked.status = "queued"
                    blocked.updated_at = utc_now()
                    self.store.save_work_item(blocked)
            self.store.append_journal(
                session_id,
                {
                    "timestamp": _now_iso(),
                    "event": "planner.completed",
                    "work_item_id": item.work_item_id,
                    "agent_id": agent_id,
                    "summary": payload.get("summary") or "Planner updated session constraints.",
                },
            )

        elif item.role_id == "optimizer":
            variants = payload.get("variants")
            if isinstance(payload.get("variant"), dict) and not variants:
                variants = [payload["variant"]]
            if not isinstance(variants, list) or not variants:
                raise ValueError("optimizer payload must include variants")
            created_variants: list[dict[str, Any]] = []
            created_work_items: list[dict[str, Any]] = []
            for raw in variants:
                if not isinstance(raw, dict):
                    continue
                variant = OptimizationVariant(
                    variant_id=str(raw.get("variant_id") or f"variant-{uuid4().hex[:10]}"),
                    session_id=session_id,
                    workspace_id=session.workspace_id,
                    module_id=session.module_id,
                    subject_id=session.subject_id,
                    title=str(raw.get("title") or f"Variant {_slugify(session.subject_id)}"),
                    summary=str(raw.get("summary") or "Optimizer proposed a structured skill variant."),
                    kind=str(raw.get("kind") or "proposal"),
                    parent_variant_id=raw.get("parent_variant_id") or session.baseline_variant_id,
                    source_skill_ref=session.source_skill_path,
                    strategy_patch=dict(raw.get("strategy_patch") or {}),
                    execution_patch=dict(raw.get("execution_patch") or {}),
                    review_patch=dict(raw.get("review_patch") or {}),
                    created_by_role="optimizer",
                    created_by_agent_id=agent_id,
                    lineage={"parent_variant_id": raw.get("parent_variant_id") or session.baseline_variant_id},
                    status="proposed",
                    metadata=dict(raw.get("metadata") or {}),
                )
                self.store.save_variant(variant)
                created_variants.append(variant.model_dump(mode="json"))
                benchmark_item = self._make_work_item(
                    session_id=session_id,
                    role_id="benchmark-runner",
                    adapter_id=item.adapter_id,
                    title=f"Benchmark {variant.title}",
                    kind="run_benchmark",
                    input_refs=[str(self.store.variants_dir(session_id) / f"{variant.variant_id}.json")],
                    metadata={"variant_id": variant.variant_id},
                )
                reviewer_item = self._make_work_item(
                    session_id=session_id,
                    role_id="reviewer",
                    adapter_id=item.adapter_id,
                    title=f"Review {variant.title}",
                    kind="review_variant",
                    depends_on=[benchmark_item.work_item_id],
                    input_refs=[str(self.store.variants_dir(session_id) / f"{variant.variant_id}.json")],
                    status="blocked",
                    metadata={"variant_id": variant.variant_id},
                )
                self.store.save_work_item(benchmark_item)
                self.store.save_work_item(reviewer_item)
                created_work_items.extend([benchmark_item.model_dump(mode="json"), reviewer_item.model_dump(mode="json")])
                self.store.append_journal(
                    session_id,
                    {
                        "timestamp": _now_iso(),
                        "event": "variant.created",
                        "variant_id": variant.variant_id,
                        "title": variant.title,
                        "agent_id": agent_id,
                    },
                )
            created["variants"] = created_variants
            created["spawned_work_items"] = created_work_items

        elif item.role_id == "benchmark-runner":
            variant_id = str(payload.get("variant_id") or item.metadata.get("variant_id") or "")
            variant = self.store.get_variant(session_id, variant_id)
            if variant is None:
                raise ValueError(f"unknown variant for benchmark: {variant_id}")
            scorecard = OptimizationScorecard.model_validate(dict(payload.get("scorecard") or {}))
            run = OptimizationRun(
                run_id=f"run-{uuid4().hex[:12]}",
                session_id=session_id,
                variant_id=variant.variant_id,
                runner_id="benchmark-runner",
                status=str(payload.get("status") or "completed"),
                summary=str(payload.get("summary") or f"Benchmark completed for {variant.title}"),
                benchmark_profile=str(payload.get("benchmark_profile") or "autoresearch-default"),
                gate_profile=str(payload.get("gate_profile") or "autoresearch-hard-gates"),
                hard_gates_passed=scorecard.hard_gates_passed,
                metrics=dict(payload.get("metrics") or {}),
                artifacts=[str(entry) for entry in payload.get("artifacts") or []],
                metadata={"agent_id": agent_id} | dict(payload.get("metadata") or {}),
            )
            self.store.save_run(run)
            variant.scorecard = scorecard
            variant.status = "benchmarked"
            variant.updated_at = utc_now()
            self.store.save_variant(variant)
            for blocked in self.store.list_work_items(session_id):
                if blocked.status == "blocked" and item.work_item_id in blocked.depends_on:
                    blocked.status = "queued"
                    blocked.updated_at = utc_now()
                    self.store.save_work_item(blocked)
            created["run"] = run.model_dump(mode="json")
            created["variant"] = variant.model_dump(mode="json")
            self.store.append_journal(
                session_id,
                {
                    "timestamp": _now_iso(),
                    "event": "benchmark.completed",
                    "variant_id": variant.variant_id,
                    "run_id": run.run_id,
                    "hard_gates_passed": scorecard.hard_gates_passed,
                },
            )

        elif item.role_id == "reviewer":
            variant_id = str(payload.get("variant_id") or item.metadata.get("variant_id") or "")
            variant = self.store.get_variant(session_id, variant_id)
            if variant is None:
                raise ValueError(f"unknown variant for review: {variant_id}")
            decision = OptimizationDecision(
                decision_id=f"decision-{uuid4().hex[:12]}",
                session_id=session_id,
                variant_id=variant_id,
                role_id="reviewer",
                decision=str(payload.get("decision") or "review_required"),
                summary=str(payload.get("summary") or f"Reviewer updated {variant.title}."),
                rationale=payload.get("rationale"),
                reviewer_confidence=payload.get("reviewer_confidence"),
                created_by_agent_id=agent_id,
                metadata=dict(payload.get("metadata") or {}),
            )
            self.store.save_decision(decision)
            variant.status = "reviewed"
            variant.updated_at = utc_now()
            self.store.save_variant(variant)
            recommendation = self._recompute_recommendation(session)
            created["decision"] = decision.model_dump(mode="json")
            created["recommendation"] = recommendation.model_dump(mode="json") if recommendation is not None else None
            self.store.append_journal(
                session_id,
                {
                    "timestamp": _now_iso(),
                    "event": "review.completed",
                    "variant_id": variant_id,
                    "decision": decision.decision,
                    "agent_id": agent_id,
                },
            )

        self._refresh_leaderboard(session_id)
        return created

    def status(self, session_id: str) -> dict[str, Any]:
        session = self.store.get_session(session_id)
        if session is None:
            raise ValueError(f"unknown session: {session_id}")
        return {
            "session": session.model_dump(mode="json"),
            "work_items": [item.model_dump(mode="json") for item in self.store.list_work_items(session_id)],
            "leaderboard": self.store.load_leaderboard(session_id),
            "recommendation": self.store.get_recommendation(session_id).model_dump(mode="json") if self.store.get_recommendation(session_id) else None,
            "agent_sessions": [item.model_dump(mode="json") for item in self.store.list_agent_sessions(session_id)],
            "brief_path": str(self.store.brief_path(session_id)),
        }

    def leaderboard(self, session_id: str) -> dict[str, Any]:
        session = self.store.get_session(session_id)
        if session is None:
            raise ValueError(f"unknown session: {session_id}")
        payload = self._refresh_leaderboard(session_id)
        return {"session_id": session_id, "leaderboard": payload}

    def review(self, session_id: str) -> dict[str, Any]:
        session = self.store.get_session(session_id)
        if session is None:
            raise ValueError(f"unknown session: {session_id}")
        recommendation = self._recompute_recommendation(session)
        return {
            "session": session.model_dump(mode="json"),
            "recommendation": recommendation.model_dump(mode="json") if recommendation is not None else None,
            "leaderboard": self.store.load_leaderboard(session_id),
            "open_work_items": [item.model_dump(mode="json") for item in self.store.list_work_items(session_id) if item.status in {"queued", "blocked", "in_progress"}],
        }

    def approve(self, session_id: str, *, variant_id: str, approved_by: str = "human", activate: bool = False) -> dict[str, Any]:
        session = self.store.get_session(session_id)
        if session is None:
            raise ValueError(f"unknown session: {session_id}")
        variant = self.store.get_variant(session_id, variant_id)
        if variant is None:
            raise ValueError(f"unknown variant: {variant_id}")
        recommendation = self.store.get_recommendation(session_id)
        status = "activated" if activate else "approved"
        activation = OptimizationActivation(
            activation_id=f"activation-{uuid4().hex[:12]}",
            session_id=session_id,
            workspace_id=session.workspace_id,
            variant_id=variant_id,
            status=status,
            approved_by=approved_by,
            activated_by=approved_by if activate else None,
            metadata={"module_id": session.module_id},
        )
        self.store.save_activation(activation)
        if recommendation is not None:
            recommendation.status = status
            recommendation.updated_at = utc_now()
            self.store.save_recommendation(recommendation)
        session.status = status
        session.updated_at = utc_now()
        self.store.save_session(session)
        self.store.append_journal(
            session_id,
            {
                "timestamp": _now_iso(),
                "event": f"session.{status}",
                "variant_id": variant_id,
                "approved_by": approved_by,
            },
        )
        return {
            "session": session.model_dump(mode="json"),
            "activation": activation.model_dump(mode="json"),
            "recommendation": recommendation.model_dump(mode="json") if recommendation is not None else None,
        }

    def archive(self, session_id: str) -> dict[str, Any]:
        session = self.store.get_session(session_id)
        if session is None:
            raise ValueError(f"unknown session: {session_id}")
        session.status = "archived"
        session.updated_at = utc_now()
        self.store.save_session(session)
        self.store.append_journal(session_id, {"timestamp": _now_iso(), "event": "session.archived"})
        return {"session": session.model_dump(mode="json")}


def build_agent_team_service(
    *,
    project_root_path: Path | None = None,
    workspace_root: Path | str | None = None,
) -> AgentTeamService:
    root = Path(project_root_path).expanduser().resolve() if project_root_path is not None else project_root()
    workspace = Path(workspace_root).expanduser().resolve() if workspace_root is not None else (root / ".ot-workspace").resolve()
    protocol = load_team_protocol_bundle(root)
    store = TeamStateStore(workspace)
    return AgentTeamService(project_root=root, workspace_root=workspace, protocol=protocol, store=store)
