import fs from "node:fs/promises";
import path from "node:path";
import {
	ApprovalState,
	CheckpointKind,
	HandoffCheckpoint,
	JsonObject,
	RecommendationState,
	WorkflowSessionState,
	WorkflowWorkItem,
} from "./ot_workflow_kernel_types.js";

export function nowIso() {
	return new Date().toISOString();
}

export async function readJson<T>(filePath: string): Promise<T> {
	return JSON.parse(await fs.readFile(filePath, "utf8")) as T;
}

export async function writeJson(filePath: string, value: unknown) {
	await fs.mkdir(path.dirname(filePath), { recursive: true });
	await fs.writeFile(filePath, JSON.stringify(value, null, 2), "utf8");
}

export async function appendJsonLine(filePath: string, value: unknown) {
	await fs.mkdir(path.dirname(filePath), { recursive: true });
	await fs.appendFile(filePath, `${JSON.stringify(value)}\n`, "utf8");
}

export async function persistSession(session: WorkflowSessionState) {
	session.updated_at = nowIso();
	await writeJson(session.session_file, session);
	await writeJson(session.work_items_file, { work_items: session.work_items });
	await writeJson(session.recommendation_file, session.recommendation);
	await writeJson(session.approval_file, session.approval);
	if (session.approval_convergence_file && session.state["approval_convergence_result"]) {
		await writeJson(session.approval_convergence_file, session.state["approval_convergence_result"]);
	}
	await writeJson(session.team_file, session.team);
}

export async function loadSession(sessionFile: string): Promise<WorkflowSessionState | null> {
	try {
		return await readJson<WorkflowSessionState>(sessionFile);
	} catch {
		return null;
	}
}

export function defaultRecommendationState(): RecommendationState {
	return {
		status: "idle",
		summary: "No recommendation yet.",
		leaderboard: [],
		source: "kernel",
		updated_at: nowIso(),
	};
}

export function defaultApprovalState(required: boolean): ApprovalState {
	return {
		status: required ? "pending" : "not_required",
		required,
		updated_at: nowIso(),
		records: [],
	};
}

export function makeCheckpoint(
	session: WorkflowSessionState,
	kind: CheckpointKind,
	summary: string,
	options: {
		work_item?: WorkflowWorkItem;
		iteration?: number | null;
		kernelRoot: string;
	} = { kernelRoot: "" },
): HandoffCheckpoint {
	const checkpointId = `${kind}-${Date.now()}`;
	const artifactPath = path.join(options.kernelRoot, "checkpoints", `${checkpointId}.json`);
	return {
		checkpoint_id: checkpointId,
		kind,
		session_id: session.session_id,
		workflow_id: session.workflow_id,
		status: session.status,
		work_item_id: options.work_item?.work_item_id,
		step_id: options.work_item?.step_id,
		iteration: options.iteration ?? options.work_item?.iteration ?? null,
		summary,
		created_at: nowIso(),
		artifact_path: artifactPath,
	};
}

export async function persistCheckpoint(
	session: WorkflowSessionState,
	checkpoint: HandoffCheckpoint,
	extra: JsonObject,
) {
	session.checkpoints.push(checkpoint);
	await writeJson(checkpoint.artifact_path, {
		checkpoint,
		session_id: session.session_id,
		workflow_id: session.workflow_id,
		status: session.status,
		work_item: checkpoint.work_item_id
			? session.work_items.find((item) => item.work_item_id === checkpoint.work_item_id) || null
			: null,
		recommendation: session.recommendation,
		approval: session.approval,
		extra,
	});
}
