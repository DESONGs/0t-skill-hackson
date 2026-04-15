# 0t-skill-v2 Agent Guide

`0t-skill-v2/` 是当前唯一 git 根、唯一对外交付仓、唯一开发入口。

## 仓库拓扑

- 根级开发指引：`distill-modules/`
- 根级权威规则：`agent.md`
- 主工程目录：`0t-skill_hackson_v2ing/`
- 业务代码：`0t-skill_hackson_v2ing/src/ot_skill_enterprise/`
- vendored 依赖：`0t-skill_hackson_v2ing/vendor/`

外层根目录负责：

- 仓库治理
- 开发约束
- distill 文档
- CI / 验收入口

内层主工程负责：

- 运行时代码
- 蒸馏、反射、编译、QA
- 前后端与脚本
- vendored 执行适配依赖

## 边界冻结

### 唯一数据边界

- 蒸馏、回测、置信度、市场上下文、历史交易特征提取全部只允许使用 `AVE`
- `onchainos` 不得作为蒸馏数据源、回测数据源、信号数据源、PnL 数据源
- 任何 `style_distillation`、`reflection`、`backtester`、`market context` 代码不得读取 vendored `onchainos` 的 market / signal / portfolio / tracker / defi 数据路径

### 唯一执行边界

- 钱包登录、签名、安全扫描、dry-run、广播统一走 `onchainos CLI`
- 执行入口只能通过生成 skill 的 `execute` action 触发
- 禁止蒸馏链、反射链、回测链直接发起链上执行
- 禁止绕过 `execute` action 直接从 `primary`、`style_distillation`、`reflection` 调用 `onchainos`

## Skill 合同冻结

- `primary`
  - `allow_network: false`
  - 负责 recommendation + trade_plan
  - 不做真实交易
- `execute`
  - `allow_network: true`
  - 只消费已有 `trade_plan + execution_intent`
  - 通过执行适配层调用 `onchainos CLI`
  - 默认只要求 `dry_run_ready`

## Agent Team Write Set

### MainAgent

- 根级 `agent.md`
- 仓库拓扑与规则裁决
- 跨层接口审核：`StrategySpec`、`ExecutionIntent`、`execution_readiness`

### Agent A

- 根级 README
- 根级仓库元信息
- 内层 `agent.md` 指针化
- 开发入口与路径说明

### Agent B

- `0t-skill_hackson_v2ing/vendor/onchainos_cli/`
- `0t-skill_hackson_v2ing/src/ot_skill_enterprise/execution/`
- 执行配置与 subprocess 合同

### Agent C

- `0t-skill_hackson_v2ing/src/ot_skill_enterprise/reflection/`
- `0t-skill_hackson_v2ing/src/ot_skill_enterprise/style_distillation/`
- `0t-skill_hackson_v2ing/src/ot_skill_enterprise/skills_compiler/`

### Agent D

- `0t-skill_hackson_v2ing/tests/`
- QA 契约
- 验收脚本与测试补充

## 禁止事项

- 禁止双 git 根
- 禁止双数据路径
- 禁止从 `primary` 直接链上操作
- 禁止用 `onchainos` 的市场、信号、PnL 数据回灌蒸馏
- 禁止把 `execution_readiness` 与 `confidence` 混为一个字段

## 开发顺序

1. 先冻结仓库边界与规则
2. 再接 `onchainos CLI` 执行适配
3. 再升级蒸馏合同到 `profile + strategy + execution_intent + review`
4. 最后补双层 QA 与端到端验证
