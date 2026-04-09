# 0T Skill Enterprise

`0t-skill_enterprise` 是一个单根目录、可直接运行的 AVE 数据分析工程。

项目目标很明确：以 `skill_enterprise` 的技能运行和演化能力为核心，把 `ave-cloud-skill` 作为外部数据来源接入，形成一个可执行、可测试、可演进的分析闭环。v1 只做数据分析，不做交易，不接 WSS。

## 当前已实现

- `ave-data-service`
  - 对外统一暴露 5 个数据操作
  - 支持 `mock` 和真实 AVE REST provider
- `ave-data-gateway`
  - 把 AVE 数据服务包装成稳定 skill action
  - 写出标准化 artifact
- `analysis-core`
  - 生成分析计划
  - 整合证据
  - 输出 `report.md` 和 `report.json`
- `workflow presets`
  - `token_due_diligence`
  - `wallet_profile`
  - `hot_market_scan`
- `analysis-core` 自演化闭环
  - `feedback -> case -> proposal -> submission`
- 单根目录运行入口
  - `ot-enterprise`
  - `ot-serve-ave-data`

## 架构

1. `services/ave-data-service/`
   AVE 数据服务。负责 AVE REST 映射、统一 envelope、错误处理和 provider 选择。
2. `skills/ave-data-gateway/`
   稳定数据 skill。只做取数，不做分析，不参与自动演化。
3. `skills/analysis-core/`
   分析 skill。只通过 gateway 产物做分析，是唯一进入演化闭环的 skill。
4. `src/ot_skill_enterprise/`
   项目实现代码，包括 runtime、gateway adapter、analysis logic、workflow runtime、lab 和 registry glue。
5. `vendor/`
   复刻的运行依赖和上游能力快照。
   - `vendor/skill_enterprise/`
   - `vendor/ave_cloud_skill/`

## 快速开始

### 1. 安装依赖

要求：

- Python `3.11+`
- 可用的 `pip`

推荐流程：

```bash
cd 0t-skill_enterprise
python3 -m venv .venv
source .venv/bin/activate
cp .env.example .env
./scripts/bootstrap.sh
```

如果你要跑真实 AVE 数据，还需要在 `.env` 中填写：

- `AVE_API_KEY`
- `API_PLAN`
- `AVE_DATA_PROVIDER=ave_rest`

本地开发默认可直接使用：

- `AVE_DATA_PROVIDER=mock`

### 2. 启动数据服务

```bash
cd 0t-skill_enterprise
./scripts/start_ave_data_service.sh
```

默认监听：

- `http://127.0.0.1:8080`

### 3. 发现本地 skill

```bash
cd 0t-skill_enterprise
PYTHONPATH=src python -m ot_skill_enterprise.root_cli bridge discover
```

### 4. 运行一个本地 workflow

```bash
cd 0t-skill_enterprise
PYTHONPATH=src python -m ot_skill_enterprise.root_cli workflow-run \
  --preset token_due_diligence \
  --workspace-dir .ot-workspace \
  --inputs-file examples/staging/token_due_diligence.json
```

输出报告位于：

- `.ot-workspace/reports/analysis-report.md`
- `.ot-workspace/reports/analysis-report.json`

## Staging 启动流程

准备 `.env`：

```env
AVE_DATA_PROVIDER=ave_rest
AVE_API_KEY=your-real-key
API_PLAN=free
AVE_DATA_SERVICE_BIND_HOST=127.0.0.1
AVE_DATA_SERVICE_PORT=8080
OT_STAGING_WORKSPACE=.staging-workspace
```

然后执行：

```bash
cd 0t-skill_enterprise
./scripts/run_staging_flow.sh
```

默认会：

1. 加载 `.env`
2. 启动 `ave-data-service`
3. 等待 `/healthz`
4. 跑 `token_due_diligence`
5. 把产物写到 `.staging-workspace/`

更详细的 staging 说明见 `docs/operations/04-staging-startup.md`。

## 常用命令

```bash
# 安装依赖
./scripts/bootstrap.sh

# 启动数据服务
./scripts/start_ave_data_service.sh

# 跑 staging smoke
./scripts/run_staging_flow.sh

# 跑测试与基础 smoke
./scripts/verify.sh

# 发现本地与 vendored skill
PYTHONPATH=src python -m ot_skill_enterprise.root_cli bridge discover
```

## 目录结构

```text
0t-skill_enterprise/
├── agent.md
├── bin/
├── docs/
├── examples/
├── reports/
├── scripts/
├── services/
│   └── ave-data-service/
├── skills/
│   ├── analysis-core/
│   └── ave-data-gateway/
├── src/
│   └── ot_skill_enterprise/
├── tests/
├── vendor/
│   ├── ave_cloud_skill/
│   └── skill_enterprise/
└── workflows/
```

## 文档入口

- `agent.md`
  - 当前 mainagent / subagent 协作与 QA 规则
- `docs/README.md`
  - 文档索引
- `docs/architecture/01-system-overview.md`
  - 系统定位
- `docs/contracts/02-ave-data-gateway-contract.md`
  - 稳定数据动作合同
- `docs/contracts/03-analysis-report-and-feedback-schema.md`
  - 报告与反馈合同

## GitHub 上传前检查

在推 GitHub 前，建议至少完成：

1. 复制 `.env.example` 为 `.env`，确认真实密钥没有进入仓库。
2. 执行 `./scripts/verify.sh`。
3. 确认工作目录里没有 `.ot-workspace/`、`.staging-workspace/`、`.enterprise-installs/` 等运行产物。
4. 检查 `README.md`、`agent.md`、`docs/README.md` 是否与当前实现一致。

更细的检查项见 `docs/operations/05-github-release-checklist.md`。
