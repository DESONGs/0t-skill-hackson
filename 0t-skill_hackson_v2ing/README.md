# 0T Skill Enterprise

主工程目录位于外层仓库 `0t-skill-v2/` 下，本目录负责运行时代码、蒸馏链路、执行适配、测试与本地前端。

## 当前能力

当前主线是 `wallet-style skill`：

- 用 AVE 拉取钱包、代币、市场和信号数据
- 生成 `DistillationFeatures`
- 用 Pi/Kimi 做结构化 reflection，输出 `profile + strategy + execution_intent + review`
- 构建并晋升本地 skill 包
- 运行 `primary` 和 `execute` 两个 action
- 做 dry-run 和 live readiness 验收

同时保留通用 SkillOps 主链：

`run -> evaluation -> candidate -> package -> validate -> promote`

## 启动方式

```bash
cd /Users/chenge/Desktop/hackson/0t-skill-v2/0t-skill_hackson_v2ing
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
```

命令行入口会自动读取本目录下的 `.env`。

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

前端默认地址：

- `http://127.0.0.1:8090`

## 配置说明

项目不依赖硬编码密钥；外部依赖全部通过环境变量注入。

### 数据与模型

- `AVE_API_KEY`
- `API_PLAN`
- `AVE_DATA_PROVIDER`
- `KIMI_API_KEY`
- `OT_PI_REFLECTION_MODEL`
- `OT_PI_REFLECTION_REASONING`
- `OT_PI_REFLECTION_MOCK`

### 执行层

- `OKX_API_KEY`
- `OKX_SECRET_KEY`
- `OKX_PASSPHRASE`
- `ONCHAINOS_HOME`
- `OT_ONCHAINOS_CLI_BIN`
- `OT_ONCHAINOS_LIVE_CAP_USD`
- `OT_ONCHAINOS_MIN_LEG_USD`
- `OT_ONCHAINOS_APPROVAL_WAIT_RETRIES`
- `OT_ONCHAINOS_APPROVAL_WAIT_SECONDS`

### 工作区与前端

- `OT_DEFAULT_WORKSPACE`
- `OT_FRONTEND_BIND_HOST`
- `OT_FRONTEND_PORT`

完整样例见 [.env.example](./.env.example)。

## 当前目录说明

- `src/ot_skill_enterprise/`
  - 核心业务代码
- `frontend/`
  - 本地 dashboard 静态资源
- `tests/`
  - 回归测试
- `vendor/`
  - vendored `pi_runtime` 和 `onchainos_cli`
- `docs/`
  - 工程和产品说明

## 文档入口

- [docs/README.md](./docs/README.md)
- [docs/architecture/01-system-overview.md](./docs/architecture/01-system-overview.md)
- [docs/architecture/02-wallet-style-agent-reflection.md](./docs/architecture/02-wallet-style-agent-reflection.md)
- [docs/product/01-plain-language-platform-guide.md](./docs/product/01-plain-language-platform-guide.md)
