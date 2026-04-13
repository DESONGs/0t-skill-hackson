import fs from "node:fs/promises";
import path from "node:path";
import { completeSimple, getEnvApiKey, getModels, getProviders, type KnownProvider } from "@mariozechner/pi-ai";

type RuntimePayload = {
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

type ReflectionJob = {
	subject_kind?: string;
	subject_id?: string;
	flow_id?: string;
	system_prompt?: string;
	compact_input?: Record<string, unknown>;
	expected_output_schema?: Record<string, unknown>;
	artifact_root?: string;
	prompt?: string;
	metadata?: Record<string, unknown>;
};

function runtimeEvent(type: string, message: string, extra: Record<string, unknown> = {}) {
	return {
		type,
		message,
		...extra,
	};
}

function ensureObject(value: unknown): Record<string, unknown> {
	if (value && typeof value === "object" && !Array.isArray(value)) {
		return { ...(value as Record<string, unknown>) };
	}
	return {};
}

function extractTextContent(message: { content?: Array<{ type?: string; text?: string }> }): string {
	return (message.content || [])
		.filter((item): item is { type: string; text: string } => item?.type === "text" && typeof item.text === "string")
		.map((item) => item.text)
		.join("\n")
		.trim();
}

function normalizeJsonText(text: string): string {
	const trimmed = text.trim();
	if (trimmed.startsWith("```")) {
		const firstNewline = trimmed.indexOf("\n");
		const lastFence = trimmed.lastIndexOf("```");
		if (firstNewline !== -1 && lastFence > firstNewline) {
			return trimmed.slice(firstNewline + 1, lastFence).trim();
		}
	}
	return trimmed;
}

function parseJsonObject(text: string): Record<string, unknown> {
	const normalized = normalizeJsonText(text);
	const parsed = JSON.parse(normalized);
	if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
		throw new Error("Reflection output must be a JSON object");
	}
	return parsed as Record<string, unknown>;
}

function buildReflectionPrompt(job: ReflectionJob): string {
	return [
		"Return exactly one JSON object and nothing else.",
		"The JSON object must satisfy the following expected schema:",
		JSON.stringify(ensureObject(job.expected_output_schema), null, 2),
		"Use the following compact input as the source of truth:",
		JSON.stringify(ensureObject(job.compact_input), null, 2),
		job.prompt ? `Task hint: ${job.prompt}` : "",
	]
		.filter((line) => line.trim().length > 0)
		.join("\n\n");
}

function resolveModelReference(reference: string) {
	const normalized = reference.trim().toLowerCase();
	if (!normalized) return undefined;
	for (const provider of getProviders()) {
		const match = getModels(provider)
			.find((item) => `${item.provider}/${item.id}`.toLowerCase() === normalized || item.id.toLowerCase() === normalized);
		if (match) {
			return match;
		}
	}
	return undefined;
}

async function resolveModel(job: ReflectionJob) {
	const metadata = ensureObject(job.metadata);
	const configuredReference = [
		String(metadata["model"] || "").trim(),
		String(process.env.OT_PI_REFLECTION_MODEL || "").trim(),
		String(process.env.OT_PI_DEFAULT_MODEL || "").trim(),
	].find((value) => value.length > 0);

	let model = configuredReference ? resolveModelReference(configuredReference) : undefined;
	if (!model) {
		for (const provider of getProviders()) {
			const available = getModels(provider as KnownProvider).find((item) => Boolean(getEnvApiKey(item.provider)));
			if (available) {
				model = available;
				break;
			}
		}
	}
	if (!model) {
		throw new Error("No Pi model is available via environment auth. Set a provider API key or OT_PI_DEFAULT_MODEL.");
	}
	return {
		model,
		apiKey: getEnvApiKey(model.provider),
		headers: undefined,
		reasoning: String(metadata["reasoning_level"] || process.env.OT_PI_REFLECTION_REASONING || "medium"),
	};
}

async function writeJson(pathname: string, payload: unknown) {
	await fs.mkdir(path.dirname(pathname), { recursive: true });
	await fs.writeFile(pathname, JSON.stringify(payload, null, 2), "utf8");
}

export async function runReflectionMode(payload: RuntimePayload) {
	const inputPayload = ensureObject(payload.input_payload);
	const metadata = ensureObject(payload.metadata);
	const job = ensureObject(inputPayload["reflection_job"]) as ReflectionJob;
	const runId = String(payload.run_id || `run-${Date.now()}`);
	const sessionId = String(payload.session_id || `pi-session-${Date.now()}`);
	const invocationId = String(payload.invocation_id || `pi-invocation-${Date.now()}`);
	const runtimeId = String(payload.runtime_id || "pi");
	const cwd = path.resolve(String(payload.cwd || process.cwd()));
	const workspaceDir = path.resolve(String(payload.workspace_dir || path.join(cwd, ".ot-workspace")));
	const sessionWorkspace = path.resolve(String(payload.session_workspace || path.join(workspaceDir, "runtime-sessions", sessionId)));
	const artifactRoot = path.resolve(String(job.artifact_root || path.join(sessionWorkspace, "reflection")));
	const requestPath = path.join(artifactRoot, `${runId}.reflection.request.json`);
	const rawPath = path.join(artifactRoot, `${runId}.reflection.raw.json`);
	const normalizedPath = path.join(artifactRoot, `${runId}.reflection.normalized.json`);

	await fs.mkdir(artifactRoot, { recursive: true });
	await writeJson(requestPath, {
		run_id: runId,
		session_id: sessionId,
		invocation_id: invocationId,
		runtime_id: runtimeId,
		job,
		metadata,
	});

	const events: Array<Record<string, unknown>> = [
		runtimeEvent("agent_start", "Pi reflection session started", {
			status: "running",
			metadata: { session_id: sessionId, runtime_id: runtimeId, flow_id: job.flow_id || "wallet_style_reflection_review" },
		}),
		runtimeEvent("turn_start", "Pi reflection invocation started", {
			status: "running",
			metadata: { session_id: sessionId, invocation_id: invocationId },
		}),
		runtimeEvent("message_start", "Pi reflection agent preparing structured review", {
			status: "running",
			metadata: { session_id: sessionId, invocation_id: invocationId },
		}),
	];

	try {
		const mockResponse = ensureObject(ensureObject(job.metadata)["mock_response"]);
		let normalizedOutput: Record<string, unknown>;
		let rawOutput: Record<string, unknown>;
		let reviewBackend: string;
		let providerIds: string[];
		if (Object.keys(mockResponse).length > 0) {
			normalizedOutput = mockResponse;
			rawOutput = { text: JSON.stringify(mockResponse, null, 2), mode: "mock" };
			reviewBackend = "pi-reflection-mock";
			providerIds = ["mock"];
		} else {
			const systemPrompt = String(job.system_prompt || "").trim();
			const promptText = buildReflectionPrompt(job);
			const { model, apiKey, headers, reasoning } = await resolveModel(job);
			const response = await completeSimple(
				model,
				{
					systemPrompt,
					messages: [
						{
							role: "user" as const,
							content: [{ type: "text" as const, text: promptText }],
							timestamp: Date.now(),
						},
					],
				},
				model.reasoning ? { apiKey, headers, reasoning, maxTokens: 3000 } : { apiKey, headers, maxTokens: 3000 },
			);
			if (response.stopReason === "error") {
				throw new Error(response.errorMessage || "Pi reflection completion failed");
			}
			const responseText = extractTextContent(response);
			normalizedOutput = parseJsonObject(responseText);
			rawOutput = {
				text: responseText,
				stop_reason: response.stopReason,
				model: {
					provider: model.provider,
					model_id: model.id,
					api: model.api,
				},
			};
			reviewBackend = `pi-reflection-agent:${model.provider}/${model.id}`;
			providerIds = [model.provider];
		}

		await writeJson(rawPath, rawOutput);
		await writeJson(normalizedPath, normalizedOutput);
		const summary =
			String(ensureObject(ensureObject(normalizedOutput)["review"])["reasoning"] || "").trim() ||
			String(ensureObject(ensureObject(normalizedOutput)["profile"])["summary"] || "").trim() ||
			"Pi reflection review completed.";
		events.push(
			runtimeEvent("message_end", summary, {
				status: "succeeded",
				summary,
				metadata: {
					session_id: sessionId,
					invocation_id: invocationId,
					review_backend: reviewBackend,
				},
			}),
			runtimeEvent("turn_end", "Pi reflection invocation finished", {
				status: "succeeded",
				summary,
				metadata: {
					session_id: sessionId,
					invocation_id: invocationId,
					run_id: runId,
					review_backend: reviewBackend,
				},
			}),
			runtimeEvent("agent_end", "Pi reflection session finished", {
				status: "succeeded",
				summary,
				metadata: {
					session_id: sessionId,
					runtime_id: runtimeId,
					run_id: runId,
					review_backend: reviewBackend,
				},
			}),
		);
		return {
			ok: true,
			status: "succeeded",
			summary,
			output: {
				summary,
				review_backend: reviewBackend,
				normalized_output: normalizedOutput,
				raw_output: rawOutput,
			},
			artifacts: [
				{
					artifact_id: `${runId}-reflection-request`,
					kind: "reflection.request.json",
					uri: requestPath,
					label: "Pi reflection request",
					content_type: "application/json",
					payload: { flow_id: job.flow_id, subject_kind: job.subject_kind, subject_id: job.subject_id },
					metadata: { runtime_id: runtimeId, session_id: sessionId, invocation_id: invocationId },
				},
				{
					artifact_id: `${runId}-reflection-raw`,
					kind: "reflection.raw.json",
					uri: rawPath,
					label: "Pi reflection raw output",
					content_type: "application/json",
					payload: rawOutput,
					metadata: { runtime_id: runtimeId, session_id: sessionId, invocation_id: invocationId },
				},
				{
					artifact_id: `${runId}-reflection-normalized`,
					kind: "reflection.normalized.json",
					uri: normalizedPath,
					label: "Pi reflection normalized output",
					content_type: "application/json",
					payload: normalizedOutput,
					metadata: { runtime_id: runtimeId, session_id: sessionId, invocation_id: invocationId },
				},
			],
			events,
			metadata: {
				runtime_id: runtimeId,
				session_id: sessionId,
				invocation_id: invocationId,
				workspace_dir: workspaceDir,
				cwd,
				flow_id: job.flow_id || "wallet_style_reflection_review",
				subject_kind: job.subject_kind || "reflection",
				subject_id: job.subject_id || sessionId,
				review_backend: reviewBackend,
			},
			provider_ids: providerIds,
			skill_ids: [],
		};
	} catch (error) {
		const errorMessage = String(error instanceof Error ? error.message : error);
		await writeJson(rawPath, { error: errorMessage });
		events.push(
			runtimeEvent("message_end", errorMessage, {
				status: "failed",
				summary: errorMessage,
				metadata: { session_id: sessionId, invocation_id: invocationId },
			}),
			runtimeEvent("turn_end", "Pi reflection invocation failed", {
				status: "failed",
				summary: errorMessage,
				metadata: { session_id: sessionId, invocation_id: invocationId, run_id: runId },
			}),
			runtimeEvent("error", "Pi reflection session failed", {
				status: "failed",
				summary: errorMessage,
				metadata: { session_id: sessionId, runtime_id: runtimeId, run_id: runId },
			}),
		);
		return {
			ok: false,
			status: "failed",
			summary: errorMessage,
			output: {
				summary: errorMessage,
				review_backend: "pi-reflection-runtime",
				normalized_output: {},
				raw_output: { error: errorMessage },
			},
			artifacts: [
				{
					artifact_id: `${runId}-reflection-request`,
					kind: "reflection.request.json",
					uri: requestPath,
					label: "Pi reflection request",
					content_type: "application/json",
					payload: { flow_id: job.flow_id, subject_kind: job.subject_kind, subject_id: job.subject_id },
					metadata: { runtime_id: runtimeId, session_id: sessionId, invocation_id: invocationId },
				},
				{
					artifact_id: `${runId}-reflection-raw`,
					kind: "reflection.raw.json",
					uri: rawPath,
					label: "Pi reflection raw output",
					content_type: "application/json",
					payload: { error: errorMessage },
					metadata: { runtime_id: runtimeId, session_id: sessionId, invocation_id: invocationId },
				},
			],
			events,
			metadata: {
				runtime_id: runtimeId,
				session_id: sessionId,
				invocation_id: invocationId,
				workspace_dir: workspaceDir,
				cwd,
				flow_id: job.flow_id || "wallet_style_reflection_review",
				subject_kind: job.subject_kind || "reflection",
				subject_id: job.subject_id || sessionId,
				review_backend: "pi-reflection-runtime",
				error: errorMessage,
			},
			provider_ids: [],
			skill_ids: [],
		};
	}
}
