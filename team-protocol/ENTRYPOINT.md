# Team Protocol Entrypoint

This is the repo-tracked entrypoint for Codex, Claude Code, and similar agents that need the agent-team optimization layer.

## When To Use This

Use `team-protocol/` when the task is about planning, optimizing, reviewing, or iterative research orchestration.

Do not replace the repository startup contract with this bundle. Keep using:

- `ot-enterprise` for runtime preparation, distillation, and execution-adjacent flows
- `team-protocol/` + `ot-team` for agent-team coordination flows

## Required Read Order

1. `team-protocol/manifest.json`
2. `team-protocol/roles/planner.md`
3. `team-protocol/roles/optimizer.md`
4. `team-protocol/roles/reviewer.md`
5. `team-protocol/workflows/autoresearch.workflow.yaml`
6. `team-protocol/modules/autoresearch.module.json`

## Agent Operating Rules

- Stay at the repository root.
- Treat `team-protocol/` as repo-tracked configuration, not generated output.
- Keep protocol edits aligned with the manifest references.
- Use the planner to frame work, the optimizer to improve it, and the reviewer to decide whether the loop should stop or continue.
- Escalate to normal repository docs when the task shifts from protocol work back to product/runtime work.

## `ot-team` CLI Discovery

If the `ot-team` CLI is available in your environment, use it from the repository root:

```bash
uv run ot-team doctor
uv run ot-team start autoresearch --workspace desk-alpha --skill my-skill --adapter codex
uv run ot-team handoff --session-id <session_id> --role planner
```

The stable contract in this repository is the tracked bundle directory `./team-protocol` plus the `autoresearch` workflow id.

## Handoff Boundary

When an agent finishes protocol work, summarize:

- the active workflow
- the current role state
- any reviewer gates still open
- whether follow-up should continue in `ot-team` or return to the normal `ot-enterprise` operator flow
