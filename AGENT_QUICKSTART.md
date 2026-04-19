# Agent Quickstart

这份文档给“不想自己敲一堆命令，只想打开 Codex / Claude Code 让 agent 接手”的用户。

如果你要的是新的 agent-team protocol / `0t team` 协作入口，也可以直接让 agent 先读 [the `0t-protocol` guide](./docs/product/0t-protocol-guide.md)。

## 默认推荐

默认先走 **host `uv` path**。  
只有当本机 Python / Node 环境明显不稳定时，再切到 Docker path。

## 你只需要做的事

### 第 1 步：用仓库根目录打开 agent

不要让 agent 从子目录开始。  
仓库根目录是唯一正确入口。

### 第 2 步：把下面这段话直接发给 agent

```text
Read AGENTS.md and START_HERE.md first.
If the task is about the planner/optimizer/reviewer protocol, also read the 0t-protocol entrypoint.
Stay on the real provider path, not mock mode.
Use the host uv path unless you detect the machine is missing prerequisites.
Run ./scripts/doctor.sh from the repository root.
Then create .env from .env.example if it does not exist.
Do not continue past environment setup until you tell me exactly which keys I still need to fill.
```

### 第 3 步：你补完密钥后，再发第二段

```text
Continue from the repository root.
Use the host uv path.
Run: uv sync --frozen
Then run: uv run 0t runtime prepare --workspace-dir .ot-workspace
Then start the required local services and tell me the frontend URL and whether the real distillation path is ready.
```

### 第 4 步：要蒸馏某个钱包时，再发第三段

把下面的 `0x...` 换成目标钱包地址：

```text
Use the current real setup and run one wallet distillation.
Command: uv run 0t style distill --workspace-dir .ot-workspace --wallet 0x... --chain bsc
After it finishes, tell me:
1. the generated package name
2. what that name means
3. where the package was written
4. whether it is ready only for review or also ready for execution
```

## 如果 host `uv` path 不稳定

把下面这段发给 agent：

```text
Switch to the Docker path because the local machine setup is unstable.
Run ./scripts/docker_build.sh.
Then run ./scripts/docker_up.sh.
Then use ./scripts/docker_cli.sh to execute the real distillation path.
Tell me the frontend URL and whether the app containers are healthy before you distill.
```

## 如果你是让 agent 跑新的 `0t team` protocol

把下面这段发给 agent：

```text
Read AGENTS.md and the 0t-protocol entrypoint first.
Stay at the repository root.
Treat the repo-tracked 0t-protocol bundle as the source of truth for the planner/optimizer/reviewer architecture.
Run 0t team doctor, then start the autoresearch workflow and generate the planner handoff.
Tell me which workflow, role files, and review gates are active before you make protocol changes.
```

## 现在的默认产物命名

如果你不手动指定 `--skill-name`，系统会自动生成更短、更能表达结果的名字：

- 有明显风格结果时：`meme-hunter-bsc-567a89`
- 还没有稳定风格标签时：`distill-bsc-567a89`

这两个名字都表达三件事：

- 这是一份蒸馏产物
- 来自哪个链
- 对应哪个钱包后缀

## 如果你想自己指定名字

可以直接对 agent 说：

```text
Run the distillation, but use --skill-name Desk Alpha BSC.
```

或者直接让 agent 改命令为：

```bash
uv run 0t style distill --workspace-dir .ot-workspace --wallet 0x... --chain bsc --skill-name "Desk Alpha BSC"
```
