# 0T-Skill Agent Guide

This is the canonical orientation file for Codex, Claude Code, and similar repository agents.

For the repo-tracked planner/optimizer/reviewer bundle, start with [the `0t-protocol` entrypoint](./0t-protocol/ENTRYPOINT.md). Use that bundle for `0t team` coordination work, but keep the startup and runtime contract in this file unchanged.

## Root Contract

- The repository root is the only supported working directory.
- Do not look for a nested application root.
- Preserve the real startup path unless the task explicitly calls for mock verification.
- Prefer the host `uv` path for agent-driven editing and debugging.

## Official Startup Contracts

### Host `uv` path

```bash
./scripts/doctor.sh
cp .env.example .env
# Fill in AVE_API_KEY, API_PLAN, KIMI_API_KEY
uv sync --frozen
uv run 0t runtime prepare --workspace-dir .ot-workspace
./scripts/start_ave_data_service.sh
./scripts/start_frontend.sh
uv run 0t style distill --workspace-dir .ot-workspace --wallet 0x... --chain bsc
```

### Docker path

```bash
./scripts/doctor.sh
cp .env.example .env
./scripts/docker_build.sh
./scripts/docker_up.sh
./scripts/docker_cli.sh style distill --workspace-dir /app/.ot-workspace --wallet 0x... --chain bsc
```

Add `--with-infra` to `./scripts/docker_up.sh` when the task needs local Postgres / Redis / MinIO.

## Default Operating Mode

`.env.example` is intentionally real-first:

- `AVE_DATA_PROVIDER=ave_rest`
- `OT_PI_REFLECTION_MOCK=0`
- `AVE_USE_DOCKER=true`

Host `uv` mode uses the real AVE and real Kimi path.  
Docker app services override `AVE_USE_DOCKER=false` internally so they do not try to launch nested Docker.

`./scripts/bootstrap.sh` still exists, but it is only a helper around `uv sync` + `runtime prepare`.

## Repository Map

- `AGENT_QUICKSTART.md`
  - user-facing copy-paste prompts for handing the repo to Codex or Claude Code
- `0t-protocol` bundle
  - repo-tracked protocol bundle with the entrypoint, manifest, roles, workflows, and modules for the `0t team` coordination layer
- `START_HERE.md`
  - shortest operator startup path for humans and thin agents
- `README.md`
  - human quick start and operating modes
- `CONFIGURATION.md`
  - scenario-based environment guide
- `docs/`
  - architecture, product, contract docs, plus archived hackathon docs
- `docker/`
  - Dockerfiles for the app image and AVE bridge image
- `scripts/`
  - doctor, bootstrap helper, Docker helpers, service start, verification
- `src/ot_skill_enterprise/`
  - control plane, runtime integration, style distillation, storage, skill compilation
- `services/`
  - local data service implementations
- `frontend/`
  - static dashboard assets served by the Python frontend server
- `skills/`
  - public fixture skills and promoted skill packages
- `vendor/`
  - vendored runtime and upstream code; read only when needed
- `tests/`
  - regression and unit coverage

## Read Order For Agents

1. `README.md`
2. `AGENT_QUICKSTART.md`
3. `START_HERE.md`
4. `CONFIGURATION.md`
5. [the `0t-protocol` entrypoint](./0t-protocol/ENTRYPOINT.md) when the task involves `0t-protocol` agent-team optimization work
6. `docs/README.md`
7. `src/ot_skill_enterprise/README.md`
8. only then dive into implementation modules

## Where To Spend Time

Focus on these first:

- `scripts/`
- `src/ot_skill_enterprise/control_plane/`
- `src/ot_skill_enterprise/style_distillation/`
- `services/ave-data-service/`
- `tests/`

Avoid spending time in `vendor/` unless the task is explicitly about vendored runtime behavior.

## Agent-Team Optimization Path

When the task is about multi-agent planning, optimization, or review loops:

- enter through [the `0t-protocol` entrypoint](./0t-protocol/ENTRYPOINT.md)
- use [the `0t-protocol` manifest](./0t-protocol/manifest.json) as the machine-readable source of truth
- keep `0t team` work separate from `0t` runtime startup work
- return to the normal startup docs when the task moves back to runtime, distillation, or execution

## Workflow Runtime Default

- `0t workflow ...` uses `OT_WORKFLOW_RUNTIME=ts-kernel` by default.
- `0t style distill` follows the same TS-kernel workflow path unless the command is in a live-execution mode.
- The only supported rollback flag is:

```bash
OT_WORKFLOW_RUNTIME=python-compat
```

- Do not describe `0t workflow` as an additive migration path anymore. It is the default workflow runtime surface.

## Data And Execution Boundaries

- AVE is the data plane for distillation and market context.
- OKX OnchainOS is the execution plane.
- Do not mix execution concerns into distillation-only code paths.
- Live execution remains a host-oriented path; do not move it into the main Docker app path.

## Verification Contract

Use these commands before claiming the repository is broken:

```bash
./scripts/doctor.sh
./scripts/verify.sh
```

`verify.sh` uses mock-backed smoke coverage by design. If it fails, fix the local environment or repository wiring before attempting real credentials or execution work.

## Switching To Mock Verification

Only switch when the task requires mock verification:

```bash
AVE_DATA_PROVIDER=mock
OT_PI_REFLECTION_MOCK=1
AVE_USE_DOCKER=false
```

For live execution on the real path, add:

```bash
OKX_API_KEY=...
OKX_SECRET_KEY=...
OKX_PASSPHRASE=...
```
