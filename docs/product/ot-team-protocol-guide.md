# `ot-team` Protocol Bundle Guide

This guide is for operators and agents who need to discover the repo-tracked team protocol bundle without confusing it with the main runtime startup flow.

## What To Reach For

Use the normal startup docs when you want to run or debug the product:

- [README.md](../../README.md)
- [START_HERE.md](../../START_HERE.md)
- [AGENTS.md](../../AGENTS.md)

Use the protocol bundle when you want a structured planner/optimizer/reviewer loop:

- [team-protocol/ENTRYPOINT.md](../../team-protocol/ENTRYPOINT.md)
- [docs/architecture/agent-team-optimization.md](../architecture/agent-team-optimization.md)

## Suggested `ot-team` Flow

From the repository root:

```bash
uv run ot-team doctor
uv run ot-team start autoresearch --workspace desk-alpha --skill my-skill --adapter codex
uv run ot-team handoff --session-id <session_id> --role planner
```

The stable identifiers are:

- bundle dir: `./team-protocol`
- workflow id: `autoresearch`
- module id: `autoresearch`

## Operational Boundary

- `ot-team` is for coordination and protocol execution
- `ot-enterprise` is for runtime and distillation execution
- `./scripts/doctor.sh` and `./scripts/verify.sh` remain the first-line repository health checks
