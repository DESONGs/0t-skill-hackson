# 0T-Skill

0T-Skill distills an on-chain wallet's trading behavior into a runnable skill package. The repository root is the only supported entrypoint.

If you want the shortest human path, start with [START_HERE.md](./START_HERE.md).  
If you want Codex or Claude Code to take over immediately, use [AGENT_QUICKSTART.md](./AGENT_QUICKSTART.md).
If you need the repo-tracked planner/optimizer/reviewer bundle for the new `0t-protocol` agent-team architecture, start with [the `0t-protocol` guide](./docs/product/0t-protocol-guide.md).

## Official Startup Paths

### 1. Host `uv` path

Use this when you want direct local debugging, direct file visibility, and the best agent editing experience.

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

### 2. Docker path

Use this when you want a more stable, more isolated startup path for weak local environments.

```bash
./scripts/doctor.sh
cp .env.example .env
./scripts/docker_build.sh
./scripts/docker_up.sh
./scripts/docker_cli.sh style distill --workspace-dir /app/.ot-workspace --wallet 0x... --chain bsc
```

If you also want local Postgres / Redis / MinIO:

```bash
./scripts/docker_up.sh --with-infra
```

## Default Workflow Path

The repository now defaults workflow orchestration to the `TS Pi kernel + Python domain workers` path. Use `0t workflow` for explicit workflow sessions, and note that `0t style distill` now follows the same kernel/worker chain by default:

```bash
uv run 0t workflow overview
uv run 0t workflow distillation-seed --wallet 0x... --chain bsc --skill-name desk-alpha
uv run 0t workflow autonomous-research --wallet 0x... --chain bsc --skill-name desk-alpha
```

Rollback is a feature flag, not a code-path swap:

```bash
OT_WORKFLOW_RUNTIME=python-compat uv run 0t workflow distillation-seed --wallet 0x... --chain bsc --skill-name desk-alpha
OT_WORKFLOW_RUNTIME=python-compat uv run 0t style distill --workspace-dir .ot-workspace --wallet 0x... --chain bsc
```

Use `OT_WORKFLOW_RUNTIME=python-compat` only when you are explicitly rolling back from the default `ts-kernel` runtime.

## Runtime Notes

- `.env.example` keeps the real provider path:
  - `AVE_DATA_PROVIDER=ave_rest`
  - `OT_PI_REFLECTION_MOCK=0`
  - `AVE_USE_DOCKER=true`
- Host `uv` mode defaults to real AVE + real Kimi.
- Docker app services override `AVE_USE_DOCKER=false` internally to avoid nested Docker.
- `./scripts/bootstrap.sh` still exists as a helper, but `uv` is now the primary contract.

## Agent-Team Protocol Bundle

The repository now also tracks a protocol bundle for the separate `0t team` coordination layer:

- public guide: [0t team / 0t-protocol guide](./docs/product/0t-protocol-guide.md)
- architecture note: [docs/architecture/agent-team-optimization.md](./docs/architecture/agent-team-optimization.md)

This does not replace the startup paths above.

- use `0t` and the scripts in this README for runtime preparation, serving, and distillation
- use `0t team` when the task is about agent-team planning, optimization, and review workflows
- `0t team` remains the long-running multi-agent/operator entrypoint, but kernel-owned workflow state now lives under `.ot-workspace/runtime-sessions/.../workflow-kernel`

Frontend default address:

- [http://127.0.0.1:8090](http://127.0.0.1:8090)

## Usage Modes

| Mode | Purpose | Extra setup |
|---|---|---|
| Real distillation | AVE-backed data + real reflection | Set `AVE_API_KEY`, `API_PLAN`, `KIMI_API_KEY` |
| Live execution | OKX OnchainOS execution flow | Add `OKX_*` credentials and a working `cargo` or `OT_ONCHAINOS_CLI_BIN` |
| Mock verification | Repository smoke checks and CI | Optional; used by `./scripts/verify.sh`, not the default user path |
| Local infra | Postgres / Redis / MinIO | Use `./scripts/start_stack.sh` or `./scripts/docker_up.sh --with-infra` |

## Repository Layout

```text
.
├── AGENTS.md                 # Canonical instructions for Codex, Claude Code, and similar agents
├── AGENT_QUICKSTART.md       # Copy-paste prompts for users opening the repo in an agent
├── START_HERE.md             # Shortest startup path for human operators
├── README.md                 # Human quick start and operating modes
├── CONFIGURATION.md          # Scenario-based environment guide
├── .env.example              # Real-path environment template
├── docs/                     # Architecture, contracts, product docs, archived hackathon docs
├── docker/                   # Dockerfiles for app and AVE bridge
├── scripts/                  # Doctor, startup, Docker helpers, verification
├── src/ot_skill_enterprise/  # Python control plane and business logic
├── services/                 # Local service implementations
├── frontend/                 # Static dashboard assets
├── skills/                   # Public fixture skills; local generated skills are ignored by git
├── vendor/                   # Vendored upstream code; read last
└── tests/                    # Regression and unit tests
```

The repo also includes the repo-tracked `0t-protocol` bundle alongside these top-level materials.

## Agent Integration

Start with [AGENTS.md](./AGENTS.md). For most agent systems:

1. `./scripts/doctor.sh`
2. `cp .env.example .env`
3. choose either `uv` or Docker
4. stay at the repository root

For the separate agent-team coordination layer, use [the `0t-protocol` guide](./docs/product/0t-protocol-guide.md) and point `0t team` at the repo-tracked `0t-protocol` bundle.

## Verification

```bash
./scripts/verify.sh
```

`verify.sh` intentionally uses mock-backed smoke coverage for repository health checks. It validates repository wiring before you debug real-provider behavior.

## Documentation

- [START_HERE.md](./START_HERE.md)
- [AGENT_QUICKSTART.md](./AGENT_QUICKSTART.md)
- [AGENTS.md](./AGENTS.md)
- [CONFIGURATION.md](./CONFIGURATION.md)
- [docs/README.md](./docs/README.md)
- [docs/architecture/next-architecture/README.md](./docs/architecture/next-architecture/README.md)
- [0t team / 0t-protocol guide](./docs/product/0t-protocol-guide.md)
- [docs/architecture/agent-team-optimization.md](./docs/architecture/agent-team-optimization.md)
- [docs/architecture/system-overview.md](./docs/architecture/system-overview.md)
- [docs/product/platform-guide.md](./docs/product/platform-guide.md)
- [docs/legacy/hackathon/README.md](./docs/legacy/hackathon/README.md)

## License

Apache License 2.0
