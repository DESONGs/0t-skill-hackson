# Handoff And Escalation

## 1. handoff 规则

- subagent 完成阶段任务后，先写报告再交接
- 没有报告，不算完成 handoff
- handoff 时必须说明：
  - 完成范围
  - 未完成范围
  - 风险
  - 建议下一步

## 2. escalation 规则

以下情况必须上报 mainagent：

- contract 变更
- 跨 owner 目录修改
- blocker 超过 30 分钟
- 测试基线无法稳定通过

## 3. mainagent 决策类型

- `accept`
- `revise`
- `block`
- `reassign`

subagent 收到结论后才能继续动作。
