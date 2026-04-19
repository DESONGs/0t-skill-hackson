# `0t team` / `0t-protocol` Guide

This guide is the public entrypoint for the repo-tracked `0t-protocol` bundle and the `0t team` operator flow.

## What To Reach For

Use the normal startup docs when you want to run or debug the product:

- [README.md](../../README.md)
- [START_HERE.md](../../START_HERE.md)
- [AGENTS.md](../../AGENTS.md)

Use this guide when you want a structured planner/optimizer/reviewer loop:

- [docs/architecture/agent-team-optimization.md](../architecture/agent-team-optimization.md)

## Suggested `0t team` Flow

From the repository root:

```bash
uv run 0t team doctor
uv run 0t team start autoresearch --workspace desk-alpha --skill my-skill --adapter codex --data-source-adapter ave --execution-adapter onchainos_cli
uv run 0t team status <session_id>
uv run 0t team review <session_id>
uv run 0t team approve <session_id> --variant <variant_id>
```

`0t team start` now creates the kernel session and, by default, runs it forward until it reaches one of these kernel-owned states:

- `awaiting_approval`
- `recommended`
- terminal failure

`0t team handoff` and `0t team submit-work` are only used when the kernel explicitly projects a `handoff_ready` work item for that session.

`0t team start` must resolve adapters explicitly. The supported sources are:

- CLI flags `--data-source-adapter` and `--execution-adapter`
- workspace config `.ot-workspace/workspaces/<workspace_id>/workflow-config.json`

The stable identifiers are:

- public bundle name: `0t-protocol`
- workflow id: `autoresearch`
- module id: `autoresearch`

## Operational Boundary

- `0t team` is a long-running multi-agent/operator facade over kernel-owned workflow state
- `0t` is for runtime and distillation execution
- `./scripts/doctor.sh` and `./scripts/verify.sh` remain the first-line repository health checks
