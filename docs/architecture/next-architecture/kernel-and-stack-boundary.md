# Kernel And Stack Boundary

## Core Decision

采用：

- `TS Pi 内核 + Python 领域 worker`

不采用：

- `继续以 Python 作为总系统，仅把 Pi 当成 reflection 子进程`
- `一步到位把全部领域逻辑重写为 TypeScript`

## Why This Boundary

### 原因 1

Pi-agent 天然适合做：

- session runtime
- extension / skill / package loading
- workflow orchestration
- agent team coordination
- RPC / SDK integration

### 原因 2

当前 Python 代码已经承载了大量稳定领域逻辑：

- distillation pipeline
- reflection fallback handling
- candidate compile / validate / promote
- storage / runs / evaluation
- 本地服务和脚本

这些能力现在直接丢掉重写，收益低，风险高。

### 原因 3

当前真正的问题不是“语言错了”，而是：

- Pi 不是主系统
- 数据源和执行层边界没收好
- Python 同时承担业务与编排两层职责

## Layer Responsibilities

## TS Pi Kernel

TS Pi kernel 负责：

- workflow registry
- plugin lifecycle
- work item graph
- multi-agent session orchestration
- event normalization
- artifact journal
- approval workflow
- plugin-to-worker dispatch
- repo-native protocol loading

TS Pi kernel 不负责：

- 钱包特征计算
- 领域级 schema 兼容补丁
- 本地数据服务内部实现
- 每个执行供应商的具体调用细节

## Python Domain Workers

Python workers 负责：

- wallet style extraction
- distillation pre-processing
- structured reflection parsing and fallback
- skill build and compile
- benchmark implementations that already exist in Python
- candidate / promotion persistence
- local service APIs

Python workers 不负责：

- workflow graph orchestration
- plugin registry
- session-level agent team governance
- 跨插件调度

## Adapter Layer

adapter layer 既不是 kernel，也不是 worker。

它是稳定边界，负责：

- 统一外部依赖模型
- 隐藏单个供应商差异
- 保持 workflow 与外部依赖解耦

必须存在：

- `DataSourceAdapter`
- `ExecutionAdapter`
- 后续可扩展 `ModelProviderAdapter`、`StorageAdapter`

## Plugin Boundary

插件边界必须卡在“业务能力”和“组合编排”之间。

### Step Plugin

step plugin 是单一步骤能力。

例如：

- `distillation`
- `review`
- `benchmark`
- `execution-prepare`

### Workflow Plugin

workflow plugin 是业务编排能力。

例如：

- `autoresearch`
- `distill-and-harden`
- `candidate-promotion-review`

要求：

- workflow plugin 可以编排 step plugin
- step plugin 不能反过来自己持有全局编排权

## Business Composition Rule

技术独立不等于业务隔离。

例如：

- `benchmark` 要能单独测试
- `review` 要能单独运行
- 但 `autoresearch` 必须能调用二者形成研究循环

因此正确模型是：

- 插件技术上独立
- workflow 上可组合
- 组合关系通过 graph/template 声明，不写死在插件实现里

## Communication Contracts

TS Pi kernel 与 Python workers 之间，统一通过以下边界通信：

- JSON payload
- versioned schema
- artifact refs
- event envelopes
- explicit status model

禁止：

- 直接共享内存对象
- 在 plugin 内直接 import Python module 作为主调用路径
- 在 worker 内部偷偷发起下游 workflow

## State Ownership

### Kernel-Owned

- workflow session state
- work item status
- team role assignment
- approval status
- artifact lineage
- recommendation state

### Worker-Owned

- domain computation internals
- local execution details
- provider-specific parsing
- benchmark raw result generation

### Shared Through Contract

- payload schemas
- event models
- artifact refs
- health and diagnostics

## What Must Be Removed Over Time

以下模式需要逐步移除：

- 编译生成物直接 import 单一 execution module
- execution layer 内部反向耦合 data source 供应商
- distillation service 直接假设唯一 provider
- Python service 直接承担 workflow orchestration

## Migration Rule

任何迁移都必须满足：

1. 先抽边界，再迁语言
2. 先让 Pi 接管 orchestrator，再决定是否迁移 worker
3. 先让 adapter SPI 稳定，再引入第二个真实供应商
4. 任何 live execution 变更都不能绕过人工审批
