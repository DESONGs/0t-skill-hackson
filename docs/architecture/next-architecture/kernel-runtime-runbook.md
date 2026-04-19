# TS Kernel Runtime Runbook

本 runbook 面向维护 `TS Pi kernel + Python domain workers` 路径的开发与运维团队。

## 1. 标准准备

### Host `uv` 路径

```bash
./scripts/doctor.sh
cp .env.example .env
uv sync --frozen
uv run 0t runtime prepare --workspace-dir .ot-workspace
```

### Vendored Pi runtime 依赖

```bash
cd vendor/pi_runtime
npm ci
npm run build:ot-runtime
```

如果 `vendor/pi_runtime/dist/pi-runtime.mjs` 旧于 `vendor/pi_runtime/upstream/coding_agent/src/*.ts`，运行时会自动切到 `tsx` dev path。

## 2. 默认工作流路径

默认值：

```bash
OT_WORKFLOW_RUNTIME=ts-kernel
```

显式检查：

```bash
uv run 0t workflow overview
```

关键观察点：

- `kernel_launch_plan.workflow_runtime_mode == "ts-kernel"`
- `kernel_launch_plan.mode` 为 `release` 或 `dev`
- `workflow_registry` 中存在 `distillation / autoresearch / benchmark / review`

## 3. 回滚

仅允许通过环境变量回滚：

```bash
OT_WORKFLOW_RUNTIME=python-compat
```

回滚后的验证命令：

```bash
OT_WORKFLOW_RUNTIME=python-compat uv run 0t workflow distillation-seed --wallet 0x... --chain bsc --skill-name rollback-check
OT_WORKFLOW_RUNTIME=python-compat uv run 0t style distill --workspace-dir .ot-workspace --wallet 0x... --chain bsc
```

禁止做法：

- 直接修改代码切回旧路径
- 绕过 `0t workflow` 手工拼接 worker 调用
- 在 Docker app 服务里再套一层 `AVE_USE_DOCKER=true`

## 4. 故障排查

### 症状：workflow overview 仍显示旧 build artifact

检查：

```bash
uv run 0t workflow overview
```

若 `kernel_launch_plan.mode == "release"` 但上游 `src/*.ts` 已更新，重新构建 vendored runtime：

```bash
cd vendor/pi_runtime
npm ci
npm run build:ot-runtime
```

### 症状：TS kernel 运行成功但主命令拿不到 final_result

检查 runtime session：

```bash
find .ot-workspace/runtime-sessions -path '*workflow-kernel/result.json' | sort | tail -n 1
find .ot-workspace/runtime-sessions -path '*workflow-kernel/session.json' | sort | tail -n 1
find .ot-workspace/runtime-sessions -path '*workflow-kernel/journal.jsonl' | sort | tail -n 1
```

重点看：

- `result.json` 是否存在
- `session.json.status`
- `journal.jsonl` 是否完成 terminal step

### 症状：worker bridge 调用失败

检查 request/response：

```bash
find .ot-workspace/runtime-sessions -path '*workflow-kernel/requests/*.json' | sort | tail -n 1
find .ot-workspace/runtime-sessions -path '*workflow-kernel/responses/*.json' | sort | tail -n 1
```

重点字段：

- `action_id`
- `contract_version`
- `outputs`
- `error.code`
- `error.details.traceback`

## 5. 最小验收

```bash
PYTHONPATH=src python -m pytest -q \
  tests/test_nextgen_distillation_worker_bridge.py \
  tests/test_nextgen_plugin_registry.py \
  tests/test_nextgen_adapter_registry.py \
  tests/test_nextgen_adapter_integration.py \
  tests/test_nextgen_workflow_service.py \
  tests/test_nextgen_workflow_cli.py \
  tests/test_nextgen_architecture_cli.py \
  tests/test_agent_team_service.py \
  tests/test_verify_script.py

./scripts/verify.sh
```

## 6. Release Gate

满足以下条件才允许把 `ts-kernel` 保持为默认：

- targeted nextgen regression 通过
- `./scripts/verify.sh` 通过
- `uv run 0t workflow distillation-seed ...` 通过
- `uv run 0t style distill ...` 通过同一路径输出
- CI 在安装 vendored Pi runtime 依赖后通过
