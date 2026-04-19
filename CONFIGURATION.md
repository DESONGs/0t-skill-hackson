# Configuration Guide

The repository keeps the real startup path as the default. Copy `.env.example` to `.env`, fill the required real-provider variables, and use mock mode only when you explicitly want a smoke check or offline validation.

## Startup Contract

### Host `uv` path

```bash
./scripts/doctor.sh
cp .env.example .env
uv sync --frozen
uv run 0t runtime prepare --workspace-dir .ot-workspace
```

### Docker path

```bash
./scripts/doctor.sh
cp .env.example .env
./scripts/docker_build.sh
./scripts/docker_up.sh
```

Everything runs from the repository root.

## Environment Modes

| Mode | Required tools | Required variables | Default in `.env.example` |
|---|---|---|---|
| Real distillation | Python 3.11+, `uv`, Node 20+, npm | `AVE_API_KEY`, `API_PLAN`, `KIMI_API_KEY` | Yes |
| Docker app path | Docker / Docker Compose | `AVE_API_KEY`, `API_PLAN`, `KIMI_API_KEY` | Supported |
| Live execution | Python 3.11+, `uv`, Node 20+, npm, Cargo or `OT_ONCHAINOS_CLI_BIN` | Real distillation vars plus `OKX_API_KEY`, `OKX_SECRET_KEY`, `OKX_PASSPHRASE` | No |
| Mock verification | Python 3.11+, Node 20+, npm | None beyond the repo defaults | No |
| Local infra | Docker / Docker Compose | `OT_START_LOCAL_STACK=1` or explicit storage env vars | Optional |

## Recommended Values

### Real distillation default

Keep these values:

```bash
AVE_DATA_PROVIDER=ave_rest
OT_PI_REFLECTION_MOCK=0
AVE_USE_DOCKER=true
```

This is the intended user-facing mode for:

- new contributor setup
- Codex or Claude Code repository orientation
- normal distillation work
- integration into another agent system

Required values:

```bash
AVE_API_KEY=...
API_PLAN=pro
KIMI_API_KEY=...
```

### Mock verification

Switch to these values only when you intentionally want offline smoke coverage:

```bash
AVE_DATA_PROVIDER=mock
OT_PI_REFLECTION_MOCK=1
AVE_USE_DOCKER=false
```

### Live execution

Add these only when the execution path is needed:

```bash
OKX_API_KEY=...
OKX_SECRET_KEY=...
OKX_PASSPHRASE=...
OT_ONCHAINOS_CLI_BIN=/absolute/path/to/onchainos
```

## Variable Groups

### Core workspace

- `OT_DEFAULT_WORKSPACE`
- `OT_FRONTEND_BIND_HOST`
- `OT_FRONTEND_PORT`
- `OT_FRONTEND_WORKSPACE`

### AVE data plane

- `AVE_DATA_PROVIDER`
- `AVE_API_KEY`
- `API_PLAN`
- `AVE_REST_SCRIPT_PATH`
- `AVE_REST_PYTHON`
- `AVE_REST_TIMEOUT_SECONDS`
- `AVE_DATA_SERVICE_URL`
- `AVE_DATA_SERVICE_TIMEOUT`
- `AVE_DATA_SERVICE_BIND_HOST`
- `AVE_DATA_SERVICE_PORT`
- `AVE_USE_DOCKER`

### Reflection

- `OT_RUNTIME_DEFAULT`
- `OT_PI_RUNTIME_ROOT`
- `OT_PI_NODE`
- `OT_PI_NPM`
- `KIMI_API_KEY`
- `OT_PI_DEFAULT_MODEL`
- `OT_PI_REFLECTION_MODEL`
- `OT_PI_REFLECTION_REASONING`
- `OT_PI_REFLECTION_MOCK`
- `OT_PI_REFLECTION_TIMEOUT_SECONDS`
- `OT_PI_REFLECTION_REQUEST_TIMEOUT_SECONDS`
- `OT_PI_REFLECTION_MAX_TOKENS`
- `OT_PI_SESSION_DIR`

### Execution

- `ONCHAINOS_HOME`
- `OT_ONCHAINOS_CLI_BIN`
- `OKX_API_KEY`
- `OKX_SECRET_KEY`
- `OKX_PASSPHRASE`
- `OT_ONCHAINOS_LIVE_CAP_USD`
- `OT_ONCHAINOS_MIN_LEG_USD`
- `OT_ONCHAINOS_APPROVAL_WAIT_RETRIES`
- `OT_ONCHAINOS_APPROVAL_WAIT_SECONDS`

### Optional persistence and blob storage

- `OT_DB_DSN`
- `OT_REDIS_URL`
- `OT_BLOB_BACKEND`
- `OT_BLOB_ROOT`
- `OT_BLOB_ENDPOINT`
- `OT_BLOB_BUCKET`
- `OT_BLOB_REGION`
- `OT_BLOB_PREFIX`
- `OT_INLINE_PAYLOAD_LIMIT_BYTES`

## Dependency Notes

- Host `uv` default installs the core dependency layer.
- Add storage support on the host only when needed:

```bash
uv sync --frozen --extra storage
```

- `vendor/pi_runtime` is vendored source. Use:

```bash
uv run 0t runtime prepare --workspace-dir .ot-workspace
```

to install, build, and verify the embedded runtime on the host.

- `vendor/onchainos_cli` is vendored Rust source. Live execution uses either `OT_ONCHAINOS_CLI_BIN` or `cargo run`.
- `vendor/ave_cloud_skill` contains the AVE REST bridge. Host `uv` mode prebuilds `ave-cloud` when `AVE_USE_DOCKER=true`.
- Docker app services disable nested Docker internally by overriding `AVE_USE_DOCKER=false`.

## Diagnostics

Use these commands before debugging application code:

```bash
./scripts/doctor.sh
./scripts/verify.sh
```

If `doctor.sh` reports missing `uv`, Python, Node, or Docker requirements, fix those first. `verify.sh` is a mock-backed repository health check; use it to validate the machine and the repository wiring before debugging real-provider issues.
