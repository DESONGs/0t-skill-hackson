import { execFile } from "node:child_process";
import path from "node:path";
import process from "node:process";
import { WorkerBridgeSpec, WorkflowSessionState, WorkflowStep, WorkflowWorkItem, WorkerResponse } from "./ot_workflow_kernel_types.js";
import { readJson, writeJson } from "./ot_workflow_store.js";

function execFileAsync(
	file: string,
	args: string[],
	options: { cwd: string; env: NodeJS.ProcessEnv },
): Promise<{ stdout: string; stderr: string }> {
	return new Promise((resolve, reject) => {
		execFile(file, args, { cwd: options.cwd, env: options.env }, (error, stdout, stderr) => {
			if (error) {
				reject(
					new Error(
						[
							`command failed: ${file} ${args.join(" ")}`,
							error.message,
							stdout?.trim() ? `stdout:\n${stdout}` : "",
							stderr?.trim() ? `stderr:\n${stderr}` : "",
						]
							.filter(Boolean)
							.join("\n\n"),
					),
				);
				return;
			}
			resolve({ stdout, stderr });
		});
	});
}

function stepInputs(session: WorkflowSessionState, step: WorkflowStep, workItem: WorkflowWorkItem) {
	const outputs = session.state;
	switch (step.action_id) {
		case "distillation.execute":
			return {};
		case "skill_creation.materialize_baseline":
			return {
				baseline_variant: outputs["baseline_variant"] || {},
			};
		case "autoresearch.plan_iteration":
			return {
				baseline_variant: outputs["baseline_variant"] || {},
				parent_variant: outputs["selected_variant"] || outputs["parent_variant"] || outputs["baseline_variant"] || {},
				iteration_index: workItem.iteration || 1,
			};
		case "skill_creation.create_variants":
			return {
				variant_plans: outputs["variant_plans"] || [],
				planned_candidate_variants: outputs["planned_candidate_variants"] || [],
				parent_variant: outputs["parent_variant"] || outputs["selected_variant"] || outputs["baseline_variant"] || {},
				baseline_variant: outputs["baseline_variant"] || {},
				iteration_index: workItem.iteration || 1,
			};
		case "benchmark.score_baseline":
			return { baseline_variant: outputs["baseline_variant"] || {} };
		case "benchmark.score_candidates":
		case "benchmark.score_finalist":
			return {
				baseline_variant: outputs["baseline_variant"] || {},
				candidate_variants:
					outputs["candidate_variants"] ||
					(outputs["selected_variant"] ? [outputs["selected_variant"]] : []),
				iteration_index: workItem.iteration || 1,
			};
		case "review.evaluate_candidates":
		case "review.finalize_candidate":
			return {
				baseline_scorecard: outputs["baseline_scorecard"] || {},
				candidate_scorecards:
					outputs["candidate_scorecards"] ||
					(outputs["benchmark_scorecard"] ? [outputs["benchmark_scorecard"]] : []),
				iteration_index: workItem.iteration || 1,
			};
		case "approval_convergence.converge_approval":
			return {
				recommendation_bundle: outputs["recommendation_bundle"] || {},
				selected_variant: outputs["selected_variant"] || {},
				benchmark_scorecard: outputs["benchmark_scorecard"] || {},
				review_decision: outputs["review_decision"] || {},
				iteration_index: workItem.iteration || 1,
			};
		case "autoresearch.decide_iteration":
			return {
				baseline_variant: outputs["baseline_variant"] || {},
				baseline_scorecard: outputs["baseline_scorecard"] || {},
				candidate_variants: outputs["candidate_variants"] || [],
				candidate_scorecards: outputs["candidate_scorecards"] || [],
				review_decisions: outputs["review_decisions"] || [],
				iteration_index: workItem.iteration || 1,
			};
		default:
			return {};
	}
}

function resolveBridgeAction(step: WorkflowStep) {
	switch (step.action_id) {
		case "autoresearch.plan_iteration":
		case "skill_creation.materialize_baseline":
		case "skill_creation.create_variants":
		case "autoresearch.decide_iteration":
			return { mode: "python" as const, actionId: step.action_id };
		default:
			return { mode: "python" as const, actionId: step.action_id };
	}
}

function normalizeWorkerResponse(step: WorkflowStep, response: WorkerResponse, _workItem: WorkflowWorkItem): WorkerResponse {
	if (step.action_id === "autoresearch.decide_iteration") {
		return {
			...response,
			metadata: {
				...(response.metadata || {}),
				alias_action_id: "autoresearch.finalize_iteration",
				stop_decision: response.metadata?.["stop_decision"] || response.control || {},
			},
			state_patch: {
				...(response.state_patch || {}),
				stop_decision: response.metadata?.["stop_decision"] || response.control || {},
			},
		};
	}
	return response;
}

function businessRequest(session: WorkflowSessionState) {
	const request = session.request || {};
	return {
		workflow_id: session.workflow_id,
		session_id: session.session_id,
		workspace_id: typeof request["workspace_id"] === "string" ? request["workspace_id"] : undefined,
		workspace_dir: session.workspace_dir,
		wallet: typeof request["wallet"] === "string" ? request["wallet"] : undefined,
		chain: typeof request["chain"] === "string" ? request["chain"] : "bsc",
		skill_name: typeof request["skill_name"] === "string" ? request["skill_name"] : undefined,
		objective: typeof request["objective"] === "string" ? request["objective"] : undefined,
		iteration_budget: typeof request["iteration_budget"] === "number" ? request["iteration_budget"] : undefined,
		max_variants: typeof request["max_variants"] === "number" ? request["max_variants"] : undefined,
		candidate_variants: Array.isArray(request["candidate_variants"]) ? request["candidate_variants"] : undefined,
		data_source_adapter_id:
			typeof request["data_source_adapter_id"] === "string" ? request["data_source_adapter_id"] : undefined,
		execution_adapter_id:
			typeof request["execution_adapter_id"] === "string" ? request["execution_adapter_id"] : undefined,
		operator_hints: typeof request["operator_hints"] === "object" && request["operator_hints"] ? request["operator_hints"] : {},
		metadata: typeof request["metadata"] === "object" && request["metadata"] ? request["metadata"] : {},
	};
}

function preferredLaunchers(bridge: WorkerBridgeSpec) {
	const configuredPython = String(process.env.OT_WORKFLOW_PYTHON_EXECUTABLE || "").trim();
	if (!configuredPython) return bridge.preferred_launchers;
	return [
		{
			launcher_id: "env.python-executable",
			argv: [configuredPython],
		},
		...bridge.preferred_launchers,
	];
}

export async function invokePythonWorkerBridge(args: {
	cwd: string;
	bridge: WorkerBridgeSpec;
	session: WorkflowSessionState;
	step: WorkflowStep;
	workItem: WorkflowWorkItem;
	requestPath: string;
	responsePath: string;
}): Promise<{ response: WorkerResponse; launcherId: string }> {
	const bridgeAction = resolveBridgeAction(args.step);
	const env = {
		...process.env,
		PYTHONPATH: process.env.PYTHONPATH ? `${args.cwd}/src:${process.env.PYTHONPATH}` : `${args.cwd}/src`,
	};
	const requestPayload = {
		contract_version: args.bridge.io.request_contract_version,
		bridge_id: args.bridge.bridge_id,
		bridge_version: args.bridge.bridge_version,
		action_id: bridgeAction.actionId,
		workflow_id: args.session.workflow_id,
		workflow_step_id: args.step.step_id,
		workspace_dir: args.session.workspace_dir,
		request: businessRequest(args.session),
		state: {
			...args.session.state,
			...stepInputs(args.session, args.step, args.workItem),
		},
		metadata: {
			session_id: args.session.session_id,
			runtime_id: args.session.runtime_id,
			work_item_id: args.workItem.work_item_id,
			iteration: args.workItem.iteration,
		},
	};
	await writeJson(args.requestPath, requestPayload);
	let lastError: Error | null = null;
	for (const launcher of preferredLaunchers(args.bridge)) {
		if (!launcher.argv.length) continue;
		const [file, ...prefix] = launcher.argv;
		const cmdArgs = [
			...prefix,
			"-m",
			"ot_skill_enterprise.nextgen.worker_bridge",
			"--project-root",
			args.cwd,
			args.bridge.io.request_file_arg,
			args.requestPath,
			args.bridge.io.response_file_arg,
			args.responsePath,
		];
		try {
			await execFileAsync(file, cmdArgs, { cwd: args.cwd, env });
			const response = await readJson<WorkerResponse>(args.responsePath);
			return {
				response: normalizeWorkerResponse(args.step, response, args.workItem),
				launcherId: launcher.launcher_id,
			};
		} catch (error) {
			lastError = error instanceof Error ? error : new Error(String(error));
		}
	}
	throw lastError || new Error(`unable to launch worker bridge for ${args.step.action_id}`);
}
