# Documentation Index

This directory now has one documentation tree for the entire repository.

## Read Order

1. [../README.md](../README.md)
2. [../AGENT_QUICKSTART.md](../AGENT_QUICKSTART.md)
3. [../START_HERE.md](../START_HERE.md)
4. [../AGENTS.md](../AGENTS.md)
5. [architecture/system-overview.md](./architecture/system-overview.md)
6. [architecture/wallet-style-agent-reflection-flow.md](./architecture/wallet-style-agent-reflection-flow.md)
7. [product/platform-guide.md](./product/platform-guide.md)
8. [contracts/runtime-run-evaluation-schema.md](./contracts/runtime-run-evaluation-schema.md)
9. [contracts/workspace-discovery-api.md](./contracts/workspace-discovery-api.md)

## Sections

- `architecture/`: module boundaries, pipeline stages, runtime flow
- `product/`: operator-facing explanations and walkthroughs
- `contracts/`: runtime payloads, workspace discovery, field contracts
- `legacy/hackathon/`: archived public-facing hackathon docs kept for historical context

## Current Repository Contract

- single root entrypoint
- real-path onboarding from the repository root
- mock-backed verification only as a repository health check
- AVE as the data plane
- OnchainOS as the execution plane
- environment variables as the configuration boundary
- numbered document names were removed so grep and agent retrieval are easier
