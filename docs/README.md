# Documentation Index

This directory now has one documentation tree for the entire repository.

## Read Order

1. [../README.md](../README.md)
2. [../AGENT_QUICKSTART.md](../AGENT_QUICKSTART.md)
3. [../START_HERE.md](../START_HERE.md)
4. [../AGENTS.md](../AGENTS.md)
5. [0t team / `0t-protocol` guide](./product/0t-protocol-guide.md)
6. [architecture/system-overview.md](./architecture/system-overview.md)
7. [architecture/wallet-style-agent-reflection-flow.md](./architecture/wallet-style-agent-reflection-flow.md)
8. [architecture/agent-team-optimization.md](./architecture/agent-team-optimization.md)
9. [architecture/next-architecture/README.md](./architecture/next-architecture/README.md)
10. [product/platform-guide.md](./product/platform-guide.md)
11. [contracts/runtime-run-evaluation-schema.md](./contracts/runtime-run-evaluation-schema.md)
12. [contracts/workspace-discovery-api.md](./contracts/workspace-discovery-api.md)

## Linked Bundle And Sections

- the public `0t-protocol` entry: [0t team / 0t-protocol guide](./product/0t-protocol-guide.md)
- `architecture/`: module boundaries, pipeline stages, runtime flow
- `architecture/next-architecture/`: next-stage target architecture, plugin model, adapter SPI, migration phases, and team delivery plan
- `product/`: operator-facing explanations and walkthroughs
- `contracts/`: runtime payloads, workspace discovery, field contracts
- `legacy/hackathon/`: archived public-facing hackathon docs kept for historical context

## Next-Architecture Entry

The executable workflow surface now starts from:

- `uv run 0t workflow overview`
- `uv run 0t workflow distillation-seed ...`
- `uv run 0t workflow autonomous-research ...`

These commands are the default workflow runtime path. The host `uv` and Docker startup contracts in [../README.md](../README.md) and [../AGENTS.md](../AGENTS.md) stay the same, but workflow orchestration now defaults to `OT_WORKFLOW_RUNTIME=ts-kernel`. Rollback is controlled only by `OT_WORKFLOW_RUNTIME=python-compat`.

## Current Repository Contract

- single root entrypoint
- repo-tracked `0t-protocol` bundle for agent coordination work
- `0t team` as the multi-agent/operator facade; kernel-owned workflow state under `.ot-workspace/runtime-sessions/.../workflow-kernel`
- real-path onboarding from the repository root
- mock-backed verification only as a repository health check
- AVE as the data plane
- OnchainOS as the execution plane
- environment variables as the configuration boundary
- numbered document names were removed so grep and agent retrieval are easier

## Current Vs Next

This index now tracks two architecture views:

- current repository architecture
  - [architecture/system-overview.md](./architecture/system-overview.md)
  - [architecture/wallet-style-agent-reflection-flow.md](./architecture/wallet-style-agent-reflection-flow.md)
  - [architecture/agent-team-optimization.md](./architecture/agent-team-optimization.md)
- next-stage target architecture
  - [architecture/next-architecture/README.md](./architecture/next-architecture/README.md)

Read the current architecture first when you need to understand the repository as it runs today.  
Read the next-architecture package when the task is about restructuring, migration planning, plugin boundaries, or team delivery.
