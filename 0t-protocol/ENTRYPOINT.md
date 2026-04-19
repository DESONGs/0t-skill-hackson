# 0T Protocol Entrypoint

This is the repo-tracked entrypoint for Codex, Claude Code, and similar agents that need the 0T coordination layer.

## When To Use This

Use `0t-protocol/` when the task is about planning, optimizing, reviewing, or iterative research orchestration.

Do not replace the repository startup contract with this bundle. Keep using:

- `0t workflow` for runtime preparation, distillation, and execution-adjacent flows
- `0t-protocol/` + the current 0T coordination facade (`0t team`) for long-running review/optimization flows

## Required Read Order

1. `0t-protocol/manifest.json`
2. `0t-protocol/roles/planner.md`
3. `0t-protocol/roles/optimizer.md`
4. `0t-protocol/roles/reviewer.md`
5. `0t-protocol/workflows/autoresearch.workflow.yaml`
6. `0t-protocol/modules/autoresearch.module.json`

## Agent Operating Rules

- Stay at the repository root.
- Treat `0t-protocol/` as repo-tracked configuration, not generated output.
- Keep protocol edits aligned with the manifest references.
- Use the planner to frame work, the optimizer to improve it, and the reviewer to decide whether the loop should stop or continue.
- Escalate to normal repository docs when the task shifts from protocol work back to product/runtime work.

## 0T Facade Discovery

Use the current 0T facade executable (`0t team`) from the repository root:

```bash
uv run 0t team doctor
uv run 0t team start autoresearch --workspace desk-alpha --skill my-skill --adapter codex --data-source-adapter ave --execution-adapter onchainos_cli
uv run 0t team status <session_id>
uv run 0t team review <session_id>
uv run 0t team approve <session_id> --variant <variant_id>
```

The current 0T facade start command requires adapter selection from one of two sources:

- explicit `--data-source-adapter` / `--execution-adapter`
- workspace config at `.ot-workspace/workspaces/<workspace_id>/workflow-config.json`

The current 0T facade start command creates a kernel session and lets the TS kernel advance the workflow until the session reaches `awaiting_approval`, `recommended`, or terminal failure. Use handoff/submit only when the kernel explicitly exposes a `handoff_ready` work item.

The stable contract in this repository is the tracked bundle directory `./0t-protocol` plus the `autoresearch` workflow id.

## Handoff Boundary

When an agent finishes protocol work, summarize:

- the active workflow
- the current role state
- any reviewer gates still open
- whether follow-up should continue in the 0T facade or return to the normal `0t workflow` operator flow
