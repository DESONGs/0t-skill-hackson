# 0T Agent Guide

这份文件是给 Codex、Claude Code 和类似 agent 的正式入口说明。

先记住三件事：

1. 只在仓库根目录工作
2. 默认走真实路径，不要一上来切 mock
3. 对外正式命令只有 `0t`

如果任务是多 agent 协作、研究循环、handoff 或审批流，再看 [0t-protocol/ENTRYPOINT.md](./0t-protocol/ENTRYPOINT.md)。

## 先读什么

1. [README.md](./README.md)
2. [AGENT_QUICKSTART.md](./AGENT_QUICKSTART.md)
3. [START_HERE.md](./START_HERE.md)
4. [CONFIGURATION.md](./CONFIGURATION.md)
5. [0t-protocol/ENTRYPOINT.md](./0t-protocol/ENTRYPOINT.md)  
   只有任务明确涉及 `0t team` 时再读
6. [docs/README.md](./docs/README.md)
7. [src/ot_skill_enterprise/README.md](./src/ot_skill_enterprise/README.md)

## 两条正式启动路径

### 本机 `uv`

这是默认推荐路径，最适合 agent 接手。

```bash
./scripts/doctor.sh
cp .env.example .env
# 填 AVE_API_KEY、API_PLAN、KIMI_API_KEY
uv sync --frozen
uv run 0t runtime prepare --workspace-dir .ot-workspace
./scripts/start_ave_data_service.sh
./scripts/start_frontend.sh
uv run 0t workflow wallet-style-distillation --workspace-dir .ot-workspace --wallet 0x... --chain bsc
```

### Docker

本机环境不稳时再走这条。

```bash
./scripts/doctor.sh
cp .env.example .env
./scripts/docker_build.sh
./scripts/docker_up.sh
./scripts/docker_cli.sh workflow wallet-style-distillation --workspace-dir /app/.ot-workspace --wallet 0x... --chain bsc
```

要本地 Postgres / Redis / MinIO 时加：

```bash
./scripts/docker_up.sh --with-infra
```

## 平时该用哪个命令

### 正常业务入口

- `0t workflow ...`

常用命令：

```bash
uv run 0t workflow overview
uv run 0t workflow wallet-style-distillation --wallet 0x... --chain bsc
uv run 0t workflow autonomous-research --wallet 0x... --chain bsc --skill-name desk-alpha
```

### 多 agent / 长任务入口

- `0t team ...`

只在这些场景用：

- planner / optimizer / reviewer 协作
- 长时间研究循环
- handoff
- 审批前后的 operator 动作

常用命令：

```bash
uv run 0t team doctor
uv run 0t team start autoresearch --workspace desk-alpha --skill my-skill --adapter codex --data-source-adapter ave --execution-adapter onchainos_cli
uv run 0t team status <session_id>
uv run 0t team review <session_id>
```

## 运行边界

- `0t workflow` 是默认前门
- `0t team` 是高级入口，不是第二套主系统
- `TS Pi kernel` 持有 workflow/session/work-item/recommendation/approval 真状态
- `Python workers` 只做蒸馏、评测、review、执行准备这些业务工作

## 数据和执行边界

- AVE 是数据平面
- OKX OnchainOS 是执行平面
- 不要把执行逻辑混进纯蒸馏路径
- live execution 仍然是宿主机导向，不进主 Docker app 路径

## 默认模式

`.env.example` 默认就是 real-first：

- `AVE_DATA_PROVIDER=ave_rest`
- `OT_PI_REFLECTION_MOCK=0`
- `AVE_USE_DOCKER=true`

如果只是做 mock 验证，才切：

```bash
AVE_DATA_PROVIDER=mock
OT_PI_REFLECTION_MOCK=1
AVE_USE_DOCKER=false
```

如果做真实执行，再补：

```bash
OKX_API_KEY=...
OKX_SECRET_KEY=...
OKX_PASSPHRASE=...
```

## 回滚开关

默认 workflow runtime 是 `ts-kernel`。  
只有明确回滚时，才设置：

```bash
OT_WORKFLOW_RUNTIME=python-compat
```

## 验证顺序

先跑：

```bash
./scripts/doctor.sh
./scripts/verify.sh
```

`verify.sh` 是 mock 烟测，用来判断仓库 wiring 有没有坏。  
在这两个命令没过之前，不要先下结论说项目坏了。

## 优先读哪些目录

先看：

- `scripts/`
- `src/ot_skill_enterprise/control_plane/`
- `src/ot_skill_enterprise/style_distillation/`
- `services/ave-data-service/`
- `tests/`

最后再看：

- `vendor/`
