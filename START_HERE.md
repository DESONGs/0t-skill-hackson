# Start Here

这份文档给“代码能力一般，但想尽快跑起来”的用户。

如果你不是自己跑命令，而是想直接把仓库交给 Codex / Claude Code，请先看 [AGENT_QUICKSTART.md](./AGENT_QUICKSTART.md)。
如果你要找的是新的 agent-team protocol / `0t team` 入口，请看 [the `0t-protocol` guide](./docs/product/0t-protocol-guide.md)。

先记住一条：

- 所有命令都在仓库根目录执行
- 不要进入别的子目录再启动

## 你有两条正式路径

### 1. Host `uv` path

适合：

- 想直接本机调试
- 想让 Codex / Claude Code 直接改代码
- 想保留最完整的本机文件路径

### 2. Docker path

适合：

- 本机 Python / Node 环境不稳定
- 你只想“先跑起来”
- 你更在意隔离和一致性

## 如果你是用 agent 启动

最省事的路径不是自己记命令，而是：

1. 用仓库根目录打开 Codex / Claude Code
2. 把 [AGENT_QUICKSTART.md](./AGENT_QUICKSTART.md) 里的第一段提示词直接发给 agent
3. 补完 `.env` 后，再把第二段和第三段提示词继续发给 agent

下面保留的是“你自己手动跑”的路径。

`0t-protocol` 不是启动项目的替代路径，它只负责 planner / optimizer / reviewer 协作层。

## 手动路径 A：Host `uv`

### 第 1 步：检查电脑缺不缺依赖

```bash
./scripts/doctor.sh
```

这个命令只做检查，不会启动项目。

### 第 2 步：复制配置文件

```bash
cp .env.example .env
```

### 第 3 步：先填这 3 个关键配置

打开 `.env`，先填下面 3 项：

```bash
AVE_API_KEY=...
API_PLAN=pro
KIMI_API_KEY=...
```

### 第 4 步：同步 Python 环境

```bash
uv sync --frozen
```

### 第 5 步：准备 runtime 和 AVE Docker 镜像

```bash
uv run 0t runtime prepare --workspace-dir .ot-workspace
```

### 第 6 步：启动后端数据服务

新开一个终端窗口：

```bash
./scripts/start_ave_data_service.sh
```

### 第 7 步：启动前端页面

再开一个终端窗口：

```bash
./scripts/start_frontend.sh
```

浏览器打开：

- [http://127.0.0.1:8090](http://127.0.0.1:8090)

### 第 8 步：执行一次蒸馏命令

再开一个终端窗口：

```bash
uv run 0t workflow wallet-style-distillation --workspace-dir .ot-workspace --wallet 0x... --chain bsc
```

## 手动路径 B：Docker

### 第 1 步：检查环境

```bash
./scripts/doctor.sh
```

### 第 2 步：复制配置文件

```bash
cp .env.example .env
```

### 第 3 步：先填这 3 个关键配置

```bash
AVE_API_KEY=...
API_PLAN=pro
KIMI_API_KEY=...
```

### 第 4 步：构建 app 镜像

```bash
./scripts/docker_build.sh
```

### 第 5 步：启动服务

```bash
./scripts/docker_up.sh
```

如果你还要本地 Postgres / Redis / MinIO：

```bash
./scripts/docker_up.sh --with-infra
```

### 第 6 步：执行一次蒸馏

```bash
./scripts/docker_cli.sh workflow wallet-style-distillation --workspace-dir /app/.ot-workspace --wallet 0x... --chain bsc
```

## 如果你只想先检查仓库是不是完整

```bash
./scripts/verify.sh
```

这个命令是仓库健康检查，不是正式启动流程。  
它会走 mock 烟测，用来判断“仓库 wiring 有没有坏”。
