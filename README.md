# 0T-Skill

这个仓库做两件事：

1. 把一个链上钱包的交易风格蒸馏成可复用的 skill 包
2. 围绕这个 skill 继续做研究、评测、复查和审批

对外只有一个正式入口：`0t`。

- 普通运行用 `0t workflow ...`
- 多 agent 长任务用 `0t team ...`

如果你只想尽快跑起来，看 [START_HERE.md](./START_HERE.md)。  
如果你想把仓库直接交给 Codex / Claude Code，看 [AGENT_QUICKSTART.md](./AGENT_QUICKSTART.md)。

## 这个项目现在能做什么

- 钱包蒸馏：把地址的行为总结成一个 skill
- 研究闭环：跑 baseline、变体、benchmark、review、approval
- 本地前端：启动本地页面查看结果
- Docker 启动：机器环境弱时也能跑
- Agent 协作：`0t team` 可以把长任务交给多 agent 协作处理

## 一句话架构

现在的主结构很简单：

- `TS Pi kernel` 负责流程、状态、审批和长任务编排
- `Python workers` 负责蒸馏、数据获取、评测、review 这些业务工作
- `0t workflow` 是默认前门
- `0t team` 是高级入口，只在长任务和 handoff 场景下用

## 最常用的两条启动路径

### 1. 本机 `uv` 路径

适合：

- 你要直接调试
- 你要让 Codex / Claude Code 直接改代码
- 你想保留最完整的本机文件路径

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

前端默认地址：

- [http://127.0.0.1:8090](http://127.0.0.1:8090)

### 2. Docker 路径

适合：

- 本机 Python / Node 环境不稳定
- 你只想先跑起来
- 你更看重隔离和一致性

```bash
./scripts/doctor.sh
cp .env.example .env
./scripts/docker_build.sh
./scripts/docker_up.sh
./scripts/docker_cli.sh workflow wallet-style-distillation --workspace-dir /app/.ot-workspace --wallet 0x... --chain bsc
```

如果还要本地 Postgres / Redis / MinIO：

```bash
./scripts/docker_up.sh --with-infra
```

## 你日常会用到的命令

### 正常做事

```bash
uv run 0t workflow overview
uv run 0t workflow wallet-style-distillation --wallet 0x... --chain bsc
uv run 0t workflow autonomous-research --wallet 0x... --chain bsc --skill-name desk-alpha
```

### 多 agent 长任务

```bash
uv run 0t team doctor
uv run 0t team start autoresearch --workspace desk-alpha --skill my-skill --adapter codex --data-source-adapter ave --execution-adapter onchainos_cli
uv run 0t team status <session_id>
uv run 0t team review <session_id>
```

### 仓库健康检查

```bash
./scripts/doctor.sh
./scripts/verify.sh
```

`verify.sh` 是 mock 烟测，用来查“仓库 wiring 有没有坏”，不是正式生产运行路径。

## 配置边界

默认是 real-first：

- `AVE_DATA_PROVIDER=ave_rest`
- `OT_PI_REFLECTION_MOCK=0`
- `AVE_USE_DOCKER=true`

这表示：

- 默认走真实 AVE
- 默认走真实 Kimi
- Docker app 服务内部会覆盖 `AVE_USE_DOCKER=false`，避免容器里再套 Docker

如果你只是做 mock 验证，可以临时切成：

```bash
AVE_DATA_PROVIDER=mock
OT_PI_REFLECTION_MOCK=1
AVE_USE_DOCKER=false
```

## 仓库怎么看

- [AGENTS.md](./AGENTS.md)
  给 Codex / Claude Code 这类 agent 的正式入口说明
- [AGENT_QUICKSTART.md](./AGENT_QUICKSTART.md)
  给普通用户直接复制给 agent 的提示词
- [START_HERE.md](./START_HERE.md)
  最短人工启动路径
- [CONFIGURATION.md](./CONFIGURATION.md)
  配置和环境说明
- [docs/README.md](./docs/README.md)
  文档索引
- [docs/product/0t-protocol-guide.md](./docs/product/0t-protocol-guide.md)
  `0t team` 和 `0t-protocol` 的说明

## 仓库里主要目录

```text
.
├── 0t-protocol/             # repo-tracked protocol bundle
├── scripts/                 # doctor、启动、Docker、verify
├── src/ot_skill_enterprise/ # control plane、runtime、distillation、execution
├── services/                # 本地数据服务
├── frontend/                # 本地前端静态资源
├── skills/                  # fixture skill 和生成产物目录
├── docs/                    # 操作、架构、协议文档
├── docker/                  # app 和 AVE bridge Dockerfile
├── tests/                   # 回归和单测
└── vendor/                  # vendored runtime，上手时最后再看
```

## 许可证

Apache License 2.0
