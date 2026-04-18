# Documentation Index

This directory now has one documentation tree for the entire repository.

## Read Order

1. [../README.md](../README.md)
2. [../AGENT_QUICKSTART.md](../AGENT_QUICKSTART.md)
3. [../START_HERE.md](../START_HERE.md)
4. [../AGENTS.md](../AGENTS.md)
5. [../team-protocol/ENTRYPOINT.md](../team-protocol/ENTRYPOINT.md)
6. [architecture/agent-team-optimization.md](./architecture/agent-team-optimization.md)
7. [product/ot-team-protocol-guide.md](./product/ot-team-protocol-guide.md)
8. [architecture/system-overview.md](./architecture/system-overview.md)
9. [architecture/wallet-style-agent-reflection-flow.md](./architecture/wallet-style-agent-reflection-flow.md)
10. [product/platform-guide.md](./product/platform-guide.md)
11. [contracts/runtime-run-evaluation-schema.md](./contracts/runtime-run-evaluation-schema.md)
12. [contracts/workspace-discovery-api.md](./contracts/workspace-discovery-api.md)

## Linked Bundle And Sections

- `../team-protocol/`: repo-tracked bundle for the `ot-team` planner/optimizer/reviewer architecture
- `architecture/`: module boundaries, pipeline stages, runtime flow
- `product/`: operator-facing explanations and walkthroughs
- `contracts/`: runtime payloads, workspace discovery, field contracts
- `legacy/hackathon/`: archived public-facing hackathon docs kept for historical context

## Current Repository Contract

- single root entrypoint
- repo-tracked `ot-team` bundle for agent coordination work
- real-path onboarding from the repository root
- mock-backed verification only as a repository health check
- AVE as the data plane
- OnchainOS as the execution plane
- environment variables as the configuration boundary
- numbered document names were removed so grep and agent retrieval are easier
