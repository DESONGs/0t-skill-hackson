import fs from "node:fs/promises";
import path from "node:path";
import { BundleManifest, PluginSpec, WorkerBridgeSpec, WorkflowSpec } from "./ot_workflow_kernel_types.js";

export const DEFAULT_PROTOCOL_MANIFEST = "0t-protocol/nextgen/manifest.json";

export type LoadedBundle = {
	manifestPath: string;
	manifest: BundleManifest;
	plugins: Map<string, PluginSpec>;
	workflows: Map<string, WorkflowSpec>;
	bridges: Map<string, WorkerBridgeSpec>;
};

async function readJson<T>(filePath: string): Promise<T> {
	return JSON.parse(await fs.readFile(filePath, "utf8")) as T;
}

function stringList(...values: unknown[]) {
	const seen = new Set<string>();
	const resolved: string[] = [];
	for (const value of values) {
		if (typeof value === "string") {
			const text = value.trim();
			if (text && !seen.has(text)) {
				seen.add(text);
				resolved.push(text);
			}
			continue;
		}
		if (Array.isArray(value)) {
			for (const item of value) {
				const text = String(item || "").trim();
				if (text && !seen.has(text)) {
					seen.add(text);
					resolved.push(text);
				}
			}
		}
	}
	return resolved;
}

function cloneStep(step: WorkflowSpec["steps"][number]) {
	return JSON.parse(JSON.stringify(step)) as WorkflowSpec["steps"][number];
}

function ensureSkillCreationPlugin(plugins: Map<string, PluginSpec>) {
	if (plugins.has("skill-creation")) return;
	plugins.set("skill-creation", {
		plugin_id: "skill-creation",
		plugin_version: "1.0.0",
		plugin_type: "skill-creation",
		display_name: "Skill Creation",
		summary: "Materializes baseline and candidate variants into replayable artifacts.",
		worker_actions: ["skill_creation.materialize_baseline", "skill_creation.create_variants"],
		metadata: {
			worker_tier: "python-domain-worker",
			synthesized_by: "ts-workflow-kernel",
		},
	});
}

function ensureApprovalConvergencePlugin(plugins: Map<string, PluginSpec>) {
	if (plugins.has("approval-convergence")) return;
	plugins.set("approval-convergence", {
		plugin_id: "approval-convergence",
		plugin_version: "1.0.0",
		plugin_type: "approval-convergence",
		display_name: "Approval Convergence",
		summary: "Aggregates finalist benchmark and review outputs into approval-ready activation state.",
		worker_actions: ["approval_convergence.converge_approval"],
		metadata: {
			worker_tier: "python-domain-worker",
			composes_with: ["benchmark", "review"],
			synthesized_by: "ts-workflow-kernel",
		},
	});
}

function ensureBridgeActions(bridges: Map<string, WorkerBridgeSpec>) {
	for (const bridge of bridges.values()) {
		bridge.actions = bridge.actions || {};
		bridge.actions["skill_creation.materialize_baseline"] ||= {
			plugin_id: "skill-creation",
			summary: "Materialize a distilled baseline into replayable artifacts.",
			required_inputs: ["baseline_variant"],
			produces: ["baseline_materialization_bundle"],
		};
		bridge.actions["skill_creation.create_variants"] ||= {
			plugin_id: "skill-creation",
			summary: "Materialize planned variants into candidate artifacts.",
			required_inputs: ["variant_plans", "planned_candidate_variants"],
			produces: ["candidate_variants", "variant_materialization_bundle"],
		};
		bridge.actions["autoresearch.plan_iteration"] ||= {
			plugin_id: "autoresearch",
			summary: "Plan the next research iteration.",
			required_inputs: ["baseline_variant", "parent_variant"],
			produces: ["variant_plans", "planned_candidate_variants"],
		};
		bridge.actions["autoresearch.decide_iteration"] ||= {
			plugin_id: "autoresearch",
			summary: "Decide whether the research loop should continue.",
			required_inputs: ["candidate_scorecards", "review_decisions", "baseline_scorecard"],
			produces: ["recommendation_bundle", "stop_decision"],
		};
		bridge.actions["approval_convergence.converge_approval"] ||= {
			plugin_id: "approval-convergence",
			summary: "Aggregate finalist benchmark and review outputs into approval-ready activation state.",
			required_inputs: ["baseline_variant", "recommendation_bundle", "benchmark_scorecard", "review_decision"],
			produces: [
				"approval_convergence_result",
				"approval_activation_bundle",
				"approval_recommendation",
				"kernel_handoff_payload",
			],
		};
	}
}

function normalizeDistillationSeed(workflow: WorkflowSpec): WorkflowSpec {
	const hasMaterialize = workflow.steps.some((step) => step.step_id === "materialize_baseline");
	if (hasMaterialize) return workflow;
	const steps = workflow.steps.map((step) => cloneStep(step));
	const seed = steps.find((step) => step.step_id === "seed_baseline") || steps[0];
	if (!seed) return workflow;
	steps.push({
		step_id: "materialize_baseline",
		title: "Materialize Baseline",
		plugin_id: "skill-creation",
		action_id: "skill_creation.materialize_baseline",
		stage: "execute",
		description: "Convert the distilled baseline into replayable package and QA artifacts.",
		depends_on: [seed.step_id],
		outputs: ["baseline_variant", "baseline_materialization_bundle"],
	});
	return {
		...workflow,
		terminal_steps: ["materialize_baseline"],
		steps,
	};
}

function normalizeAutonomousResearch(workflow: WorkflowSpec): WorkflowSpec {
	const hasPlanIteration = workflow.steps.some((step) => step.step_id === "plan_iteration");
	const hasCreateVariants = workflow.steps.some((step) => step.step_id === "create_variants");
	const hasMaterializeBaseline = workflow.steps.some((step) => step.step_id === "materialize_baseline");
	if (hasPlanIteration && hasCreateVariants && hasMaterializeBaseline) return workflow;
	const seed = workflow.steps.find((step) => step.step_id === "seed_baseline");
	const benchmarkBaseline = workflow.steps.find((step) => step.step_id === "benchmark_baseline");
	const benchmarkCandidates = workflow.steps.find((step) => step.step_id === "benchmark_candidates");
	const reviewCandidates = workflow.steps.find((step) => step.step_id === "review_candidates");
	const decideNextIteration = workflow.steps.find((step) => step.step_id === "decide_next_iteration");
	if (!seed || !benchmarkBaseline || !benchmarkCandidates || !reviewCandidates || !decideNextIteration) return workflow;
	return {
		...workflow,
		entry_steps: ["seed_baseline"],
		terminal_steps: ["decide_next_iteration"],
		iteration: {
			...(workflow.iteration || {}),
			decision_step_id: "decide_next_iteration",
			reentry_step_id: "plan_iteration",
		},
		steps: [
			{
				...cloneStep(seed),
				action_id: "distillation.execute",
			},
			{
				step_id: "materialize_baseline",
				title: "Materialize Baseline",
				plugin_id: "skill-creation",
				action_id: "skill_creation.materialize_baseline",
				stage: "execute",
				description: "Normalize the baseline into replayable materialization artifacts before benchmark starts.",
				depends_on: ["seed_baseline"],
				outputs: ["baseline_variant", "baseline_materialization_bundle"],
			},
			{
				...cloneStep(benchmarkBaseline),
				depends_on: ["materialize_baseline"],
				loop_scope: "workflow",
			},
			{
				step_id: "plan_iteration",
				title: "Plan Iteration",
				plugin_id: "autoresearch",
				action_id: "autoresearch.plan_iteration",
				stage: "plan",
				description: "Plan the current iteration and emit artifact-backed variant plans.",
				depends_on: ["benchmark_baseline"],
				outputs: ["variant_plans"],
				loop_scope: "iteration",
			},
			{
				step_id: "create_variants",
				title: "Create Variants",
				plugin_id: "skill-creation",
				action_id: "skill_creation.create_variants",
				stage: "execute",
				description: "Materialize planned variant mutations into replayable candidate artifacts.",
				depends_on: ["plan_iteration"],
				outputs: ["candidate_variants", "variant_materialization_bundle"],
				loop_scope: "iteration",
			},
			{
				...cloneStep(benchmarkCandidates),
				depends_on: ["create_variants"],
				loop_scope: "iteration",
			},
			{
				...cloneStep(reviewCandidates),
				depends_on: ["benchmark_candidates"],
				loop_scope: "iteration",
			},
			{
				...cloneStep(decideNextIteration),
				action_id: "autoresearch.decide_iteration",
				depends_on: ["review_candidates"],
				loop_scope: "iteration",
			},
		],
	};
}

function normalizeApprovalConvergence(workflow: WorkflowSpec): WorkflowSpec {
	const hasConvergeApproval = workflow.steps.some((step) => step.step_id === "converge_approval");
	if (hasConvergeApproval) return workflow;
	const benchmarkFinalist = workflow.steps.find((step) => step.step_id === "benchmark_finalist");
	const reviewFinalist = workflow.steps.find((step) => step.step_id === "review_finalist");
	if (!benchmarkFinalist || !reviewFinalist) return workflow;
	return {
		...workflow,
		terminal_steps: ["converge_approval"],
		steps: [
			cloneStep(benchmarkFinalist),
			{
				...cloneStep(reviewFinalist),
				outputs: Array.from(new Set([...(reviewFinalist.outputs || []), "review_decision"])),
			},
			{
				step_id: "converge_approval",
				title: "Converge Approval",
				plugin_id: "approval-convergence",
				action_id: "approval_convergence.converge_approval",
				stage: "finalize",
				description: "Aggregate finalist benchmark and review outputs into approval-ready activation state.",
				depends_on: ["review_finalist"],
				outputs: [
					"approval_convergence_result",
					"approval_activation_bundle",
					"approval_recommendation",
					"kernel_handoff_payload",
				],
			},
		],
	};
}

function normalizeWorkflowSpec(workflow: WorkflowSpec): WorkflowSpec {
	if (workflow.workflow_id === "distillation_seed") return normalizeDistillationSeed(workflow);
	if (workflow.workflow_id === "autonomous_research") return normalizeAutonomousResearch(workflow);
	if (workflow.workflow_id === "approval_convergence") return normalizeApprovalConvergence(workflow);
	return workflow;
}

function mergeWorkflowIdentity(reference: BundleReference, workflow: WorkflowSpec): WorkflowSpec {
	const metadata = typeof workflow.metadata === "object" && workflow.metadata ? { ...workflow.metadata } : {};
	const displayId = String(workflow.display_id || reference.display_id || "").trim() || undefined;
	const aliases = stringList(workflow.aliases, reference.aliases, displayId);
	return {
		...workflow,
		display_id: displayId,
		aliases,
		metadata: {
			...metadata,
			protocol_namespace: "0t",
			protocol_bundle_dir: "0t-protocol",
			canonical_workflow_id: workflow.workflow_id,
			display_workflow_id: displayId || workflow.workflow_id,
			invocation_ids: stringList(workflow.workflow_id, displayId, aliases, metadata["invocation_ids"]),
		},
	};
}

function resolveReference(cwd: string, manifestPath: string, referencePath: string): string {
	if (path.isAbsolute(referencePath)) return referencePath;
	if (referencePath.startsWith("0t-protocol/")) return path.resolve(cwd, referencePath);
	return path.resolve(path.dirname(manifestPath), referencePath);
}

export async function loadWorkflowProtocolBundle(cwd: string, manifestOverride?: string): Promise<LoadedBundle> {
	const manifestPath = path.resolve(cwd, manifestOverride || DEFAULT_PROTOCOL_MANIFEST);
	const manifest = await readJson<BundleManifest>(manifestPath);
	const plugins = new Map<string, PluginSpec>();
	const workflows = new Map<string, WorkflowSpec>();
	const bridges = new Map<string, WorkerBridgeSpec>();

	for (const reference of manifest.plugins) {
		if (!reference.plugin_id) continue;
		plugins.set(reference.plugin_id, await readJson<PluginSpec>(resolveReference(cwd, manifestPath, reference.path)));
	}
	ensureSkillCreationPlugin(plugins);
	ensureApprovalConvergencePlugin(plugins);
	for (const reference of manifest.workflows) {
		if (!reference.workflow_id) continue;
		const workflow = mergeWorkflowIdentity(
			reference,
			normalizeWorkflowSpec(await readJson<WorkflowSpec>(resolveReference(cwd, manifestPath, reference.path))),
		);
		workflows.set(workflow.workflow_id, workflow);
		for (const invocationId of stringList(
			workflow.display_id,
			workflow.aliases,
			workflow.metadata?.["invocation_ids"],
			reference.display_id,
			reference.aliases,
		)) {
			workflows.set(invocationId, workflow);
		}
	}
	for (const reference of manifest.worker_bridges) {
		if (!reference.bridge_id) continue;
		bridges.set(reference.bridge_id, await readJson<WorkerBridgeSpec>(resolveReference(cwd, manifestPath, reference.path)));
	}
	ensureBridgeActions(bridges);

	return { manifestPath, manifest, plugins, workflows, bridges };
}

export function workflowScopeSteps(workflow: WorkflowSpec) {
	return workflow.steps.filter((step) => (step.loop_scope || "workflow") !== "iteration");
}

export function iterationScopeSteps(workflow: WorkflowSpec) {
	return workflow.steps.filter((step) => (step.loop_scope || "workflow") === "iteration");
}

export function findWorkflowStep(workflow: WorkflowSpec, stepId: string) {
	return workflow.steps.find((step) => step.step_id === stepId);
}
