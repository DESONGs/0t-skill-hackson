# 0T-Skill

0T-Skill distills an on-chain wallet's trading behavior into a runnable skill package. The repository root is the only supported entrypoint.

If you want the shortest human path, start with [START_HERE.md](./START_HERE.md).  
If you want Codex or Claude Code to take over immediately, use [AGENT_QUICKSTART.md](./AGENT_QUICKSTART.md).

## Official Startup Paths

### 1. Host `uv` path

Use this when you want direct local debugging, direct file visibility, and the best agent editing experience.

```bash
./scripts/doctor.sh
cp .env.example .env
# Fill in AVE_API_KEY, API_PLAN, KIMI_API_KEY
uv sync --frozen
uv run ot-enterprise runtime prepare --workspace-dir .ot-workspace
uv run ot-serve-ave-data
uv run ot-frontend
uv run ot-enterprise style distill --workspace-dir .ot-workspace --wallet 0x... --chain bsc
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

## Runtime Notes

- `.env.example` keeps the real provider path:
  - `AVE_DATA_PROVIDER=ave_rest`
  - `OT_PI_REFLECTION_MOCK=0`
  - `AVE_USE_DOCKER=true`
- Host `uv` mode defaults to real AVE + real Kimi.
- Docker app services override `AVE_USE_DOCKER=false` internally to avoid nested Docker.
- `./scripts/bootstrap.sh` still exists as a compatibility wrapper, but `uv` is now the primary contract.

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

## Agent Integration

Start with [AGENTS.md](./AGENTS.md). For most agent systems:

1. `./scripts/doctor.sh`
2. `cp .env.example .env`
3. choose either `uv` or Docker
4. stay at the repository root

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
- [docs/architecture/system-overview.md](./docs/architecture/system-overview.md)
- [docs/product/platform-guide.md](./docs/product/platform-guide.md)
- [docs/legacy/hackathon/README.md](./docs/legacy/hackathon/README.md)

## License

Apache License 2.0
