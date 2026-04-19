import fs from "node:fs/promises";
import path from "node:path";
import {
	ApprovalRecord,
	HandoffCheckpoint,
	JsonObject,
	Payload,
	TeamAgentSession,
	TeamProjection,
	TeamProjectionWorkItem,
	WorkflowAction,
	WorkflowArtifact,
	WorkflowKernelResult,
	WorkflowSessionState,
	WorkflowSpec,
	WorkflowStatus,
	WorkflowStep,
	WorkflowWorkItem,
} from "./ot_workflow_kernel_types.js";
import { findWorkflowStep, iterationScopeSteps, loadWorkflowProtocolBundle, workflowScopeSteps } from "./ot_workflow_protocol.js";
import {
	appendJsonLine,
	defaultApprovalState,
	defaultRecommendationState,
	loadSession,
	makeCheckpoint,
	nowIso,
	persistCheckpoint,
	persistSession,
	readJson,
	writeJson,
} from "./ot_workflow_store.js";
import { invokePythonWorkerBridge } from "./ot_workflow_bridge.js";

function runtimeEvent(type: string, message: string, extra: Record<string, unknown> = {}) {
	return { type, message, ...extra };
}

function ensureObject(value: unknown): JsonObject {
	if (value && typeof value === "object" && !Array.isArray(value)) return { ...(value as JsonObject) };
	return {};
}

function actionFromPayload(payload: Payload): WorkflowAction {
	const metadata = ensureObject(payload.metadata);
	const inputPayload = ensureObject(payload.input_payload);
	const workflowSession = ensureObject(inputPayload["workflow_session"]);
	const action = String(
		workflowSession["action"] || metadata["workflow_action"] || inputPayload["workflow_action"] || "run",
	)
		.trim()
		.toLowerCase();
	if (["run", "resume", "replay", "handoff", "approve", "reject", "activate", "archive"].includes(action)) {
		return action as WorkflowAction;
	}
	return "run";
}

function itemId(stepId: string, iteration: number | null) {
	return iteration == null ? `workflow:${stepId}` : `iteration:${iteration}:${stepId}`;
}

function stepLoopScope(step: WorkflowStep) {
	return step.loop_scope || "workflow";
}

function roleForStep(step: WorkflowStep) {
	switch (step.action_id) {
		case "benchmark.score_baseline":
		case "benchmark.score_candidates":
			return "benchmark-runner";
		case "review.evaluate_candidates":
			return "reviewer";
		case "autoresearch.plan_iteration":
		case "autoresearch.decide_iteration":
			return "planner";
		case "skill_creation.materialize_baseline":
		case "skill_creation.create_variants":
		case "distillation.execute":
			return "optimizer";
		default:
			return step.stage === "review" ? "reviewer" : step.stage === "benchmark" ? "benchmark-runner" : "optimizer";
	}
}

function kindForStep(step: WorkflowStep) {
	return `${step.plugin_id}:${step.stage}`;
}

function inputRefsForStep(workflow: WorkflowSpec, step: WorkflowStep) {
	const dependencyOutputs = (step.depends_on || []).flatMap((dependencyId) => {
		const dependency = findWorkflowStep(workflow, dependencyId);
		return (dependency?.outputs || []).map((output) => `state.${output}`);
	});
	const refs = (() => {
		switch (step.action_id) {
		case "skill_creation.materialize_baseline":
			return ["state.baseline_variant", ...dependencyOutputs];
		case "autoresearch.plan_iteration":
			return ["state.baseline_variant", "state.parent_variant", "state.selected_variant", ...dependencyOutputs];
		case "skill_creation.create_variants":
			return ["state.variant_plans", "state.planned_candidate_variants", "state.parent_variant", ...dependencyOutputs];
		case "benchmark.score_baseline":
			return ["state.baseline_variant", ...dependencyOutputs];
		case "benchmark.score_candidates":
			return ["state.baseline_variant", "state.candidate_variants", ...dependencyOutputs];
		case "review.evaluate_candidates":
			return ["state.baseline_scorecard", "state.candidate_scorecards", ...dependencyOutputs];
		case "autoresearch.decide_iteration":
			return [
				"state.baseline_variant",
				"state.baseline_scorecard",
				"state.candidate_variants",
				"state.candidate_scorecards",
				"state.review_decisions",
				...dependencyOutputs,
			];
		default:
			return dependencyOutputs;
		}
	})();
	return [...new Set(refs)];
}

function defaultTeamProjection(adapterFamily: string): TeamProjection {
	return {
		adapter_family: adapterFamily,
		work_items: [],
		agent_sessions: [],
		recommendation_state: defaultRecommendationState(),
		approval_records: [],
		updated_at: nowIso(),
	};
}

function isStepSatisfied(session: WorkflowSessionState, step: WorkflowStep) {
	if (!step.skip_if_session_has?.length) return false;
	return step.skip_if_session_has.every((key) => session.state[key] !== undefined && session.state[key] !== null);
}

function dependencyItemId(workflow: WorkflowSpec, dependencyId: string, iteration: number | null) {
	const dependency = findWorkflowStep(workflow, dependencyId);
	if (!dependency) return itemId(dependencyId, null);
	return stepLoopScope(dependency) === "iteration" ? itemId(dependencyId, iteration) : itemId(dependencyId, null);
}

function computeRecommendation(session: WorkflowSessionState) {
	const workerRecommendation = ensureObject(session.state["recommendation_bundle"]);
	if (Object.keys(workerRecommendation).length > 0) {
		session.recommendation = {
			status: String(workerRecommendation["status"] || "review_required"),
			summary: String(workerRecommendation["summary"] || "Worker recommendation available."),
			recommended_variant_id: String(workerRecommendation["recommended_variant_id"] || "") || null,
			selected_variant: ensureObject(workerRecommendation["selected_variant"]) || null,
			leaderboard: Array.isArray(workerRecommendation["leaderboard"])
				? (workerRecommendation["leaderboard"] as JsonObject[])
				: [],
			source: "worker",
			updated_at: nowIso(),
		};
		return;
	}
	const reviews = Array.isArray(session.state["review_decisions"]) ? (session.state["review_decisions"] as JsonObject[]) : [];
	const scores = Array.isArray(session.state["candidate_scorecards"]) ? (session.state["candidate_scorecards"] as JsonObject[]) : [];
	if (!reviews.length) {
		session.recommendation = {
			...session.recommendation,
			status: "idle",
			summary: "No review decisions available yet.",
			updated_at: nowIso(),
		};
		return;
	}
	const scoreByVariant = new Map<string, JsonObject>(scores.map((item) => [String(item["variant_id"] || ""), item]));
	const ranked = [...reviews].sort((left, right) => {
		const leftStatus = String(left["status"] || "");
		const rightStatus = String(right["status"] || "");
		const leftRank = leftStatus === "recommended" ? 3 : leftStatus === "keep" ? 2 : leftStatus === "review_required" ? 1 : 0;
		const rightRank = rightStatus === "recommended" ? 3 : rightStatus === "keep" ? 2 : rightStatus === "review_required" ? 1 : 0;
		if (leftRank !== rightRank) return rightRank - leftRank;
		const leftScore = Number(scoreByVariant.get(String(left["variant_id"] || ""))?.["primary_quality_score"] || 0);
		const rightScore = Number(scoreByVariant.get(String(right["variant_id"] || ""))?.["primary_quality_score"] || 0);
		return rightScore - leftScore;
	});
	const selected = ranked[0];
	session.recommendation = {
		status: String(selected["status"] || "review_required"),
		summary: String(selected["reasoning"] || selected["summary"] || "Kernel-derived recommendation."),
		recommended_variant_id: String(selected["variant_id"] || "") || null,
		selected_variant: null,
		leaderboard: ranked.map((item) => {
			const variantId = String(item["variant_id"] || "");
			const score = scoreByVariant.get(variantId) || {};
			return {
				variant_id: variantId,
				status: item["status"],
				primary_quality_score: score["primary_quality_score"] || 0,
				style_distance: score["style_distance"] || null,
				risk_penalty: score["risk_penalty"] || null,
			};
		}),
		source: "kernel",
		updated_at: nowIso(),
	};
}

function syncApprovalState(session: WorkflowSessionState) {
	const required = Boolean(session.workflow.human_review_required);
	if (!required) {
		session.approval.required = false;
		session.approval.status = "not_required";
		session.approval.updated_at = nowIso();
		return;
	}
	const approvalBundle = ensureObject(session.state["approval_activation_bundle"]);
	const approvalState = ensureObject(approvalBundle["approval"]);
	if (Object.keys(approvalState).length > 0) {
		session.approval.required = Boolean(approvalState["approval_required"]);
		session.approval.recommended_variant_id =
			String(approvalBundle["recommended_variant_id"] || approvalState["variant_id"] || "") ||
			session.recommendation.recommended_variant_id ||
			null;
		const bundleStatus = String(approvalState["status"] || approvalBundle["status"] || "");
		if (bundleStatus === "approved" || bundleStatus === "activated") {
			session.approval.status = bundleStatus;
		} else if (bundleStatus === "blocked") {
			session.approval.status = "rejected";
		} else if (bundleStatus === "review_required") {
			session.approval.status = "pending";
		}
		session.approval.updated_at = nowIso();
		if (session.approval.status !== "pending") return;
	}
	session.approval.required = true;
	session.approval.recommended_variant_id = session.recommendation.recommended_variant_id || null;
	if (["approved", "activated", "rejected"].includes(session.approval.status)) {
		session.approval.updated_at = nowIso();
		return;
	}
	if (["recommended", "keep", "review_required"].includes(session.recommendation.status)) {
		session.approval.status = "pending";
		session.approval.updated_at = nowIso();
	}
}

function deriveSessionStatus(session: WorkflowSessionState): WorkflowStatus {
	if (session.work_items.some((item) => item.status === "failed")) return "failed";
	if (session.approval.required && session.approval.status === "pending" && session.work_items.every((item) => ["completed", "skipped"].includes(item.status))) {
		return "awaiting_approval";
	}
	if (session.approval.status === "approved") return "approved";
	if (session.approval.status === "activated") return "activated";
	if (session.approval.status === "rejected") return "rejected";
	if (session.status === "archived") return "archived";
	if (session.work_items.some((item) => item.status === "handoff_ready")) return "handoff_ready";
	if (session.work_items.every((item) => ["completed", "skipped"].includes(item.status))) return "completed";
	if (session.work_items.some((item) => item.status === "running")) return "running";
	return "planned";
}

function shouldAutoAdvanceToApprovalConvergence(session: WorkflowSessionState): boolean {
	if (session.workflow_id !== "autonomous_research") return false;
	if (session.work_items.some((item) => item.status === "handoff_ready")) return false;
	if (!session.recommendation.recommended_variant_id) return false;
	const approvalBundle = ensureObject(session.state["approval_activation_bundle"]);
	const approvalPayload = ensureObject(approvalBundle["approval"]);
	if (Object.keys(approvalPayload).length > 0) return false;
	if (Object.keys(approvalBundle).length > 0) return false;
	return true;
}

function touchTeamActionTimestamp(session: WorkflowSessionState, action: WorkflowAction) {
	if (action === "run") session.team.last_run_at = nowIso();
	if (action === "resume") session.team.last_resume_at = nowIso();
	if (action === "replay") session.team.last_replay_at = nowIso();
	if (action === "handoff") session.team.last_handoff_at = nowIso();
}

function syncTeamProjection(session: WorkflowSessionState) {
	const workItems: TeamProjectionWorkItem[] = session.work_items.map((item) => {
		const step = findWorkflowStep(session.workflow, item.step_id);
		return {
			id: item.work_item_id,
			role: step ? roleForStep(step) : "optimizer",
			title: item.title,
			kind: step ? kindForStep(step) : item.plugin_id,
			status: item.status,
			depends_on: [...item.depends_on],
			input_refs: step ? inputRefsForStep(session.workflow, step) : [],
			instructions_path: item.instructions_path || null,
			result_path: item.response_path || null,
			metadata: {
				step_id: item.step_id,
				action_id: item.action_id,
				plugin_id: item.plugin_id,
				stage: item.stage,
				iteration: item.iteration,
				loop_scope: item.loop_scope,
				launcher_id: item.launcher_id || null,
			},
			created_at: item.created_at,
			updated_at: item.updated_at,
			started_at: item.started_at || null,
			handoff_ready_at: item.handoff_ready_at || null,
			completed_at: item.completed_at || null,
			failed_at: item.failed_at || null,
		};
	});
	session.team.work_items = workItems;
	session.team.recommendation_state = session.recommendation;
	session.team.approval_records = [...session.approval.records];
	session.team.updated_at = nowIso();
}

function upsertAgentSession(session: WorkflowSessionState, agentSession: TeamAgentSession) {
	const existing = session.team.agent_sessions.find((item) => item.agent_session_id === agentSession.agent_session_id);
	if (existing) {
		Object.assign(existing, agentSession, { updated_at: nowIso() });
		return;
	}
	session.team.agent_sessions.push(agentSession);
}

function createWorkItem(step: WorkflowStep, workflow: WorkflowSpec, iteration: number | null): WorkflowWorkItem {
	const workItemId = itemId(step.step_id, iteration);
	const createdAt = nowIso();
	return {
		work_item_id: workItemId,
		step_id: step.step_id,
		title: step.title,
		action_id: step.action_id,
		plugin_id: step.plugin_id,
		stage: step.stage,
		iteration,
		loop_scope: stepLoopScope(step),
		status: "blocked",
		depends_on: (step.depends_on || []).map((dependencyId) => dependencyItemId(workflow, dependencyId, iteration)),
		blocked_by: [],
		allow_reentry: Boolean(step.allow_reentry),
		created_at: createdAt,
		started_at: null,
		handoff_ready_at: null,
		completed_at: null,
		failed_at: null,
		artifact_paths: [],
		updated_at: createdAt,
	};
}

function ensureWorkItems(session: WorkflowSessionState) {
	const desired: WorkflowWorkItem[] = [];
	for (const step of workflowScopeSteps(session.workflow)) {
		const existing = session.work_items.find((item) => item.work_item_id === itemId(step.step_id, null));
		desired.push(existing || createWorkItem(step, session.workflow, null));
	}
	const currentIteration = Math.max(
		1,
		Number(session.state["iteration_index"] || 1),
		session.iterations.length ? Number(session.iterations[session.iterations.length - 1]["iteration_index"] || 1) : 1,
	);
	if (session.workflow.iterative) {
		for (let iteration = 1; iteration <= currentIteration; iteration += 1) {
			for (const step of iterationScopeSteps(session.workflow)) {
				const existing = session.work_items.find((item) => item.work_item_id === itemId(step.step_id, iteration));
				desired.push(existing || createWorkItem(step, session.workflow, iteration));
			}
		}
	}
	const historical = session.work_items.filter(
		(item) => !desired.some((candidate) => candidate.work_item_id === item.work_item_id) && ["completed", "failed", "skipped"].includes(item.status),
	);
	session.work_items = [...historical, ...desired];
	for (const item of session.work_items) {
		if (["completed", "failed", "skipped"].includes(item.status)) continue;
		const blockedBy = item.depends_on.filter((dependencyId) => {
			const dependency = session.work_items.find((candidate) => candidate.work_item_id === dependencyId);
			return !dependency || !["completed", "skipped"].includes(dependency.status);
		});
		item.blocked_by = blockedBy;
		if (item.status === "running") {
			item.updated_at = nowIso();
			continue;
		}
		item.status = blockedBy.length > 0 ? "blocked" : item.status === "handoff_ready" ? "handoff_ready" : "ready";
		item.updated_at = nowIso();
		const step = findWorkflowStep(session.workflow, item.step_id);
		if (step && isStepSatisfied(session, step)) {
			item.status = "skipped";
			item.summary = "Satisfied by session state.";
			item.completed_at ||= nowIso();
		}
	}
}

function nextRunnableWorkItem(session: WorkflowSessionState) {
	return session.work_items.find((item) => item.status === "ready" || item.status === "handoff_ready");
}

function buildKernelArtifacts(session: WorkflowSessionState): WorkflowArtifact[] {
	const artifacts: WorkflowArtifact[] = [
		{
			artifact_id: `${session.session_id}-session`,
			kind: "workflow.kernel.session",
			uri: session.session_file,
			label: "Workflow kernel session",
			content_type: "application/json",
		},
		{
			artifact_id: `${session.session_id}-work-items`,
			kind: "workflow.kernel.work_items",
			uri: session.work_items_file,
			label: "Workflow kernel work items",
			content_type: "application/json",
		},
		{
			artifact_id: `${session.session_id}-recommendation`,
			kind: "workflow.kernel.recommendation",
			uri: session.recommendation_file,
			label: "Workflow kernel recommendation",
			content_type: "application/json",
		},
		{
			artifact_id: `${session.session_id}-approval`,
			kind: "workflow.kernel.approval",
			uri: session.approval_file,
			label: "Workflow kernel approval",
			content_type: "application/json",
		},
		{
			artifact_id: `${session.session_id}-team`,
			kind: "workflow.kernel.team",
			uri: session.team_file,
			label: "Workflow kernel team projection",
			content_type: "application/json",
		},
		{
			artifact_id: `${session.session_id}-result`,
			kind: "workflow.kernel.result",
			uri: session.result_path,
			label: "Workflow kernel result",
			content_type: "application/json",
		},
	];
	if (session.approval_convergence_file) {
		artifacts.push({
			artifact_id: `${session.session_id}-approval-convergence`,
			kind: "workflow.kernel.approval_convergence",
			uri: session.approval_convergence_file,
			label: "Workflow kernel approval convergence",
			content_type: "application/json",
		});
	}
	return artifacts;
}

function asArray(value: unknown) {
	return Array.isArray(value) ? value : [];
}

function asObjectArray(value: unknown) {
	return asArray(value).filter((item) => item && typeof item === "object" && !Array.isArray(item)) as JsonObject[];
}

function toContractArtifact(artifact: WorkflowArtifact): JsonObject {
	return {
		ref: {
			artifact_id: artifact.artifact_id,
			kind: artifact.kind,
			uri: artifact.uri,
			label: artifact.label,
			metadata: ensureObject(artifact.metadata),
		},
		payload: ensureObject(artifact.payload),
	};
}

function collectBusinessArtifacts(session: WorkflowSessionState): JsonObject[] {
	const artifacts: JsonObject[] = [];
	for (const artifact of buildKernelArtifacts(session)) artifacts.push(toContractArtifact(artifact));
	for (const item of [
		session.state["baseline_variant"],
		...asArray(session.state["candidate_variants"]),
		session.recommendation.selected_variant,
	]) {
		const source = ensureObject(item);
		for (const artifact of asObjectArray(source["artifacts"])) artifacts.push({ ...artifact });
	}
	for (const score of asObjectArray(session.state["candidate_scorecards"])) {
		for (const artifact of asObjectArray(score["artifacts"])) artifacts.push({ ...artifact });
	}
	for (const review of asObjectArray(session.state["review_decisions"])) {
		for (const artifact of asObjectArray(review["artifacts"])) artifacts.push({ ...artifact });
	}
	const recommendationBundle = ensureObject(session.state["recommendation_bundle"]);
	for (const artifact of asObjectArray(recommendationBundle["artifacts"])) artifacts.push({ ...artifact });
	return artifacts;
}

function deriveStopDecision(session: WorkflowSessionState) {
	const stateDecision = ensureObject(session.state["stop_decision"]);
	if (Object.keys(stateDecision).length > 0) return stateDecision;
	const lastIteration = session.iterations.length ? ensureObject(session.iterations[session.iterations.length - 1]["stop_decision"]) : {};
	if (Object.keys(lastIteration).length > 0) return lastIteration;
	if (session.recommendation.status === "recommended" || session.recommendation.status === "keep") {
		return {
			decision: "stop",
			reason: session.recommendation.summary || "recommendation ready",
			selected_variant_id: session.recommendation.recommended_variant_id || null,
			next_parent_variant_id: session.recommendation.recommended_variant_id || null,
			human_review_required: Boolean(session.approval.required),
			metadata: {},
		};
	}
	return {
		decision: "continue",
		reason: session.recommendation.summary || "continue iteration",
		selected_variant_id: session.recommendation.recommended_variant_id || null,
		next_parent_variant_id: session.recommendation.recommended_variant_id || null,
		human_review_required: Boolean(session.approval.required),
		metadata: {},
	};
}

function activeWorkflowId(session: WorkflowSessionState): string {
	return session.workflow.workflow_id || session.workflow_id;
}

function projectDistillationSeedResult(session: WorkflowSessionState): JsonObject {
	return {
		workflow_id: activeWorkflowId(session),
		session_id: session.session_id,
		workspace_id: session.workspace_id || null,
		baseline_variant: ensureObject(session.state["baseline_variant"]),
		candidate_variants: asObjectArray(session.state["candidate_variants"]),
		scorecards: [],
		review_decisions: [],
		iterations: asObjectArray(session.iterations),
		recommendation_bundle: null,
		artifacts: collectBusinessArtifacts(session),
		metadata: {
			kernel_mode: "workflow",
			session_file: session.session_file,
			team_file: session.team_file,
			result_path: session.result_path,
			session_status: session.status,
			team: session.team,
		},
	};
}

function projectAutonomousResearchResult(session: WorkflowSessionState): JsonObject {
	const baselineVariant = ensureObject(session.state["baseline_variant"]);
	const recommendationBundle = ensureObject(session.state["recommendation_bundle"]);
	const selectedVariant = ensureObject(
		recommendationBundle["selected_variant"] || session.recommendation.selected_variant || session.state["selected_variant"],
	);
	return {
		workflow_id: activeWorkflowId(session),
		baseline_variant_id: String(baselineVariant["variant_id"] || "baseline"),
		session_id: session.session_id,
		workspace_id: session.workspace_id || null,
		status: session.recommendation.status || "review_required",
		summary: session.recommendation.summary || "Kernel-derived recommendation.",
		recommended_variant_id:
			(recommendationBundle["recommended_variant_id"] as string | undefined) ||
			session.recommendation.recommended_variant_id ||
			null,
		iteration_count: Number(session.iterations.length || session.state["iteration_index"] || 0),
		selected_variant: Object.keys(selectedVariant).length > 0 ? selectedVariant : null,
		leaderboard:
			asObjectArray(recommendationBundle["leaderboard"]).length > 0
				? asObjectArray(recommendationBundle["leaderboard"])
				: session.recommendation.leaderboard,
		scorecards: asObjectArray(session.state["candidate_scorecards"]),
		review_decisions: asObjectArray(session.state["review_decisions"]),
		iterations: asObjectArray(session.iterations),
		stop_decision: deriveStopDecision(session),
		artifacts: collectBusinessArtifacts(session),
		metadata: {
			kernel_mode: "workflow",
			session_file: session.session_file,
			team_file: session.team_file,
			result_path: session.result_path,
			session_status: session.status,
			team: session.team,
			approval: session.approval,
		},
	};
}

function projectApprovalConvergenceResult(session: WorkflowSessionState): JsonObject {
	const workflowId = activeWorkflowId(session);
	const workerBundle = ensureObject(session.state["approval_activation_bundle"]);
	if (Object.keys(workerBundle).length > 0) {
		const approvalPayload = ensureObject(workerBundle["approval"]);
		const recommendedVariantId =
			String(workerBundle["recommended_variant_id"] || approvalPayload["variant_id"] || session.approval.recommended_variant_id || "") ||
			null;
		const approvalStatus =
			session.approval.status === "pending" || session.approval.status === "not_required"
				? "review_required"
				: session.approval.status === "rejected"
					? "blocked"
					: session.approval.status;
		return {
			...workerBundle,
			workflow_id: String(workerBundle["workflow_id"] || workflowId),
			session_id: String(workerBundle["session_id"] || session.session_id),
			workspace_id:
				workerBundle["workspace_id"] || String(session.request["workspace_id"] || "") || null,
			recommended_variant_id: recommendedVariantId,
			status: approvalStatus,
			approval: {
				...approvalPayload,
				variant_id: String(approvalPayload["variant_id"] || recommendedVariantId || "") || null,
				approval_required: Boolean(session.approval.required),
				approval_granted: ["approved", "activated"].includes(session.approval.status),
				activation_requested: session.approval.status === "activated",
				activation_allowed: Boolean(
					approvalPayload["activation_allowed"] || session.approval.status === "activated" || session.approval.status === "approved",
				),
				status: approvalStatus,
				metadata: {
					...ensureObject(approvalPayload["metadata"]),
					approval_records: session.approval.records,
					kernel_approval_status: session.approval.status,
				},
			},
			artifacts:
				asObjectArray(workerBundle["artifacts"]).length > 0
					? asObjectArray(workerBundle["artifacts"])
					: collectBusinessArtifacts(session),
			metadata: {
				...ensureObject(workerBundle["metadata"]),
				kernel_mode: "workflow",
				session_file: session.session_file,
				team_file: session.team_file,
				result_path: session.result_path,
				session_status: session.status,
				approval: session.approval,
			},
		};
	}
	const baselineVariant = ensureObject(session.state["baseline_variant"]);
	const recommendationBundle = ensureObject(session.state["recommendation_bundle"]);
	const reviewDecisions = asObjectArray(session.state["review_decisions"]);
	const selectedVariant = ensureObject(
		recommendationBundle["selected_variant"] || session.recommendation.selected_variant || session.state["selected_variant"],
	);
	const selectedVariantId =
		String(selectedVariant["variant_id"] || "") || session.recommendation.recommended_variant_id || null;
	const finalistScorecard =
		asObjectArray(session.state["candidate_scorecards"]).find(
			(item) => String(item["variant_id"] || "") === selectedVariantId,
		) || asObjectArray(session.state["candidate_scorecards"])[0] || null;
	const finalistReview =
		reviewDecisions.find((item) => String(item["variant_id"] || "") === selectedVariantId) || reviewDecisions[0] || null;
	const governance = finalistReview ? ensureObject(finalistReview["governance"]) : {};
	const approvalStatus =
		session.approval.status === "pending" || session.approval.status === "not_required"
			? "review_required"
			: session.approval.status === "rejected"
				? "blocked"
			: session.approval.status;
	const approvalRecord = {
		variant_id: selectedVariantId || "unknown",
		approval_required: Boolean(session.approval.required),
		approval_granted: ["approved", "activated"].includes(session.approval.status),
		activation_requested: session.approval.status === "activated",
		activation_allowed: Boolean(governance["activation_allowed"] || session.approval.status === "activated"),
		status: approvalStatus,
		rationale:
			String(governance["rationale"] || finalistReview?.["reasoning"] || session.recommendation.summary || "Approval review pending.") ||
			"Approval review pending.",
		artifacts: asObjectArray(governance["artifacts"]),
		metadata: {
			approval_records: session.approval.records,
			kernel_approval_status: session.approval.status,
		},
	};
	const nestedRecommendation =
		Object.keys(recommendationBundle).length > 0
			? recommendationBundle
			: {
				workflow_id: String(recommendationBundle["workflow_id"] || session.state["recommendation_workflow_id"] || "autonomous_research"),
				baseline_variant_id: String(baselineVariant["variant_id"] || "baseline"),
				session_id: session.session_id,
				status: session.recommendation.status || "review_required",
				summary: session.recommendation.summary || "Kernel-derived recommendation.",
				recommended_variant_id: session.recommendation.recommended_variant_id || null,
				selected_variant: Object.keys(selectedVariant).length > 0 ? selectedVariant : null,
				leaderboard: session.recommendation.leaderboard,
				scorecards: asObjectArray(session.state["candidate_scorecards"]),
				review_decisions: reviewDecisions,
				iterations: asObjectArray(session.iterations),
				stop_decision: deriveStopDecision(session),
				artifacts: collectBusinessArtifacts(session),
				metadata: {
					kernel_mode: "workflow",
				},
			};
	return {
		workflow_id: workflowId,
		session_id: session.session_id,
		workspace_id: String(session.request["workspace_id"] || "") || null,
		baseline_variant_id: String(baselineVariant["variant_id"] || "baseline"),
		recommended_variant_id: selectedVariantId,
		selected_variant: Object.keys(selectedVariant).length > 0 ? selectedVariant : null,
		recommendation_bundle: nestedRecommendation,
		benchmark_scorecard: finalistScorecard,
		review_decision: finalistReview,
		approval: approvalRecord,
		status: approvalStatus,
		summary: approvalRecord.rationale,
		artifacts: collectBusinessArtifacts(session),
		metadata: {
			kernel_mode: "workflow",
			session_file: session.session_file,
			team_file: session.team_file,
			result_path: session.result_path,
			session_status: session.status,
			approval: session.approval,
		},
	};
}

function buildFinalResult(session: WorkflowSessionState) {
	const workflowId = activeWorkflowId(session);
	if (workflowId === "distillation_seed") return projectDistillationSeedResult(session);
	if (workflowId === "autonomous_research") return projectAutonomousResearchResult(session);
	if (workflowId === "approval_convergence") return projectApprovalConvergenceResult(session);
	return {
		workflow_id: workflowId,
		session_id: session.session_id,
		status: session.status,
		team: session.team,
		recommendation: session.recommendation,
		approval: session.approval,
		checkpoints: session.checkpoints,
		state: session.state,
	};
}

async function persistFinalOutputs(session: WorkflowSessionState, finalResult: JsonObject) {
	await writeJson(session.result_path, { final_result: finalResult });
	if (session.approval_convergence_file && (session.workflow_id === "approval_convergence" || session.state["approval_convergence_result"])) {
		const approvalPayload =
			session.workflow_id === "approval_convergence"
				? finalResult
				: projectApprovalConvergenceResult(session);
		await writeJson(session.approval_convergence_file, approvalPayload);
	}
}

function adapterFamilyFromPayload(payload: Payload): string {
	const metadata = ensureObject(payload.metadata);
	const inputPayload = ensureObject(payload.input_payload);
	const workflowSession = ensureObject(inputPayload["workflow_session"]);
	const team = ensureObject(workflowSession["team"]);
	return String(team["adapter_family"] || metadata["adapter_family"] || metadata["team_adapter_family"] || "kernel").trim();
}

function createSessionFromPayload(
	payload: Payload,
	workflow: WorkflowSpec,
	cwd: string,
	workspaceDir: string,
	sessionWorkspace: string,
	runtimeId: string,
	action: WorkflowAction,
): WorkflowSessionState {
	const inputPayload = ensureObject(payload.input_payload);
	const workflowSession = ensureObject(inputPayload["workflow_session"]);
	const request = ensureObject(workflowSession["request"]);
	const sessionId = String(payload.session_id || workflowSession["session_id"] || `pi-workflow-${Date.now()}`);
	const kernelRoot = path.join(sessionWorkspace, "workflow-kernel");
	const createdAt = nowIso();
	return {
		session_id: sessionId,
		workflow_id: workflow.workflow_id,
		workspace_id: String(request["workspace_id"] || "") || null,
		status: "planned",
		runtime_id: runtimeId,
		workspace_dir: workspaceDir,
		cwd,
		action,
		request,
		workflow,
		state: {
			...ensureObject(workflowSession["inputs"]),
			iteration_index: Number(ensureObject(workflowSession["inputs"])["iteration_index"] || 1),
		},
		work_items: [],
		recommendation: defaultRecommendationState(),
		approval: defaultApprovalState(Boolean(workflow.human_review_required)),
		checkpoints: [],
		team: defaultTeamProjection(adapterFamilyFromPayload(payload)),
		iterations: [],
		journal_path: path.join(kernelRoot, "journal.jsonl"),
		session_file: path.join(kernelRoot, "session.json"),
		work_items_file: path.join(kernelRoot, "work-items.json"),
		recommendation_file: path.join(kernelRoot, "recommendation.json"),
		approval_file: path.join(kernelRoot, "approval.json"),
		approval_convergence_file: path.join(kernelRoot, "approval-convergence.json"),
		team_file: path.join(kernelRoot, "team.json"),
		result_path: path.join(kernelRoot, "result.json"),
		created_at: createdAt,
		updated_at: createdAt,
	};
}

function hydrateSession(session: WorkflowSessionState, payload: Payload, workflow: WorkflowSpec, action: WorkflowAction, sessionWorkspace: string) {
	const kernelRoot = path.join(sessionWorkspace, "workflow-kernel");
	const inputPayload = ensureObject(payload.input_payload);
	const workflowSession = ensureObject(inputPayload["workflow_session"]);
	const requestPatch = ensureObject(workflowSession["request"]);
	const payloadMetadata = ensureObject(payload.metadata);
	const requestMetadataPatch: JsonObject = {};
	for (const key of ["approval_granted", "activation_requested", "activate", "approved_by"]) {
		if (key in payloadMetadata) requestMetadataPatch[key] = payloadMetadata[key];
	}
	session.workflow_id = workflow.workflow_id;
	session.workflow = workflow;
	session.action = action;
	session.request = {
		...ensureObject(session.request),
		...requestPatch,
	};
	session.workspace_id ||= String(session.request["workspace_id"] || "") || null;
	if (Object.keys(requestMetadataPatch).length > 0) {
		session.request["metadata"] = {
			...ensureObject(session.request["metadata"]),
			...requestMetadataPatch,
		};
	}
	session.team = session.team || defaultTeamProjection(adapterFamilyFromPayload(payload));
	session.team.adapter_family ||= adapterFamilyFromPayload(payload);
	session.team.agent_sessions ||= [];
	session.team.approval_records ||= [];
	session.team.recommendation_state ||= defaultRecommendationState();
	session.team_file ||= path.join(kernelRoot, "team.json");
	session.approval_convergence_file ||= path.join(kernelRoot, "approval-convergence.json");
	session.created_at ||= nowIso();
	for (const item of session.work_items) {
		item.created_at ||= session.created_at;
		item.started_at ||= null;
		item.handoff_ready_at ||= null;
		item.completed_at ||= null;
		item.failed_at ||= null;
	}
}

async function appendJournalEvent(session: WorkflowSessionState, type: string, extra: JsonObject = {}) {
	await appendJsonLine(session.journal_path, {
		type,
		session_id: session.session_id,
		workflow_id: session.workflow_id,
		timestamp: nowIso(),
		...extra,
	});
}

async function checkpointForItem(session: WorkflowSessionState, workItem: WorkflowWorkItem, kernelRoot: string, kind: "handoff" | "failure" = "handoff") {
	const checkpoint = makeCheckpoint(session, kind, `${workItem.title} is ready for ${kind}.`, {
		work_item: workItem,
		iteration: workItem.iteration,
		kernelRoot,
	});
	await persistCheckpoint(session, checkpoint, {
		work_item_id: workItem.work_item_id,
		step_id: workItem.step_id,
		iteration: workItem.iteration,
	});
	return checkpoint;
}

async function writeHandoffInstructions(session: WorkflowSessionState, workItem: WorkflowWorkItem, kernelRoot: string) {
	const step = findWorkflowStep(session.workflow, workItem.step_id);
	const instructionsPath = path.join(kernelRoot, "handoffs", `${workItem.work_item_id}.md`);
	workItem.instructions_path = instructionsPath;
	const inputRefs = step ? inputRefsForStep(session.workflow, step) : [];
	const content = [
		`# Workflow Handoff: ${workItem.title}`,
		"",
		`- Session ID: ${session.session_id}`,
		`- Workflow ID: ${session.workflow_id}`,
		`- Work Item ID: ${workItem.work_item_id}`,
		`- Role: ${step ? roleForStep(step) : "optimizer"}`,
		`- Kind: ${step ? kindForStep(step) : workItem.plugin_id}`,
		`- Adapter Family: ${session.team.adapter_family}`,
		`- Status: ${workItem.status}`,
		`- Depends On: ${(workItem.depends_on || []).join(", ") || "(none)"}`,
		"",
		"## Input Refs",
		...inputRefs.map((ref) => `- ${ref}`),
		"",
		"## Result Submission",
		`- Response Path: ${workItem.response_path || "(set when executed)"}`,
		`- Checkpoint Path: ${path.join(kernelRoot, "checkpoints")}`,
	].join("\n");
	await writeJson(instructionsPath.replace(/\.md$/, ".json"), {
		session_id: session.session_id,
		workflow_id: session.workflow_id,
		work_item_id: workItem.work_item_id,
		role: step ? roleForStep(step) : "optimizer",
		input_refs: inputRefs,
		status: workItem.status,
	});
	await import("node:fs/promises").then((mod) => mod.mkdir(path.dirname(instructionsPath), { recursive: true }).then(() => mod.writeFile(instructionsPath, content, "utf8")));
	return instructionsPath;
}

async function loadExternalResponse(workItem: WorkflowWorkItem): Promise<JsonObject | null> {
	if (!workItem.response_path) return null;
	try {
		const payload = JSON.parse(await fs.readFile(workItem.response_path, "utf8")) as JsonObject;
		return ensureObject(payload);
	} catch {
		return null;
	}
}

async function executeWorkItem(
	session: WorkflowSessionState,
	workItem: WorkflowWorkItem,
	bridgeSpec: import("./ot_workflow_kernel_types.js").WorkerBridgeSpec,
	cwd: string,
	kernelRoot: string,
): Promise<{ ok: boolean; response?: JsonObject; error?: JsonObject }> {
	const step = findWorkflowStep(session.workflow, workItem.step_id);
	if (!step) {
		workItem.status = "failed";
		workItem.error = { code: "unknown_step", message: `Unknown workflow step ${workItem.step_id}` };
		return { ok: false, error: workItem.error };
	}
	const agentSessionId = `agent-${workItem.work_item_id}`;
	const finalizeFailure = async (errorPayload: JsonObject, summary?: string, launcherId?: string | null) => {
		workItem.status = "failed";
		workItem.failed_at = nowIso();
		workItem.error = ensureObject(errorPayload);
		workItem.summary = summary || String(errorPayload["message"] || "worker failed");
		upsertAgentSession(session, {
			agent_session_id: agentSessionId,
			adapter_family: session.team.adapter_family,
			role: roleForStep(step),
			work_item_id: workItem.work_item_id,
			status: "failed",
			opened_at: workItem.started_at || nowIso(),
			updated_at: nowIso(),
			instructions_path: workItem.instructions_path || null,
			result_path: workItem.response_path || null,
			metadata: {
				action_id: workItem.action_id,
				plugin_id: workItem.plugin_id,
				stage: workItem.stage,
				launcher_id: launcherId || null,
				error: workItem.error,
			},
		});
		await appendJournalEvent(session, "work_item_failed", {
			work_item_id: workItem.work_item_id,
			step_id: workItem.step_id,
			error: workItem.error,
		});
		return { ok: false, error: workItem.error };
	};
	const finalizeSuccess = async (response: JsonObject, launcherId: string | null) => {
		const statePatch = ensureObject(response["state_patch"]);
		Object.assign(session.state, statePatch);
		workItem.status = "completed";
		workItem.completed_at = nowIso();
		workItem.summary = String(response["status"] || "completed");
		workItem.updated_at = nowIso();
		session.last_step_id = workItem.step_id;
		upsertAgentSession(session, {
			agent_session_id: agentSessionId,
			adapter_family: session.team.adapter_family,
			role: roleForStep(step),
			work_item_id: workItem.work_item_id,
			status: "completed",
			opened_at: workItem.started_at || nowIso(),
			updated_at: nowIso(),
			instructions_path: workItem.instructions_path || null,
			result_path: workItem.response_path || null,
			metadata: {
				action_id: workItem.action_id,
				plugin_id: workItem.plugin_id,
				stage: workItem.stage,
				launcher_id: launcherId,
				external_submission: launcherId === "handoff-submission",
			},
		});
		await appendJournalEvent(session, "work_item_completed", {
			work_item_id: workItem.work_item_id,
			step_id: workItem.step_id,
			status: response["status"],
			control: ensureObject(response["control"]),
			external_submission: launcherId === "handoff-submission",
		});
		if (step.action_id === "autoresearch.finalize_iteration" || step.action_id === "autoresearch.decide_iteration") {
			const control = ensureObject(response["control"]);
			const shouldContinue = Boolean(control["should_continue"]);
			const iterationRecord = {
				iteration_index: workItem.iteration,
				status: shouldContinue ? "continue" : "stop",
				stop_reason: control["stop_reason"] || response["status"] || "completed",
				work_item_id: workItem.work_item_id,
			};
			session.iterations.push(iterationRecord);
			if (shouldContinue) {
				session.state["iteration_index"] = Number(session.state["iteration_index"] || 1) + 1;
			}
		}
		computeRecommendation(session);
		syncApprovalState(session);
		return { ok: true, response };
	};
	workItem.status = "running";
	workItem.started_at ||= nowIso();
	workItem.updated_at = nowIso();
	const requestPath = workItem.request_path || path.join(kernelRoot, "bridge", `${workItem.work_item_id}.request.json`);
	const responsePath = workItem.response_path || path.join(kernelRoot, "bridge", `${workItem.work_item_id}.response.json`);
	workItem.request_path = requestPath;
	workItem.response_path = responsePath;
	upsertAgentSession(session, {
		agent_session_id: agentSessionId,
		adapter_family: session.team.adapter_family,
		role: roleForStep(step),
		work_item_id: workItem.work_item_id,
		status: "running",
		opened_at: workItem.started_at || nowIso(),
		updated_at: nowIso(),
		instructions_path: workItem.instructions_path || null,
		result_path: workItem.response_path || null,
		metadata: {
			action_id: workItem.action_id,
			plugin_id: workItem.plugin_id,
			stage: workItem.stage,
			launcher_scope: "workflow-kernel",
		},
	});
	await appendJournalEvent(session, "work_item_started", {
		work_item_id: workItem.work_item_id,
		step_id: workItem.step_id,
		iteration: workItem.iteration,
	});
	const externalResponse = await loadExternalResponse(workItem);
	if (externalResponse) {
		if (externalResponse["ok"] === false) {
			return finalizeFailure(ensureObject(externalResponse["error"]), String(externalResponse["status"] || "submission failed"), "handoff-submission");
		}
		return finalizeSuccess(externalResponse, "handoff-submission");
	}
	try {
		const { response, launcherId } = await invokePythonWorkerBridge({
			cwd,
			bridge: bridgeSpec,
			session,
			step,
			workItem,
			requestPath,
			responsePath,
		});
		workItem.launcher_id = launcherId;
		if (!response.ok) {
			return finalizeFailure(ensureObject(response.error), String(response.error?.["message"] || response.status || "worker failed"), launcherId);
		}
		return finalizeSuccess(response as unknown as JsonObject, launcherId);
	} catch (error) {
		const message = error instanceof Error ? error.message : String(error);
		session.last_step_id = workItem.step_id;
		return finalizeFailure({ code: "worker_bridge_failed", message }, message, null);
	}
}

async function applyApprovalAction(session: WorkflowSessionState, action: WorkflowAction, actorId: string, kernelRoot: string) {
	const record: ApprovalRecord = {
		approval_id: `${action}-${Date.now()}`,
		action: action as "approve" | "reject" | "activate",
		status: action === "approve" ? "approved" : action === "reject" ? "rejected" : "activated",
		actor_id: actorId,
		summary: `${action} recorded in TS workflow kernel.`,
		timestamp: nowIso(),
		metadata: {},
	};
	session.approval.records.push(record);
	session.approval.last_action = action;
	session.approval.status = record.status;
	session.approval.updated_at = nowIso();
	const approvalBundle = ensureObject(session.state["approval_activation_bundle"]);
	const approvalPayload = ensureObject(approvalBundle["approval"]);
	if (Object.keys(approvalBundle).length > 0) {
		approvalBundle["recommended_variant_id"] =
			String(approvalBundle["recommended_variant_id"] || approvalPayload["variant_id"] || session.approval.recommended_variant_id || "") ||
			null;
		approvalBundle["status"] = record.status;
		approvalBundle["summary"] = record.summary;
		approvalBundle["session_id"] = session.session_id;
		approvalBundle["workflow_id"] = session.workflow_id;
		approvalBundle["workspace_id"] = session.workspace_id;
		approvalBundle["approval"] = {
			...approvalPayload,
			variant_id:
				String(approvalPayload["variant_id"] || approvalBundle["recommended_variant_id"] || session.approval.recommended_variant_id || "") ||
				null,
			approval_required: Boolean(session.approval.required),
			approval_granted: ["approved", "activated"].includes(record.status),
			activation_requested: record.status === "activated",
			activation_allowed: true,
			status: record.status,
			rationale: record.summary,
			metadata: {
				...ensureObject(approvalPayload["metadata"]),
				approval_records: session.approval.records,
				kernel_approval_status: session.approval.status,
			},
		};
		session.state["approval_activation_bundle"] = approvalBundle;
		session.state["approval_convergence_result"] = approvalBundle;
	}
	session.status = deriveSessionStatus(session);
	await appendJournalEvent(session, "approval_recorded", {
		action,
		actor_id: actorId,
		approval_id: record.approval_id,
	});
	const checkpoint = makeCheckpoint(session, "approval", record.summary, { kernelRoot });
	await persistCheckpoint(session, checkpoint, { action, actor_id: actorId });
}

export async function runWorkflowKernel(payload: Payload): Promise<WorkflowKernelResult> {
	const action = actionFromPayload(payload);
	const runtimeId = String(payload.runtime_id || "pi");
	const cwd = path.resolve(String(payload.cwd || process.cwd()));
	const workspaceDir = path.resolve(String(payload.workspace_dir || path.join(cwd, ".ot-workspace")));
	const inputPayload = ensureObject(payload.input_payload);
	const workflowSession = ensureObject(inputPayload["workflow_session"]);
	const workflowId = String(workflowSession["workflow_id"] || ensureObject(payload.metadata)["flow_id"] || "workflow");
	const sessionId = String(payload.session_id || workflowSession["session_id"] || `pi-workflow-${Date.now()}`);
	const sessionWorkspace = path.resolve(String(payload.session_workspace || path.join(workspaceDir, "runtime-sessions", sessionId)));
	const kernelRoot = path.join(sessionWorkspace, "workflow-kernel");
	const bundle = await loadWorkflowProtocolBundle(cwd);
	const workflow = bundle.workflows.get(workflowId);
	if (!workflow) throw new Error(`nextgen workflow protocol does not define workflow ${workflowId}`);
	const bridge = bundle.bridges.get(bundle.manifest.default_bridge_id);
	if (!bridge) throw new Error(`nextgen workflow protocol does not define bridge ${bundle.manifest.default_bridge_id}`);
	const existing = await loadSession(path.join(kernelRoot, "session.json"));
	const session = existing || createSessionFromPayload(payload, workflow, cwd, workspaceDir, sessionWorkspace, runtimeId, action);
	hydrateSession(session, payload, workflow, action, sessionWorkspace);
	touchTeamActionTimestamp(session, action);
	ensureWorkItems(session);
	computeRecommendation(session);
	syncApprovalState(session);
	syncTeamProjection(session);
	await persistSession(session);

	const events: JsonObject[] = [
		runtimeEvent("agent_start", "Pi workflow kernel session started", {
			status: "running",
			metadata: { session_id: session.session_id, workflow_id: session.workflow_id, action },
		}),
	];

	if (action === "replay") {
		const finalResult = buildFinalResult(session);
		const journal = await readJson<JsonObject[]>(session.journal_path).catch(async () => {
			const content = await import("node:fs/promises").then((mod) => mod.readFile(session.journal_path, "utf8").catch(() => ""));
			return content
				.split(/\r?\n/)
				.map((line) => line.trim())
				.filter(Boolean)
				.map((line) => JSON.parse(line) as JsonObject);
		});
		events.push(runtimeEvent("agent_end", "Pi workflow kernel replay finished", { status: "succeeded" }));
		return {
			ok: true,
			status: "succeeded",
			summary: `Replayed workflow ${session.workflow_id}.`,
			output: {
				kernel_mode: "workflow",
				action,
				session_id: session.session_id,
				workflow_id: session.workflow_id,
				session,
				team: session.team,
				journal,
			},
			final_result: finalResult,
			result_path: session.result_path,
			session_file: session.session_file,
			artifacts: buildKernelArtifacts(session),
			events,
			metadata: {
				kernel_mode: "workflow",
				action,
				session_id: session.session_id,
				workflow_id: session.workflow_id,
				session_file: session.session_file,
				work_items_file: session.work_items_file,
				recommendation_file: session.recommendation_file,
				approval_file: session.approval_file,
				protocol_manifest: bundle.manifestPath,
			},
			provider_ids: ["pi-kernel"],
			skill_ids: [],
		};
	}

	if (["approve", "reject", "activate"].includes(action)) {
		const actorId = String(ensureObject(payload.metadata)["actor_id"] || "human-approver");
		await applyApprovalAction(session, action, actorId, kernelRoot);
		syncTeamProjection(session);
		await persistSession(session);
		events.push(runtimeEvent("agent_end", "Pi workflow kernel approval action finished", { status: "succeeded" }));
		const finalResult = buildFinalResult(session);
		await persistFinalOutputs(session, finalResult);
		return {
			ok: true,
			status: "succeeded",
			summary: `Recorded ${action} for workflow ${session.workflow_id}.`,
			output: {
				kernel_mode: "workflow",
				action,
				session_id: session.session_id,
				workflow_id: session.workflow_id,
				session,
				team: session.team,
			},
			final_result: finalResult,
			result_path: session.result_path,
			session_file: session.session_file,
			artifacts: buildKernelArtifacts(session),
			events,
			metadata: {
				kernel_mode: "workflow",
				action,
				session_id: session.session_id,
				workflow_id: session.workflow_id,
				session_file: session.session_file,
				approval_file: session.approval_file,
				protocol_manifest: bundle.manifestPath,
			},
			provider_ids: ["pi-kernel"],
			skill_ids: [],
		};
	}

	if (action === "archive") {
		session.status = "archived";
		await appendJournalEvent(session, "session_archived", {});
		syncTeamProjection(session);
		await persistSession(session);
		await persistFinalOutputs(session, buildFinalResult(session));
		events.push(runtimeEvent("agent_end", "Pi workflow kernel archive action finished", { status: "succeeded" }));
		return {
			ok: true,
			status: "succeeded",
			summary: `Archived workflow ${session.workflow_id}.`,
			output: {
				kernel_mode: "workflow",
				action,
				session_id: session.session_id,
				workflow_id: session.workflow_id,
				session,
				team: session.team,
			},
			artifacts: buildKernelArtifacts(session),
			events,
			metadata: {
				kernel_mode: "workflow",
				action,
				session_id: session.session_id,
				workflow_id: session.workflow_id,
				session_file: session.session_file,
				protocol_manifest: bundle.manifestPath,
			},
			provider_ids: ["pi-kernel"],
			skill_ids: [],
		};
	}

	if (action === "handoff") {
		const readyItem = nextRunnableWorkItem(session);
		if (readyItem) {
			readyItem.status = "handoff_ready";
			readyItem.handoff_ready_at = nowIso();
			readyItem.request_path ||= path.join(kernelRoot, "bridge", `${readyItem.work_item_id}.request.json`);
			readyItem.response_path ||= path.join(kernelRoot, "bridge", `${readyItem.work_item_id}.response.json`);
			await fs.mkdir(path.dirname(readyItem.request_path), { recursive: true });
			await fs.mkdir(path.dirname(readyItem.response_path), { recursive: true });
			const instructionsPath = await writeHandoffInstructions(session, readyItem, kernelRoot);
			session.status = deriveSessionStatus(session);
			const checkpoint = await checkpointForItem(session, readyItem, kernelRoot, "handoff");
			upsertAgentSession(session, {
				agent_session_id: `handoff-${readyItem.work_item_id}`,
				adapter_family: session.team.adapter_family,
				role: roleForStep(findWorkflowStep(session.workflow, readyItem.step_id) || session.workflow.steps[0]),
				work_item_id: readyItem.work_item_id,
				status: "prepared",
				opened_at: readyItem.handoff_ready_at || nowIso(),
				updated_at: nowIso(),
				checkpoint_id: checkpoint.checkpoint_id,
				instructions_path: instructionsPath,
				result_path: readyItem.response_path || null,
				metadata: {
					action_id: readyItem.action_id,
					plugin_id: readyItem.plugin_id,
					stage: readyItem.stage,
				},
			});
			await appendJournalEvent(session, "handoff_ready", {
				work_item_id: readyItem.work_item_id,
				checkpoint_id: checkpoint.checkpoint_id,
			});
		}
		syncTeamProjection(session);
		await persistSession(session);
		events.push(runtimeEvent("agent_end", "Pi workflow kernel handoff prepared", { status: "succeeded" }));
		const finalResult = buildFinalResult(session);
		return {
			ok: true,
			status: "succeeded",
			summary: readyItem
				? `Prepared handoff checkpoint for ${readyItem.work_item_id}.`
				: `No runnable work item available for ${session.workflow_id}.`,
			output: {
				kernel_mode: "workflow",
				action,
				session_id: session.session_id,
				workflow_id: session.workflow_id,
				session,
				team: session.team,
				next_work_item: readyItem || null,
				checkpoint: session.checkpoints[session.checkpoints.length - 1] || null,
			},
			final_result: finalResult,
			result_path: session.result_path,
			session_file: session.session_file,
			artifacts: buildKernelArtifacts(session),
			events,
			metadata: {
				kernel_mode: "workflow",
				action,
				session_id: session.session_id,
				workflow_id: session.workflow_id,
				session_file: session.session_file,
				work_items_file: session.work_items_file,
				protocol_manifest: bundle.manifestPath,
			},
			provider_ids: ["pi-kernel"],
			skill_ids: [],
		};
	}

	let continueRunning = true;
	while (continueRunning) {
		ensureWorkItems(session);
		const readyItem = nextRunnableWorkItem(session);
		if (!readyItem) break;
		if (readyItem.status === "handoff_ready") {
			const externalResponse = await loadExternalResponse(readyItem);
			if (!externalResponse) break;
		}
		const result = await executeWorkItem(session, readyItem, bridge, cwd, kernelRoot);
		if (!result.ok) {
			session.status = deriveSessionStatus(session);
			const checkpoint = await checkpointForItem(session, readyItem, kernelRoot, "failure");
			await appendJournalEvent(session, "session_failed", {
				work_item_id: readyItem.work_item_id,
				checkpoint_id: checkpoint.checkpoint_id,
			});
			syncTeamProjection(session);
			await persistSession(session);
			const finalResult = buildFinalResult(session);
			await persistFinalOutputs(session, finalResult);
			events.push(
				runtimeEvent("error", readyItem.summary || "Workflow kernel step failed", { status: "failed", error: readyItem.error }),
				runtimeEvent("agent_end", "Pi workflow kernel session finished", { status: "failed" }),
			);
			return {
				ok: false,
				status: "failed",
				summary: readyItem.summary || "Workflow kernel step failed.",
				output: {
					kernel_mode: "workflow",
					action,
					session_id: session.session_id,
					workflow_id: session.workflow_id,
					session,
					team: session.team,
					failure: readyItem.error || null,
				},
				final_result: finalResult,
				result_path: session.result_path,
				session_file: session.session_file,
				artifacts: buildKernelArtifacts(session),
				events,
				metadata: {
					kernel_mode: "workflow",
					action,
					session_id: session.session_id,
					workflow_id: session.workflow_id,
					session_file: session.session_file,
					work_items_file: session.work_items_file,
					recommendation_file: session.recommendation_file,
					approval_file: session.approval_file,
					protocol_manifest: bundle.manifestPath,
				},
				provider_ids: ["pi-kernel", "python-worker-bridge"],
				skill_ids: [],
			};
		}
	}

	ensureWorkItems(session);
	session.status = deriveSessionStatus(session);
	if (session.status === "handoff_ready") {
		const waitingItem = nextRunnableWorkItem(session);
		let checkpoint: HandoffCheckpoint | null = null;
		if (waitingItem) {
			checkpoint = makeCheckpoint(
				session,
				action === "resume" ? "resume" : "handoff",
				`Workflow ${activeWorkflowId(session)} is waiting for an external response for ${waitingItem.work_item_id}.`,
				{
					work_item: waitingItem,
					iteration: waitingItem.iteration,
					kernelRoot,
				},
			);
			await persistCheckpoint(session, checkpoint, {
				work_item_id: waitingItem.work_item_id,
				step_id: waitingItem.step_id,
				iteration: waitingItem.iteration,
			});
			await appendJournalEvent(session, "awaiting_external_response", {
				work_item_id: waitingItem.work_item_id,
				checkpoint_id: checkpoint.checkpoint_id,
				action,
			});
		}
		syncTeamProjection(session);
		session.final_result = buildFinalResult(session);
		await persistFinalOutputs(session, session.final_result);
		await persistSession(session);
		events.push(
			runtimeEvent("agent_end", "Pi workflow kernel session is waiting for external input", { status: "running" }),
		);
		return {
			ok: true,
			status: "running",
			summary: waitingItem
				? `Workflow ${activeWorkflowId(session)} is waiting for an external response for ${waitingItem.work_item_id}.`
				: `Workflow ${activeWorkflowId(session)} is waiting for external input.`,
			output: {
				kernel_mode: "workflow",
				action,
				session_id: session.session_id,
				workflow_id: session.workflow_id,
				session,
				team: session.team,
				next_work_item: waitingItem || null,
				checkpoint,
				awaiting_external_response: true,
			},
			final_result: session.final_result,
			result_path: session.result_path,
			session_file: session.session_file,
			artifacts: buildKernelArtifacts(session),
			events,
			metadata: {
				kernel_mode: "workflow",
				action,
				session_id: session.session_id,
				workflow_id: session.workflow_id,
				session_file: session.session_file,
				work_items_file: session.work_items_file,
				protocol_manifest: bundle.manifestPath,
				awaiting_external_response: true,
			},
			provider_ids: ["pi-kernel"],
			skill_ids: [],
		};
	}

	computeRecommendation(session);
	syncApprovalState(session);
	if (shouldAutoAdvanceToApprovalConvergence(session)) {
		session.updated_at = nowIso();
		await appendJournalEvent(session, "workflow_transition", {
			from_workflow_id: session.workflow_id,
			to_workflow_id: "approval_convergence",
			reason: "auto_approval_convergence",
			recommended_variant_id: session.recommendation.recommended_variant_id,
		});
		syncTeamProjection(session);
		await persistSession(session);
		const nestedMetadata = {
			...ensureObject(session.request["metadata"]),
			entry_workflow_id:
				String(
					ensureObject(session.request["metadata"])["entry_workflow_id"] ||
						ensureObject(session.request["metadata"])["requested_workflow_id"] ||
						session.workflow_id,
				) || session.workflow_id,
			requested_workflow_id:
				String(ensureObject(session.request["metadata"])["requested_workflow_id"] || session.workflow_id) ||
				session.workflow_id,
			auto_approval_convergence: true,
			parent_workflow_id: session.workflow_id,
		};
		return runWorkflowKernel({
			...payload,
			session_id: session.session_id,
			session_workspace: sessionWorkspace,
			workspace_dir: session.workspace_dir,
			metadata: {
				...ensureObject(payload.metadata),
				team_adapter_family: session.team.adapter_family,
				adapter_family: session.team.adapter_family,
			},
			input_payload: {
				...ensureObject(payload.input_payload),
				workflow_session: {
					...ensureObject(ensureObject(payload.input_payload)["workflow_session"]),
					session_id: session.session_id,
					workflow_id: "approval_convergence",
					request: {
						...ensureObject(session.request),
						workflow_id: "approval_convergence",
						session_id: session.session_id,
						metadata: nestedMetadata,
					},
				},
			},
		});
	}
	if (session.workflow_id === "approval_convergence") {
		const requestMetadata = ensureObject(session.request["metadata"]);
		const operatorHints = ensureObject(session.request["operator_hints"]);
		const approvalGranted = Boolean(requestMetadata["approval_granted"] || operatorHints["approval_granted"]);
		const activationRequested = Boolean(
			requestMetadata["activate"] || requestMetadata["activation_requested"] || operatorHints["activation_requested"],
		);
		if (approvalGranted) {
			const actorId = String(requestMetadata["approved_by"] || "human");
			await applyApprovalAction(session, activationRequested ? "activate" : "approve", actorId, kernelRoot);
		}
	}
	session.status = deriveSessionStatus(session);
	const finalKind = session.status === "awaiting_approval" ? "approval" : "final";
	const finalCheckpoint = makeCheckpoint(
		session,
		finalKind,
		finalKind === "approval"
			? `Workflow ${session.workflow_id} is awaiting approval.`
			: `Workflow ${session.workflow_id} completed.`,
		{ kernelRoot },
	);
	await persistCheckpoint(session, finalCheckpoint, {});
	await appendJournalEvent(session, "session_completed", {
		checkpoint_id: finalCheckpoint.checkpoint_id,
		status: session.status,
	});
	syncTeamProjection(session);
	session.final_result = buildFinalResult(session);
	await persistFinalOutputs(session, session.final_result);
	await persistSession(session);
	events.push(runtimeEvent("agent_end", "Pi workflow kernel session finished", { status: "succeeded" }));
	return {
		ok: true,
		status: "succeeded",
		summary:
			session.status === "awaiting_approval"
				? `Workflow ${session.workflow_id} is awaiting approval.`
				: `Workflow ${session.workflow_id} completed.`,
		output: {
			kernel_mode: "workflow",
			action,
			session_id: session.session_id,
			workflow_id: session.workflow_id,
			session,
			team: session.team,
			result: session.final_result,
		},
		final_result: session.final_result,
		result_path: session.result_path,
		session_file: session.session_file,
		artifacts: buildKernelArtifacts(session),
		events,
		metadata: {
			kernel_mode: "workflow",
			action,
			session_id: session.session_id,
			workflow_id: session.workflow_id,
			session_file: session.session_file,
			work_items_file: session.work_items_file,
			recommendation_file: session.recommendation_file,
			approval_file: session.approval_file,
			result_path: session.result_path,
			protocol_manifest: bundle.manifestPath,
		},
		provider_ids: ["pi-kernel", "python-worker-bridge"],
		skill_ids: [],
	};
}
