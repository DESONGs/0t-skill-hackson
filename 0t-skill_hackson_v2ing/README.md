# 0T Skill Enterprise

这个目录是公开仓的主工程，包含运行时代码、控制平面、服务、脚本、测试和 vendored source。

## 能力范围

当前主线是完整的 `wallet-style skill`：

- 用 AVE 拉取钱包、代币、市场和信号数据
- 生成 `DistillationFeatures`
- 用 Pi/Kimi 做结构化 reflection，输出 `profile + strategy + execution_intent + review`
- 构建并晋升本地 skill 包
- 运行 `primary` 和 `execute` 两个 action
- 做 dry-run 与 live readiness 验收

同时保留通用 SkillOps 主链：

`run -> evaluation -> candidate -> package -> validate -> promote`

## 依赖前提

- Python `3.11+`
- Node.js `20+`
- `npm`
- Rust / Cargo
  - 需要 onchainos 执行路径时必备
- Docker / Docker Compose
  - 只在本地 infra 模式下需要

## 启动方式

```bash
cd 0t-skill_hackson_v2ing
./scripts/bootstrap.sh
cp .env.example .env
```

`bootstrap.sh` 会：

- 创建或复用 `.venv`
- 安装 Python 依赖
- 安装 `vendor/ave_cloud_skill` 的 Python requirements
- 安装 `vendor/pi_runtime` 的 npm 依赖
- 构建并校验 `vendor/pi_runtime/dist/pi-runtime.mjs`

命令行入口和服务会自动读取本目录下的 `.env`。

如果你之前移动过仓库目录或污染过 `.venv`，先删除 `.venv` 再重新运行 `./scripts/bootstrap.sh`。

## 服务启动

```bash
./scripts/start_ave_data_service.sh
./scripts/start_frontend.sh
./scripts/start_pi_runtime.sh
```

如果你要本地拉起 Postgres / Redis / MinIO：

```bash
OT_START_LOCAL_STACK=1 ./scripts/bootstrap.sh
```

或手动执行：

```bash
./scripts/start_stack.sh
```

## 常用命令

```bash
ot-enterprise runtime list
ot-enterprise runtime overview --workspace-dir .ot-workspace

ot-enterprise style list --workspace-dir .ot-workspace
ot-enterprise style get --workspace-dir .ot-workspace --job-id <job_id>
ot-enterprise style distill --workspace-dir .ot-workspace --wallet 0x... --chain bsc
ot-enterprise style resume --workspace-dir .ot-workspace --job-id <job_id> --live-execute --approval-granted

ot-frontend
```

## 执行层说明

- `OT_ONCHAINOS_CLI_BIN` 已配置时，系统直接调用该二进制
- 未配置时，执行器会回退到：
  - `vendor/onchainos_cli/upstream/cli/target/release/onchainos`
  - `vendor/onchainos_cli/upstream/cli/target/debug/onchainos`
  - `cargo run --manifest-path vendor/onchainos_cli/upstream/cli/Cargo.toml --`

因此公开仓不附带 Rust 构建产物，但保留了完整的 vendored source。

## 测试与验证

```bash
.venv/bin/python -m pytest
./scripts/verify.sh
```

如果 `vendor/pi_runtime/node_modules` 尚未安装，`verify.sh` 会跳过对应检查；先执行一次 `./scripts/bootstrap.sh` 即可。

前端默认地址：

- `http://127.0.0.1:8090`

## 配置说明

项目不依赖硬编码密钥；外部依赖全部通过环境变量注入。完整模板见 [.env.example](./.env.example)。

### 核心必填

- `AVE_API_KEY`
- `API_PLAN`
- `KIMI_API_KEY`

### 执行层

- `OKX_API_KEY`
- `OKX_SECRET_KEY`
- `OKX_PASSPHRASE`
- `ONCHAINOS_HOME`
- `OT_ONCHAINOS_CLI_BIN`

### 工作区与前端

- `OT_DEFAULT_WORKSPACE`
- `OT_FRONTEND_BIND_HOST`
- `OT_FRONTEND_PORT`
- `OT_FRONTEND_WORKSPACE`

## 目录说明

- `src/ot_skill_enterprise/`
  - 核心业务代码
- `services/`
  - AVE data service 等本地服务实现
- `scripts/`
  - bootstrap、启动和验证脚本
- `tests/`
  - 回归测试
- `vendor/`
  - vendored `pi_runtime`、`onchainos_cli`、`ave_cloud_skill`、`skill_enterprise`
- `skills/`
  - 保留最小公开 skill 集合与测试 fixture
- `docs/`
  - 工程与产品说明

## 文档入口

- [docs/README.md](./docs/README.md)
- [docs/architecture/01-system-overview.md](./docs/architecture/01-system-overview.md)
- [docs/architecture/02-wallet-style-agent-reflection.md](./docs/architecture/02-wallet-style-agent-reflection.md)
- [docs/product/01-plain-language-platform-guide.md](./docs/product/01-plain-language-platform-guide.md)
