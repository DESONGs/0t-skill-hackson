# Target System Blueprint

## Goal

目标状态不是“把所有代码改成 TypeScript”，而是把系统重心从 Python 控制面迁移到 Pi-agent 内核。

目标架构：

```text
User / Codex / Claude Code
  -> Pi Kernel (TS)
    -> Workflow Plugins
      -> Domain Adapter SPI
        -> Python Domain Workers
        -> Data Sources
        -> Execution Providers
```

## Core Layers

### 1. Pi Kernel (TS)

Pi Kernel 是主系统。

它负责：

- session runtime
- agent team orchestration
- plugin registry
- workflow graph execution
- work item dispatch
- event bus
- artifact journal
- approval state machine

它不负责：

- 直接实现钱包蒸馏逻辑
- 直接实现 backtest
- 直接实现具体链上执行逻辑
- 直接内嵌 AVE / OKX 业务规则

### 2. Workflow Plugins

workflow plugin 是业务能力入口。

第一批插件：

- `distillation`
- `autoresearch`
- `review`
- `benchmark`

注意：

- 技术上它们是独立模块
- 业务上它们不一定独立运行
- 真正运行时通过 workflow graph 组合

例如 `autoresearch` 不是“单插件自己做完一切”，而是：

```text
planner -> optimizer -> benchmark -> reviewer -> compare -> iterate/recommend
```

### 3. Domain Adapter SPI

SPI 是系统真正的稳定业务边界。

需要两类一等接口：

- `DataSourceAdapter`
- `ExecutionAdapter`

所有真实外部依赖都必须挂在这里。

这层之后，业务流程不再直接依赖：

- `AVE`
- `OKX OnchainOS`

而是依赖：

- `data_source_id`
- `execution_provider_id`

### 4. Python Domain Workers

Python worker 是领域实现层，不再是系统内核。

它保留：

- 钱包风格蒸馏
- 反射结果解析和兼容
- skill 编译
- candidate / evaluation / promotion 存储
- 本地 service entrypoints
- 现有 backtest / QA / smoke 能力

未来只在两种情况下迁移到 TS：

- 该能力天然属于 Pi kernel 的交互模型
- Python 版本已经成为跨语言边界成本更高的负担

## Target Runtime Flow

### A. Distillation Flow

```text
distillation workflow
  -> request wallet+chain
  -> call DataSourceAdapter
  -> collect compact input
  -> dispatch reflection subtask to Pi runtime
  -> parse structured output
  -> build skill candidate
  -> emit candidate + artifacts
```

### B. Autoresearch Flow

```text
autoresearch workflow
  -> lock baseline skill
  -> planner defines objective and search space
  -> optimizer proposes variant
  -> benchmark evaluates variant
  -> reviewer challenges benchmark result
  -> compare against baseline
  -> iterate or recommend
```

### C. Review / Approval Flow

```text
recommended variant
  -> reviewer gate
  -> human approval
  -> activation or archive
```

### D. Execution Flow

```text
activated skill
  -> resolve ExecutionAdapter
  -> prepare
  -> dry run
  -> explicit approval
  -> live
```

## Stable Core Objects

以下对象必须作为跨模块稳定契约存在：

- `Workspace`
- `AgentSession`
- `WorkflowSession`
- `WorkItem`
- `OptimizationSession`
- `OptimizationVariant`
- `OptimizationRun`
- `OptimizationDecision`
- `OptimizationRecommendation`
- `ExecutionRequest`
- `ExecutionResult`
- `ProviderRequest`
- `ProviderResult`
- `ArtifactRef`
- `ApprovalRecord`

要求：

- 都带 `workspace_id`
- 都带 `lineage`
- 都能持久化
- 都能回放
- 都支持 agent 和人类协作审计

## Architectural Rule Set

### Rule 1

Pi Kernel 是唯一 workflow owner。

### Rule 2

业务插件不能直接操作 storage internals，只能通过 kernel service。

### Rule 3

数据源与执行层不能直接在业务插件中硬编码。

### Rule 4

Python worker 只能作为 worker/service 被调度，不能再反向接管流程编排。

### Rule 5

实盘执行不能进入无人审批闭环。

## What Changes From Today

今天的系统更像：

```text
Python business system
  -> embedded TS reflection runtime
  -> AVE data plane
  -> OKX OnchainOS execution plane
```

目标系统变成：

```text
TS Pi kernel
  -> workflow plugins
  -> data/execution adapters
  -> Python workers
```

真正变化不在语言，而在主次关系。
