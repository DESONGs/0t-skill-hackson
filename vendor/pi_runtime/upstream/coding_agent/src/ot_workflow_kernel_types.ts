export type Payload = {
	run_id?: string;
	session_id?: string;
	invocation_id?: string;
	runtime_id?: string;
	workspace_dir?: string;
	session_workspace?: string;
	cwd?: string;
	prompt?: string;
	input_payload?: Record<string, unknown>;
	metadata?: Record<string, unknown>;
};

export type JsonObject = Record<string, unknown>;

export type WorkflowAction = "run" | "resume" | "replay" | "handoff" | "approve" | "reject" | "activate" | "archive";
export type WorkflowStatus =
	| "draft"
	| "planned"
	| "running"
	| "handoff_ready"
	| "awaiting_approval"
	| "approved"
	| "activated"
	| "completed"
	| "failed"
	| "rejected"
	| "archived";
export type WorkItemStatus =
	| "blocked"
	| "ready"
	| "running"
	| "completed"
	| "failed"
	| "skipped"
	| "handoff_ready";
export type CheckpointKind = "handoff" | "resume" | "approval" | "failure" | "final";
export type ApprovalStatus = "not_required" | "pending" | "approved" | "rejected" | "activated";

export type BundleReference = {
	plugin_id?: string;
	workflow_id?: string;
	bridge_id?: string;
	display_id?: string;
	aliases?: string[];
	path: string;
};

export type BundleManifest = {
	bundle_id: string;
	bundle_version: string;
	contract_version: string;
	display_name?: string;
	default_bridge_id: string;
	defaults?: JsonObject;
	plugins: BundleReference[];
	workflows: BundleReference[];
	worker_bridges: BundleReference[];
};

export type PluginSpec = {
	plugin_id: string;
	plugin_version: string;
	plugin_type: string;
	display_name?: string;
	summary?: string;
	worker_actions?: string[];
	metadata?: JsonObject;
};

export type WorkflowStep = {
	step_id: string;
	title: string;
	plugin_id: string;
	action_id: string;
	stage: string;
	description: string;
	depends_on?: string[];
	outputs?: string[];
	allow_reentry?: boolean;
	loop_scope?: string | null;
	skip_if_session_has?: string[];
};

export type WorkflowSpec = {
	workflow_id: string;
	display_id?: string;
	aliases?: string[];
	title: string;
	description: string;
	iterative?: boolean;
	human_review_required?: boolean;
	entry_steps?: string[];
	terminal_steps?: string[];
	metadata?: JsonObject;
	iteration?: {
		budget_field?: string;
		decision_step_id?: string;
		reentry_step_id?: string;
	};
	steps: WorkflowStep[];
};

export type WorkerBridgeSpec = {
	bridge_id: string;
	bridge_version: string;
	contract_version: string;
	preferred_launchers: { launcher_id: string; argv: string[] }[];
	io: {
		request_file_arg: string;
		response_file_arg: string;
		request_contract_version: string;
		response_contract_version: string;
	};
	actions: Record<
		string,
		{
			plugin_id: string;
			summary: string;
			required_request_fields?: string[];
			required_inputs?: string[];
			produces?: string[];
			control_fields?: string[];
		}
	>;
};

export type WorkerResponse = {
	ok: boolean;
	status: string;
	action_id: string;
	outputs?: JsonObject;
	state_patch?: JsonObject;
	artifacts?: JsonObject[];
	events?: JsonObject[];
	control?: JsonObject;
	error?: JsonObject | null;
	metadata?: JsonObject;
	raw_result?: JsonObject;
	compat_payload?: JsonObject;
};

export type WorkflowWorkItem = {
	work_item_id: string;
	step_id: string;
	title: string;
	action_id: string;
	plugin_id: string;
	stage: string;
	iteration: number | null;
	loop_scope: string;
	status: WorkItemStatus;
	depends_on: string[];
	blocked_by: string[];
	allow_reentry: boolean;
	created_at?: string;
	started_at?: string | null;
	handoff_ready_at?: string | null;
	completed_at?: string | null;
	failed_at?: string | null;
	request_path?: string;
	response_path?: string;
	instructions_path?: string;
	artifact_paths: string[];
	launcher_id?: string;
	error?: JsonObject;
	summary?: string;
	updated_at: string;
};

export type RecommendationState = {
	status: string;
	summary: string;
	recommended_variant_id?: string | null;
	selected_variant?: JsonObject | null;
	leaderboard: JsonObject[];
	source: "kernel" | "worker";
	updated_at: string;
};

export type ApprovalRecord = {
	approval_id: string;
	action: "approve" | "reject" | "activate";
	status: ApprovalStatus;
	actor_id: string;
	summary: string;
	timestamp: string;
	metadata: JsonObject;
};

export type ApprovalState = {
	status: ApprovalStatus;
	required: boolean;
	recommended_variant_id?: string | null;
	last_action?: string | null;
	updated_at: string;
	records: ApprovalRecord[];
};

export type HandoffCheckpoint = {
	checkpoint_id: string;
	kind: CheckpointKind;
	session_id: string;
	workflow_id: string;
	status: WorkflowStatus;
	work_item_id?: string;
	step_id?: string;
	iteration?: number | null;
	summary: string;
	created_at: string;
	artifact_path: string;
};

export type WorkflowSessionState = {
	session_id: string;
	workflow_id: string;
	workspace_id?: string | null;
	status: WorkflowStatus;
	runtime_id: string;
	workspace_dir: string;
	cwd: string;
	action: WorkflowAction;
	request: JsonObject;
	workflow: WorkflowSpec;
	state: JsonObject;
	work_items: WorkflowWorkItem[];
	recommendation: RecommendationState;
	approval: ApprovalState;
	checkpoints: HandoffCheckpoint[];
	team: TeamProjection;
	iterations: JsonObject[];
	journal_path: string;
	session_file: string;
	work_items_file: string;
	recommendation_file: string;
	approval_file: string;
	approval_convergence_file?: string;
	team_file: string;
	result_path: string;
	created_at: string;
	last_step_id?: string;
	final_result?: JsonObject;
	updated_at: string;
};

export type TeamProjectionWorkItem = {
	id: string;
	role: string;
	title: string;
	kind: string;
	status: WorkItemStatus;
	depends_on: string[];
	input_refs: string[];
	instructions_path?: string | null;
	result_path?: string | null;
	metadata: JsonObject;
	created_at?: string;
	updated_at: string;
	started_at?: string | null;
	handoff_ready_at?: string | null;
	completed_at?: string | null;
	failed_at?: string | null;
};

export type TeamAgentSession = {
	agent_session_id: string;
	adapter_family: string;
	role: string;
	work_item_id: string;
	status: "prepared" | "running" | "completed" | "failed";
	opened_at: string;
	updated_at: string;
	checkpoint_id?: string | null;
	instructions_path?: string | null;
	result_path?: string | null;
	metadata: JsonObject;
};

export type TeamProjection = {
	adapter_family: string;
	work_items: TeamProjectionWorkItem[];
	agent_sessions: TeamAgentSession[];
	recommendation_state: RecommendationState;
	approval_records: ApprovalRecord[];
	last_run_at?: string | null;
	last_resume_at?: string | null;
	last_replay_at?: string | null;
	last_handoff_at?: string | null;
	updated_at: string;
};

export type WorkflowArtifact = {
	artifact_id: string;
	kind: string;
	uri: string;
	label: string;
	content_type?: string;
	payload?: unknown;
	metadata?: JsonObject;
};

export type WorkflowKernelResult = {
	ok: boolean;
	status: string;
	summary: string;
	output: JsonObject;
	final_result?: JsonObject;
	result_path?: string;
	session_file?: string;
	artifacts: WorkflowArtifact[];
	events: JsonObject[];
	metadata: JsonObject;
	provider_ids: string[];
	skill_ids: string[];
};
