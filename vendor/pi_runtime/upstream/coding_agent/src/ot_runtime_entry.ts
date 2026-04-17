import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { runReflectionMode } from "./ot_reflection_mode.js";

type Payload = {
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

function parseArgs(argv: string[]) {
	const parsed: Record<string, string> = {};
	for (let index = 0; index < argv.length; index += 1) {
		const current = argv[index];
		if (!current.startsWith("--")) continue;
		const key = current.slice(2);
		const next = argv[index + 1];
		if (next && !next.startsWith("--")) {
			parsed[key] = next;
			index += 1;
			continue;
		}
		parsed[key] = "true";
	}
	return parsed;
}

async function readPayload(): Promise<Payload> {
	const args = parseArgs(process.argv.slice(2));
	if (args["payload-file"]) {
		const content = await fs.readFile(args["payload-file"], "utf8");
		return JSON.parse(content) as Payload;
	}
	const stdin = await new Promise<string>((resolve, reject) => {
		let buffer = "";
		process.stdin.setEncoding("utf8");
		process.stdin.on("data", (chunk) => {
			buffer += chunk;
		});
		process.stdin.on("end", () => resolve(buffer));
		process.stdin.on("error", reject);
	});
	if (!stdin.trim()) return {};
	return JSON.parse(stdin) as Payload;
}

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

function shouldRunReflection(payload: Payload): boolean {
	const metadata = ensureObject(payload.metadata);
	const inputPayload = ensureObject(payload.input_payload);
	const piMode = String(metadata["pi_mode"] || inputPayload["pi_mode"] || "").trim().toLowerCase();
	return piMode === "reflection" || Boolean(inputPayload["reflection_job"]);
}

async function runStubMode(payload: Payload) {
	const runId = String(payload.run_id || `run-${Date.now()}`);
	const sessionId = String(payload.session_id || `pi-session-${Date.now()}`);
	const invocationId = String(payload.invocation_id || `pi-invocation-${Date.now()}`);
	const runtimeId = String(payload.runtime_id || "pi");
	const cwd = path.resolve(String(payload.cwd || process.cwd()));
	const workspaceDir = path.resolve(String(payload.workspace_dir || path.join(cwd, ".ot-workspace")));
	const sessionWorkspace = path.resolve(String(payload.session_workspace || path.join(workspaceDir, "runtime-sessions", sessionId)));
	const prompt = String(payload.prompt || "").trim();
	const metadata = { ...(payload.metadata || {}) };
	const forceFail = Boolean(metadata["force_fail"]) || prompt.toLowerCase().startsWith("fail:");

	await fs.mkdir(sessionWorkspace, { recursive: true });
	const entries = await fs.readdir(cwd).catch(() => []);

	const reportSummary = forceFail
		? "Pi runtime simulated a failed run for QA coverage."
		: `Pi runtime executed prompt against ${path.basename(cwd)} with ${entries.length} visible entries.`;

	const reportJsonPath = path.join(sessionWorkspace, `${runId}.report.json`);
	const reportMdPath = path.join(sessionWorkspace, `${runId}.report.md`);

	const reportPayload = {
		runtime_id: runtimeId,
		run_id: runId,
		session_id: sessionId,
		invocation_id: invocationId,
		cwd,
		workspace_dir: workspaceDir,
		prompt,
		summary: reportSummary,
		visible_entries: entries.slice(0, 20),
		metadata,
	};

	await fs.writeFile(reportJsonPath, JSON.stringify(reportPayload, null, 2), "utf8");
	await fs.writeFile(
		reportMdPath,
		[
			"# Pi Runtime Run",
			"",
			`- Run ID: ${runId}`,
			`- Session ID: ${sessionId}`,
			`- Runtime: ${runtimeId}`,
			`- CWD: ${cwd}`,
			`- Prompt: ${prompt || "(empty)"}`,
			"",
			reportSummary,
		].join("\n"),
		"utf8",
	);

	const status = forceFail ? "failed" : "succeeded";
	const events = [
		runtimeEvent("agent_start", "Pi session started", {
			status: "running",
			metadata: { session_id: sessionId, runtime_id: runtimeId },
		}),
		runtimeEvent("turn_start", "Pi invocation started", {
			status: "running",
			metadata: { session_id: sessionId, invocation_id: invocationId },
		}),
		runtimeEvent("tool_execution_start", "Collecting workspace snapshot", {
			toolName: "workspace_snapshot",
			args: { cwd },
			status: "running",
			metadata: { provider_id: "filesystem", session_id: sessionId, invocation_id: invocationId },
		}),
		runtimeEvent("tool_execution_end", "Workspace snapshot collected", {
			toolName: "workspace_snapshot",
			args: { cwd },
			status: forceFail ? "partial" : "succeeded",
			result: { entry_count: entries.length, entries: entries.slice(0, 20) },
			metadata: { provider_id: "filesystem", session_id: sessionId, invocation_id: invocationId },
		}),
		runtimeEvent("message_end", reportSummary, {
			status,
			summary: reportSummary,
			metadata: { session_id: sessionId, invocation_id: invocationId },
		}),
		runtimeEvent("turn_end", forceFail ? "Pi invocation failed" : "Pi invocation finished", {
			status,
			summary: reportSummary,
			metadata: { session_id: sessionId, invocation_id: invocationId, run_id: runId },
		}),
		runtimeEvent(forceFail ? "error" : "agent_end", forceFail ? "Pi session failed" : "Pi session finished", {
			status,
			summary: reportSummary,
			metadata: { session_id: sessionId, runtime_id: runtimeId, run_id: runId },
		}),
	];

	const artifacts = [
		{
			artifact_id: `${runId}-report-json`,
			kind: "report.json",
			uri: reportJsonPath,
			label: "Pi runtime JSON report",
			content_type: "application/json",
			payload: reportPayload,
			metadata: { runtime_id: runtimeId, session_id: sessionId, invocation_id: invocationId },
		},
		{
			artifact_id: `${runId}-report-md`,
			kind: "report.md",
			uri: reportMdPath,
			label: "Pi runtime markdown report",
			content_type: "text/markdown",
			payload: { summary: reportSummary },
			metadata: { runtime_id: runtimeId, session_id: sessionId, invocation_id: invocationId },
		},
	];

	const output = {
		summary: reportSummary,
		cwd,
		workspace_dir: workspaceDir,
		visible_entry_count: entries.length,
		visible_entries: entries.slice(0, 20),
	};

	return {
		ok: !forceFail,
		status,
		summary: reportSummary,
		output,
		artifacts,
		events,
		metadata: {
			runtime_id: runtimeId,
			session_id: sessionId,
			invocation_id: invocationId,
			workspace_dir: workspaceDir,
			cwd,
		},
		provider_ids: ["filesystem"],
		skill_ids: [],
	};
}

async function main() {
	const payload = await readPayload();
	const result = shouldRunReflection(payload) ? await runReflectionMode(payload) : await runStubMode(payload);
	process.stdout.write(JSON.stringify(result, null, 2));
}

main().catch((error) => {
	process.stderr.write(String(error instanceof Error ? error.stack || error.message : error));
	process.exitCode = 1;
});
