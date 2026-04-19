# Agent Execution Contract

This file is the task-specific execution contract for long-running multi-agent work in this repository.

Canonical repository onboarding and runtime instructions still live in [AGENTS.md](./AGENTS.md).  
Use this file when the task is specifically about the next-stage architecture migration described in:

- [docs/architecture/next-architecture/target-system-blueprint.md](./docs/architecture/next-architecture/target-system-blueprint.md)
- [docs/architecture/next-architecture/kernel-and-stack-boundary.md](./docs/architecture/next-architecture/kernel-and-stack-boundary.md)
- [docs/architecture/next-architecture/plugin-workflow-model.md](./docs/architecture/next-architecture/plugin-workflow-model.md)
- [docs/architecture/next-architecture/data-and-execution-adapters.md](./docs/architecture/next-architecture/data-and-execution-adapters.md)
- [docs/architecture/next-architecture/migration-phases.md](./docs/architecture/next-architecture/migration-phases.md)
- [docs/architecture/next-architecture/team-delivery-plan.md](./docs/architecture/next-architecture/team-delivery-plan.md)

## Mission

Implement the first executable iteration of the migration path from:

- `Python main system + TS sub-runtime`

to:

- `TS Pi kernel + Python domain workers`

This iteration does **not** attempt the full migration. It must only land the first stable scaffolding that future parallel work can build on safely.

## Iteration Scope

This round should implement only the following:

1. a machine-readable workflow/plugin surface for:
   - `distillation`
   - `autoresearch`
   - `review`
   - `benchmark`
2. a machine-readable adapter SPI surface for:
   - `DataSourceAdapter`
   - `ExecutionAdapter`
3. first-party wrappers that register the current runtime dependencies as adapters:
   - AVE as the first data-source adapter
   - OnchainOS / OKX as the first execution adapter
4. an introspection surface that lets operators and future agents inspect the new scaffolding
5. regression tests for the new scaffolding

This round must **not**:

- rewrite the current distillation pipeline
- remove the current legacy CLI path
- replace the current team façade path with the unified `0t` surface
- change live execution semantics
- force a TS rewrite of existing Python business logic

## Delivery Principle

The goal is to make the future architecture executable in small steps.

This means:

- contracts first
- registries second
- wrappers third
- integration fourth
- QA last

## Agent-Team Structure

### Main Agent

The main agent is responsible for:

- reading the current repository state
- choosing the first implementation slice
- assigning disjoint write scopes to subagents
- integrating their work
- preserving repository coherence
- deciding the final QA scope

The main agent should own:

- root-level task coordination
- `agent.md`
- final wiring changes
- CLI integration
- final review before QA

### Subagent A: Workflow / Plugin Surface

Owns only:

- plugin contract models
- workflow graph models
- plugin registry
- built-in plugin manifests or machine-readable specs
- plugin-surface tests

Must not edit:

- adapter files
- CLI wiring owned elsewhere

### Subagent B: Adapter SPI Surface

Owns only:

- data-source adapter contracts
- execution adapter contracts
- adapter registry
- AVE wrapper
- OnchainOS wrapper
- adapter-surface tests

Must not edit:

- plugin files
- CLI wiring owned elsewhere

### QA Subagent

Runs after integration only.

Owns only:

- verification
- failure reporting
- suggested follow-up fixes if needed

The QA subagent must not perform broad refactors while validating.

## File Ownership Rule

Each subagent gets a disjoint write set.  
No subagent may revert or rewrite files owned by another subagent unless explicitly reassigned by the main agent.

## Coding Rule For This Migration Slice

Prefer additive changes.

The new architecture scaffolding should live beside the current system, not replace it in one pass.

Recommended pattern:

- create a new isolated package for next-stage architecture contracts and registries
- wrap current implementations instead of rewriting them
- expose a small inspection command instead of changing runtime defaults

## Acceptance Criteria

This iteration is complete only if all of the following are true:

1. the repository contains a concrete plugin/workflow registry surface
2. the repository contains a concrete adapter SPI surface
3. AVE and OnchainOS are visible through the new adapter registry
4. the new scaffolding is inspectable through code and CLI
5. tests cover the new registries and wrappers
6. existing startup contracts in [AGENTS.md](./AGENTS.md) remain intact

## Verification Contract

Before claiming completion, run targeted tests for the new scaffolding plus the repository verification path when feasible.

At minimum:

- targeted pytest coverage for the new iteration
- existing smoke verification if the new code touches repository wiring

## Compatibility Rule

If there is any conflict between this file and [AGENTS.md](./AGENTS.md):

- follow `AGENTS.md` for repository/runtime behavior
- follow `agent.md` for long-task execution structure and subagent orchestration
