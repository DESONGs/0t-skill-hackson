import fs from "node:fs/promises";
import path from "node:path";
import { completeSimple, getEnvApiKey, getModels, getProviders, type KnownProvider } from "@mariozechner/pi-ai";
import AjvModule from "ajv";
import { parseStreamingJson } from "../../ai/src/utils/json-parse.js";

const Ajv = (AjvModule as any).default || AjvModule;
const reflectionAjv = new Ajv({ allErrors: true, allowUnionTypes: true, strict: false });

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
	injected_context?: Record<string, unknown>;
	user_payload?: Record<string, unknown>;
	context_sources?: Array<Record<string, unknown>>;
	metadata?: Record<string, unknown>;
};

type ReflectionFailureType =
	| "runtime_abort"
	| "runtime_timeout"
	| "provider_unavailable"
	| "empty_output"
	| "json_parse_failed"
	| "schema_rejected"
	| "generic_rejected";

type ReflectionAttemptRecord = {
	attempt_index: number;
	provider?: string;
	model_id?: string;
	model?: string;
	api?: string;
	failure_type?: ReflectionFailureType;
	error?: string;
	raw_text?: string;
	raw_text_salvaged?: boolean;
	stop_reason?: string;
	content_blocks?: Array<Record<string, unknown>>;
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

function extractTextContent(
	message: {
		content?: Array<{ type?: string; text?: string; thinking?: string; name?: string; arguments?: unknown }> | string;
	},
): string {
	const content = message.content;
	if (typeof content === "string") {
		return content.trim();
	}
	if (!Array.isArray(content)) {
		return "";
	}
	const textBlocks = content
		.filter((item): item is { type: string; text: string } => item?.type === "text" && typeof item.text === "string")
		.map((item) => item.text.trim())
		.filter((item) => item.length > 0);
	if (textBlocks.length > 0) {
		return textBlocks.join("\n").trim();
	}
	const thinkingBlocks = content
		.filter((item): item is { type: string; thinking: string } => item?.type === "thinking" && typeof item.thinking === "string")
		.map((item) => item.thinking.trim())
		.filter((item) => item.length > 0);
	if (thinkingBlocks.length > 0) {
		return thinkingBlocks.join("\n").trim();
	}
	return "";
}

function summarizeContentBlocks(
	message: {
		content?: Array<{ type?: string; text?: string; thinking?: string; name?: string; arguments?: unknown }> | string;
	},
): Array<Record<string, unknown>> {
	const content = message.content;
	if (typeof content === "string") {
		return [{ type: "string", preview: content.slice(0, 500) }];
	}
	if (!Array.isArray(content)) {
		return [];
	}
	return content.map((item) => {
		if (item?.type === "text" && typeof item.text === "string") {
			return { type: "text", preview: item.text.slice(0, 500), length: item.text.length };
		}
		if (item?.type === "thinking" && typeof item.thinking === "string") {
			return { type: "thinking", preview: item.thinking.slice(0, 500), length: item.thinking.length };
		}
		if (item?.type === "toolCall") {
			return { type: "toolCall", name: String(item.name || ""), arguments: item.arguments };
		}
		return { type: String(item?.type || "unknown") };
	});
}

function normalizeJsonText(text: string): string {
	const trimmed = text.trim();
	const fencedMatch = trimmed.match(/```(?:json)?\s*([\s\S]*?)```/i);
	if (fencedMatch?.[1]) {
		return fencedMatch[1].trim();
	}
	const firstBrace = trimmed.indexOf("{");
	const lastBrace = trimmed.lastIndexOf("}");
	if (firstBrace !== -1 && lastBrace > firstBrace) {
		return trimmed.slice(firstBrace, lastBrace + 1).trim();
	}
	return trimmed;
}

function parseJsonObject(text: string): Record<string, unknown> {
	const normalized = normalizeJsonText(text);
	let parsed: unknown;
	try {
		parsed = JSON.parse(normalized);
	} catch {
		parsed = parseStreamingJson(normalized);
	}
	if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
		throw new Error("Reflection output must be a JSON object");
	}
	const obj = parsed as Record<string, unknown>;
	if (Object.keys(obj).length === 0 && normalized.trim().length > 2) {
		throw new Error("Reflection output parsed to empty object from non-empty input");
	}
	return obj;
}

function asStringArray(value: unknown): string[] {
	if (typeof value === "string") {
		const text = value.trim();
		return text ? [text] : [];
	}
	if (!Array.isArray(value)) {
		return [];
	}
	return value
		.map((item) => String(item || "").trim())
		.filter((item) => item.length > 0);
}

function asObjectArray(value: unknown): Array<Record<string, unknown>> {
	if (!Array.isArray(value)) {
		return [];
	}
	return value
		.map((item) => ensureObject(item))
		.filter((item) => Object.keys(item).length > 0);
}

function uniqueSources(value: Array<Record<string, unknown>>): Array<Record<string, unknown>> {
	const seen = new Set<string>();
	const items: Array<Record<string, unknown>> = [];
	for (const item of value) {
		const marker = JSON.stringify(item);
		if (!seen.has(marker)) {
			seen.add(marker);
			items.push(item);
		}
	}
	return items;
}

function firstNonEmptyString(...values: Array<unknown>): string {
	for (const value of values) {
		const text = String(value || "").trim();
		if (text) return text;
	}
	return "";
}

function buildInjectedContextSection(job: ReflectionJob, inputPayload: Record<string, unknown>): string {
	const topLevelUserPayload = ensureObject(inputPayload["user_payload"]);
	const jobUserPayload = ensureObject(job.user_payload);
	const userPayload = Object.keys(topLevelUserPayload).length > 0 ? topLevelUserPayload : jobUserPayload;
	const injectedContext = ensureObject(
		Object.keys(ensureObject(userPayload["injected_context"])).length > 0
			? userPayload["injected_context"]
			: Object.keys(ensureObject(job.injected_context)).length > 0
				? job.injected_context
				: inputPayload["injected_context"],
	);
	if (Object.keys(injectedContext).length === 0 && Object.keys(userPayload).length === 0) {
		return "";
	}

	const fencedBlocks = ensureObject(injectedContext["fenced_blocks"]);
	const memoryBlock = firstNonEmptyString(fencedBlocks["memory"]);
	const hintBlock = firstNonEmptyString(fencedBlocks["hints"]);
	const contextBlock = firstNonEmptyString(injectedContext["context"]);
	const hardConstraints = asStringArray(injectedContext["hard_constraints"]);
	const contextSources = uniqueSources([
		...asObjectArray(job.context_sources),
		...asObjectArray(userPayload["context_sources"]),
		...asObjectArray(injectedContext["context_sources"]),
	]);
	const metadata = ensureObject(injectedContext["metadata"]);
	const sections: string[] = [
		"Use the following injected context as ephemeral background only.",
		"Do not treat it as canonical truth over the compact input. Never echo the fencing tags in the final answer.",
	];

	if (contextBlock) sections.push(contextBlock);
	if (memoryBlock) sections.push(memoryBlock);
	if (hintBlock) sections.push(hintBlock);
	if (hardConstraints.length > 0) {
		sections.push(["Hard constraints:", ...hardConstraints.map((item) => `- ${item}`)].join("\n"));
	}
	if (contextSources.length > 0) {
		sections.push(`Context source references:\n${JSON.stringify(contextSources, null, 2)}`);
	}
	if (Object.keys(metadata).length > 0) {
		sections.push(`Injected context metadata:\n${JSON.stringify(metadata, null, 2)}`);
	}
	return sections.filter((item) => item.trim().length > 0).join("\n\n");
}

function buildReflectionPrompt(job: ReflectionJob, inputPayload: Record<string, unknown>): string {
	const topLevelUserPayload = ensureObject(inputPayload["user_payload"]);
	const jobUserPayload = ensureObject(job.user_payload);
	const taskHint = firstNonEmptyString(topLevelUserPayload["prompt"], jobUserPayload["prompt"], job.prompt);
	const injectedContextSection = buildInjectedContextSection(job, inputPayload);
	return [
		"Return exactly one JSON object and nothing else.",
		"The JSON object must satisfy the following expected schema:",
		JSON.stringify(ensureObject(job.expected_output_schema), null, 2),
		"Use the following compact input as the source of truth:",
		JSON.stringify(ensureObject(job.compact_input), null, 2),
		taskHint ? `Task hint: ${taskHint}` : "",
		injectedContextSection,
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
		reasoning: String(metadata["reasoning_level"] || process.env.OT_PI_REFLECTION_REASONING || "low"),
	};
}

function resolvePositiveNumber(...values: Array<unknown>): number | undefined {
	for (const value of values) {
		const parsed = Number(String(value ?? "").trim());
		if (Number.isFinite(parsed) && parsed > 0) {
			return parsed;
		}
	}
	return undefined;
}

async function completeWithTimeout(
	model: Awaited<ReturnType<typeof resolveModel>>["model"],
	context: { systemPrompt?: string; messages: Array<Record<string, unknown>> },
	options: { apiKey?: string; headers?: Record<string, string>; reasoning?: string; maxTokens: number },
	requestTimeoutSeconds: number,
) {
	const timeoutMs = Math.round(requestTimeoutSeconds * 1000);
	const controller = new AbortController();
	const timeout = setTimeout(
		() => controller.abort(new Error(`Pi reflection request timed out after ${requestTimeoutSeconds}s`)),
		timeoutMs,
	);
	return completeSimple(
		model,
		context,
		options.reasoning
			? {
					apiKey: options.apiKey,
					headers: options.headers,
					reasoning: options.reasoning as "minimal" | "low" | "medium" | "high" | "xhigh",
					maxTokens: options.maxTokens,
					signal: controller.signal,
				}
			: {
					apiKey: options.apiKey,
					headers: options.headers,
					maxTokens: options.maxTokens,
					signal: controller.signal,
				},
	).finally(() => clearTimeout(timeout));
}

function normalizeReflectionFailureType(value: unknown): ReflectionFailureType | undefined {
	const text = String(value || "").trim();
	if (!text) return undefined;
	if (
		text === "runtime_abort" ||
		text === "runtime_timeout" ||
		text === "provider_unavailable" ||
		text === "empty_output" ||
		text === "json_parse_failed" ||
		text === "schema_rejected" ||
		text === "generic_rejected"
	) {
		return text;
	}
	return undefined;
}

function classifyReflectionFailureType(
	message: string,
	{
		stage,
		stopReason,
		hasRawText,
		rawOutput,
	}: {
		stage?: ReflectionFailureType;
		stopReason?: string;
		hasRawText?: boolean;
		rawOutput?: Record<string, unknown>;
	} = {},
): ReflectionFailureType {
	const explicit = normalizeReflectionFailureType(stage ?? rawOutput?.failure_type);
	if (explicit) return explicit;
	const lowered = String(message || "").toLowerCase();
	const stop = String(stopReason || "").toLowerCase();
	if (stage === "json_parse_failed") return "json_parse_failed";
	if (stage === "schema_rejected") return "schema_rejected";
	if (stop === "aborted" || stop === "cancelled" || stop === "canceled") return "runtime_abort";
	if (lowered.includes("timed out") || lowered.includes("timeout")) return "runtime_timeout";
	if (lowered.includes("api key") || lowered.includes("unauthorized") || lowered.includes("forbidden") || lowered.includes("provider unavailable")) {
		return "provider_unavailable";
	}
	if (!hasRawText || lowered.includes("no extractable content") || lowered.includes("empty output") || lowered.includes("no output")) {
		return "empty_output";
	}
	if (lowered.includes("json") && (lowered.includes("parse") || lowered.includes("valid json"))) return "json_parse_failed";
	if (lowered.includes("schema") || lowered.includes("required field") || lowered.includes("must include")) return "schema_rejected";
	if (stop === "error" || stop === "aborted") return "runtime_abort";
	return "generic_rejected";
}

function extractModelDetails(model: Awaited<ReturnType<typeof resolveModel>>["model"]): {
	provider: string;
	model_id: string;
	model: string;
	api: string | undefined;
} {
	return {
		provider: model.provider,
		model_id: model.id,
		model: `${model.provider}/${model.id}`,
		api: model.api,
	};
}

function extractAttemptRecords(value: unknown): Array<ReflectionAttemptRecord> {
	const attempts = asObjectArray(value);
	return attempts.map((item, index) => {
		const model = ensureObject(item.model);
		const provider = firstNonEmptyString(model.provider, item.provider);
		const modelId = firstNonEmptyString(model.model_id, model.id, item.model_id);
		return {
			attempt_index: Number(item.attempt_index ?? index + 1) || index + 1,
			provider,
			model_id: modelId,
			model: provider && modelId ? `${provider}/${modelId}` : firstNonEmptyString(item.model),
			api: firstNonEmptyString(model.api, item.api),
			failure_type: normalizeReflectionFailureType(item.failure_type),
			error: firstNonEmptyString(item.error),
			raw_text: firstNonEmptyString(item.raw_text, item.text),
			raw_text_salvaged: Boolean(item.raw_text || item.text),
			stop_reason: firstNonEmptyString(item.stop_reason, item.stopReason),
			content_blocks: asObjectArray(item.content_blocks),
		};
	});
}

function validateReflectionSchema(expectedSchema: Record<string, unknown>, output: Record<string, unknown>): string | undefined {
	if (!expectedSchema || Object.keys(expectedSchema).length === 0) return undefined;
	try {
		const validate = reflectionAjv.compile(expectedSchema);
		if (validate(output)) return undefined;
		return reflectionAjv.errorsText(validate.errors, { separator: "; " });
	} catch (error) {
		return String(error instanceof Error ? error.message : error);
	}
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

	let normalizedOutput: Record<string, unknown> = {};
	let rawOutput: Record<string, unknown> = {};
	let reviewBackend = "pi-reflection-runtime";
	let providerIds: string[] = [];
	let selectedModel: Awaited<ReturnType<typeof resolveModel>>["model"] | undefined;
	let attempts: ReflectionAttemptRecord[] = [];
	let finalFailureType: ReflectionFailureType | undefined;

	try {
		const mockResponse = ensureObject(ensureObject(job.metadata)["mock_response"]);
		if (Object.keys(mockResponse).length > 0) {
			normalizedOutput = mockResponse;
			reviewBackend = "pi-reflection-mock";
			providerIds = ["mock"];
			attempts = [
				{
					attempt_index: 1,
					provider: "mock",
					model_id: "mock",
					model: "mock/mock",
					api: "mock",
					raw_text: JSON.stringify(mockResponse, null, 2),
					raw_text_salvaged: true,
				},
			];
			rawOutput = {
				text: JSON.stringify(mockResponse, null, 2),
				raw_text: JSON.stringify(mockResponse, null, 2),
				stop_reason: "mock",
				content_blocks: [{ type: "mock" }],
				model: {
					provider: "mock",
					model_id: "mock",
					api: "mock",
				},
				attempts,
				failure_type: null,
				raw_text_salvaged: true,
			};
		} else {
			const systemPrompt = String(job.system_prompt || "").trim();
			const promptText = buildReflectionPrompt(job, inputPayload);
			const resolvedModel = await resolveModel(job);
			const modelDetails = extractModelDetails(resolvedModel.model);
			const requestTimeoutSeconds =
				resolvePositiveNumber(
					metadata["reflection_request_timeout_seconds"],
					ensureObject(job.metadata)["reflection_request_timeout_seconds"],
					process.env.OT_PI_REFLECTION_REQUEST_TIMEOUT_SECONDS,
					process.env.OT_PI_REFLECTION_TIMEOUT_SECONDS,
					90,
				) ?? 90;
			const maxTokens =
				resolvePositiveNumber(
					metadata["reflection_max_tokens"],
					ensureObject(job.metadata)["reflection_max_tokens"],
					process.env.OT_PI_REFLECTION_MAX_TOKENS,
					3000,
				) ?? 3000;
			const expectedSchema = ensureObject(job.expected_output_schema);
			try {
				const response = await completeWithTimeout(
					resolvedModel.model,
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
					resolvedModel.model.reasoning
						? {
								apiKey: resolvedModel.apiKey,
								headers: resolvedModel.headers,
								reasoning: resolvedModel.reasoning,
								maxTokens,
							}
						: {
								apiKey: resolvedModel.apiKey,
								headers: resolvedModel.headers,
								maxTokens,
							},
					requestTimeoutSeconds,
				);
				const stopReason = response.stopReason;
				const responseText = extractTextContent(response);
				const responseSummary = summarizeContentBlocks(response);
				if (stopReason === "error" || stopReason === "aborted") {
					const errorMessage = response.errorMessage || "Pi reflection completion failed";
					const failureType = classifyReflectionFailureType(errorMessage, { stopReason, rawOutput: { ...modelDetails } });
					attempts.push({
						attempt_index: 1,
						...modelDetails,
						failure_type: failureType,
						error: errorMessage,
						raw_text: responseText || undefined,
						raw_text_salvaged: Boolean(responseText),
						stop_reason: stopReason,
						content_blocks: responseSummary,
					});
					finalFailureType = failureType;
				} else if (!responseText) {
					const failureType = "empty_output";
					const errorMessage = `Pi reflection returned no extractable content (stopReason=${stopReason}; blocks=${JSON.stringify(responseSummary)})`;
					attempts.push({
						attempt_index: 1,
						...modelDetails,
						failure_type: failureType,
						error: errorMessage,
						raw_text_salvaged: false,
						stop_reason: stopReason,
						content_blocks: responseSummary,
					});
					finalFailureType = failureType;
				} else {
					const rawText = responseText;
					const parsed = parseJsonObject(rawText);
					const schemaError = validateReflectionSchema(expectedSchema, parsed);
					if (schemaError) {
						const failureType = "schema_rejected";
						attempts.push({
							attempt_index: 1,
							...modelDetails,
							failure_type: failureType,
							error: schemaError,
							raw_text: rawText,
							raw_text_salvaged: true,
							stop_reason: stopReason,
							content_blocks: responseSummary,
						});
						finalFailureType = failureType;
						rawOutput = {
							text: rawText,
							raw_text: rawText,
							raw_text_salvaged: true,
							stop_reason: stopReason,
							content_blocks: responseSummary,
							model: modelDetails,
							failure_type: failureType,
							error: schemaError,
						};
					} else {
						normalizedOutput = parsed;
						rawOutput = {
							text: rawText,
							raw_text: rawText,
							raw_text_salvaged: true,
							stop_reason: stopReason,
							content_blocks: responseSummary,
							model: modelDetails,
							failure_type: null,
						};
						attempts.push({
							attempt_index: 1,
							...modelDetails,
							raw_text: rawText,
							raw_text_salvaged: true,
							stop_reason: stopReason,
							content_blocks: responseSummary,
						});
						reviewBackend = `pi-reflection-agent:${resolvedModel.model.provider}/${resolvedModel.model.id}`;
						providerIds = [resolvedModel.model.provider];
						selectedModel = resolvedModel.model;
						finalFailureType = undefined;
					}
				}
			} catch (error) {
				const errorMessage = String(error instanceof Error ? error.message : error);
				const failureType = classifyReflectionFailureType(errorMessage, { stage: "generic_rejected" });
				attempts.push({
					attempt_index: 1,
					...modelDetails,
					failure_type: failureType,
					error: errorMessage,
					raw_text_salvaged: false,
				});
				finalFailureType = failureType;
			}

			if (!selectedModel && Object.keys(normalizedOutput).length === 0) {
				rawOutput = {
					error: attempts[attempts.length - 1]?.error || "Pi reflection output was rejected",
					failure_type: finalFailureType || "generic_rejected",
					raw_text: attempts[attempts.length - 1]?.raw_text || null,
					raw_text_salvaged: Boolean(attempts[attempts.length - 1]?.raw_text_salvaged),
					attempts,
				};
				finalFailureType = finalFailureType || "generic_rejected";
			}
		}

		const isFailure = finalFailureType !== undefined || (attempts.length > 0 && !selectedModel && Object.keys(normalizedOutput).length === 0 && reviewBackend !== "pi-reflection-mock");
		const summary =
			firstNonEmptyString(
				normalizedOutput["reasoning"],
				normalizedOutput["summary"],
				ensureObject(ensureObject(normalizedOutput)["review"])["reasoning"],
				ensureObject(ensureObject(normalizedOutput)["profile"])["summary"],
			) ||
			String(rawOutput["error"] || "") ||
			(isFailure ? "Pi reflection review failed." : "Pi reflection review completed.");

		if (isFailure && reviewBackend !== "pi-reflection-mock") {
			const failurePayload = {
				status: "failed",
				summary,
				failure_type: finalFailureType || "generic_rejected",
				review_backend: reviewBackend,
				provider: selectedModel?.provider || attempts[attempts.length - 1]?.provider,
				model_id: selectedModel?.id || attempts[attempts.length - 1]?.model_id,
				model:
					selectedModel && `${selectedModel.provider}/${selectedModel.id}`
						? `${selectedModel.provider}/${selectedModel.id}`
						: attempts[attempts.length - 1]?.model,
				raw_output: rawOutput,
				normalized_output: normalizedOutput,
				raw_text: rawOutput["raw_text"] || null,
				raw_text_salvaged: Boolean(rawOutput["raw_text"]),
				attempts,
				runtime: {
					ok: false,
					status: "failed",
				},
			};
			await writeJson(rawPath, failurePayload);
			await writeJson(normalizedPath, normalizedOutput);
			events.push(
				runtimeEvent("message_end", summary, {
					status: "failed",
					summary,
					metadata: {
						session_id: sessionId,
						invocation_id: invocationId,
						review_backend: reviewBackend,
						failure_type: failurePayload.failure_type,
					},
				}),
				runtimeEvent("turn_end", "Pi reflection invocation failed", {
					status: "failed",
					summary,
					metadata: {
						session_id: sessionId,
						invocation_id: invocationId,
						run_id: runId,
						review_backend: reviewBackend,
						failure_type: failurePayload.failure_type,
					},
				}),
				runtimeEvent("error", "Pi reflection session failed", {
					status: "failed",
					summary,
					metadata: {
						session_id: sessionId,
						runtime_id: runtimeId,
						run_id: runId,
						review_backend: reviewBackend,
						failure_type: failurePayload.failure_type,
					},
				}),
			);
			return {
				ok: false,
				status: "failed",
				summary,
				output: {
					summary,
					review_backend: reviewBackend,
					failure_type: failurePayload.failure_type,
					normalized_output: normalizedOutput,
					raw_output: rawOutput,
					attempts,
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
						payload: failurePayload,
						metadata: {
							runtime_id: runtimeId,
							session_id: sessionId,
							invocation_id: invocationId,
							failure_type: failurePayload.failure_type,
						},
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
					failure_type: failurePayload.failure_type,
					provider: failurePayload.provider,
					model_id: failurePayload.model_id,
					model: failurePayload.model,
					raw_text_salvaged: Boolean(failurePayload.raw_text),
					attempts,
				},
				provider_ids: attempts.map((item) => item.provider).filter((item): item is string => Boolean(item)),
				skill_ids: [],
			};
		}

		if (reviewBackend === "pi-reflection-mock") {
			rawOutput = {
				...rawOutput,
				review_backend: reviewBackend,
				attempts,
				failure_type: null,
			};
		}

		await writeJson(rawPath, {
			status: "succeeded",
			summary,
			review_backend: reviewBackend,
			raw_output: rawOutput,
			normalized_output: normalizedOutput,
			attempts,
			failure_type: null,
			provider: selectedModel?.provider || attempts[0]?.provider,
			model_id: selectedModel?.id || attempts[0]?.model_id,
			model: selectedModel ? `${selectedModel.provider}/${selectedModel.id}` : attempts[0]?.model,
			raw_text: rawOutput["raw_text"] || null,
			raw_text_salvaged: Boolean(rawOutput["raw_text"]),
		});
		await writeJson(normalizedPath, normalizedOutput);
		events.push(
			runtimeEvent("message_end", summary, {
				status: "succeeded",
				summary,
				metadata: {
					session_id: sessionId,
					invocation_id: invocationId,
					review_backend: reviewBackend,
					failure_type: null,
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
					failure_type: null,
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
					failure_type: null,
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
				attempts,
				failure_type: null,
				provider: selectedModel?.provider || attempts[0]?.provider,
				model_id: selectedModel?.id || attempts[0]?.model_id,
				model: selectedModel ? `${selectedModel.provider}/${selectedModel.id}` : attempts[0]?.model,
				raw_text: rawOutput["raw_text"] || null,
				raw_text_salvaged: Boolean(rawOutput["raw_text"]),
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
					payload: {
						...rawOutput,
						attempts,
						failure_type: null,
					},
					metadata: {
						runtime_id: runtimeId,
						session_id: sessionId,
						invocation_id: invocationId,
						failure_type: null,
					},
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
				failure_type: null,
				provider: selectedModel?.provider || attempts[0]?.provider,
				model_id: selectedModel?.id || attempts[0]?.model_id,
				model: selectedModel ? `${selectedModel.provider}/${selectedModel.id}` : attempts[0]?.model,
				raw_text_salvaged: Boolean(rawOutput["raw_text"]),
				attempts,
			},
			provider_ids: providerIds.length > 0 ? providerIds : attempts.map((item) => item.provider).filter((item): item is string => Boolean(item)),
			skill_ids: [],
		};
	} catch (error) {
		const errorMessage = String(error instanceof Error ? error.message : error);
		const preservedText = typeof rawOutput?.text === "string" ? rawOutput.text : undefined;
		const failureType = classifyReflectionFailureType(errorMessage, { stage: "generic_rejected", hasRawText: Boolean(preservedText), rawOutput });
		await writeJson(rawPath, { error: errorMessage, raw_text: preservedText, failure_type: failureType, attempts });
		events.push(
			runtimeEvent("message_end", errorMessage, {
				status: "failed",
				summary: errorMessage,
				metadata: { session_id: sessionId, invocation_id: invocationId, failure_type: failureType },
			}),
			runtimeEvent("turn_end", "Pi reflection invocation failed", {
				status: "failed",
				summary: errorMessage,
				metadata: { session_id: sessionId, invocation_id: invocationId, run_id: runId, failure_type: failureType },
			}),
			runtimeEvent("error", "Pi reflection session failed", {
				status: "failed",
				summary: errorMessage,
				metadata: { session_id: sessionId, runtime_id: runtimeId, run_id: runId, failure_type: failureType },
			}),
		);
		return {
			ok: false,
			status: "failed",
			summary: errorMessage,
			output: {
				summary: errorMessage,
				review_backend: "pi-reflection-runtime",
				failure_type: failureType,
				normalized_output: {},
				raw_output: { error: errorMessage, raw_text: preservedText, failure_type: failureType, attempts },
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
					payload: { error: errorMessage, raw_text: preservedText, failure_type: failureType, attempts },
					metadata: { runtime_id: runtimeId, session_id: sessionId, invocation_id: invocationId, failure_type: failureType },
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
				failure_type: failureType,
				raw_text_salvaged: Boolean(preservedText),
				attempts,
			},
			provider_ids: [],
			skill_ids: [],
		};
	}
}
