# Workflow Runtime Rollback Checklist

## Trigger Conditions

满足任一条件即可触发从 `ts-kernel` 回滚到 `python-compat`：

- `0t workflow distillation-seed` 在默认环境下失败
- `0t style distill` 在默认环境下失败
- worker bridge 出现不可恢复的 contract mismatch
- vendored Pi runtime 无法启动且无法在发布窗口内修复
- CI 无法在安装 runtime 依赖后通过

## Rollback Procedure

1. 仅切环境变量：

```bash
export OT_WORKFLOW_RUNTIME=python-compat
```

2. 复跑最小验证：

```bash
OT_WORKFLOW_RUNTIME=python-compat uv run 0t workflow distillation-seed --wallet 0x... --chain bsc --skill-name rollback-check
OT_WORKFLOW_RUNTIME=python-compat uv run 0t style distill --workspace-dir .ot-workspace --wallet 0x... --chain bsc
./scripts/verify.sh
```

3. 在变更记录里标注：

- rollback 时间
- 触发原因
- 失败 workflow/session id
- 最近一次成功的 commit / release

## Post-Rollback Checks

- `workflow overview` 仍可用
- `style distill` 结果与回滚前关键 artifact 等价
- `.ot-workspace/runtime-sessions/*/workflow-kernel/` 不再持续增长新的失败 session
- 现有 operator 命令无需改动

## Forbidden Actions

- 不允许在回滚窗口内手工改代码去“临时恢复”
- 不允许删除 runtime session artifacts 逃避问题
- 不允许把 rollback 当成长期默认

## Exit Criteria

只有在下列条件重新满足后，才允许把默认值切回 `ts-kernel`：

- CI 通过
- `verify.sh` 通过
- `distillation-seed` 和 `style distill` 的关键 artifact 等价性复核通过
- 本轮 root cause 已有修复和回归测试
