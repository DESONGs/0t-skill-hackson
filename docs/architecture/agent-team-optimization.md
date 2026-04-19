# Agent-Team Optimization Architecture

This repository now carries a repo-tracked `0t-protocol` bundle for the `0t team` coordination layer.

## Why It Exists

The main product path in this repository is still the `0t` runtime and distillation flow. The new protocol bundle adds a separate coordination layer for agent-team work:

- `planner` frames the task and declares acceptance criteria
- `optimizer` improves the active candidate against those criteria
- `reviewer` decides whether to stop, iterate, or escalate

That split keeps planning, optimization, and review decisions explicit instead of burying them inside a single agent prompt.

## Control Surface

- Runtime and product operations stay on the existing startup contracts in [README.md](../../README.md), [START_HERE.md](../../START_HERE.md), and [AGENTS.md](../../AGENTS.md).
- Agent-team coordination starts from the public [`0t-protocol` guide](../product/0t-protocol-guide.md).
- The bundle manifest is the machine-readable index for roles, workflows, and modules.

## Bundle Layout

```text
0t-protocol bundle
├── entrypoint
├── manifest
├── modules/
│   └── autoresearch.module.json
├── roles/
│   ├── optimizer.md
│   ├── planner.md
│   └── reviewer.md
└── workflows/
    └── autoresearch.workflow.yaml
```

## Default Workflow

`autoresearch` is the first bundled workflow. It is designed for tasks that need:

- an explicit plan before implementation or synthesis
- one or more bounded optimization passes
- a reviewer gate before the output is treated as decision-ready

The loop is intentionally capped so the system does not drift into unbounded refinement.

## CLI Positioning

The repository does not replace `0t` with `0t team`.

- use `0t` for runtime preparation, serving, distillation, and execution-related operator flows
- use `0t team` to load and run the repo-tracked `0t-protocol` bundle when the task is about multi-agent coordination

In other words, `0t team` is a protocol-layer entrypoint, not a startup-path replacement.
