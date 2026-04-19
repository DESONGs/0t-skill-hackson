# Next Architecture Package

这组文档定义 0T-Skill 下一阶段的目标架构。

核心结论只有一条：

- 从 `Python 主系统 + TS 子运行时` 升级为 `TS Pi 内核 + Python 领域 worker`

这不等于“全部重写成 TypeScript”。  
真正要重构的是系统主次关系和插件边界：

- `Pi Kernel (TS)` 负责 agent runtime、workflow orchestration、plugin registry、team coordination
- `Domain Workers (Python)` 负责当前已经稳定的蒸馏、编译、评估、服务接线、存储和部分执行逻辑
- `Data Source` 与 `Execution Provider` 必须成为独立适配器，不再由业务流程直接写死为 AVE 和 OKX OnchainOS

## Read Order

1. [target-system-blueprint.md](./target-system-blueprint.md)
2. [kernel-and-stack-boundary.md](./kernel-and-stack-boundary.md)
3. [plugin-workflow-model.md](./plugin-workflow-model.md)
4. [data-and-execution-adapters.md](./data-and-execution-adapters.md)
5. [migration-phases.md](./migration-phases.md)
6. [team-delivery-plan.md](./team-delivery-plan.md)
7. [kernel-runtime-runbook.md](./kernel-runtime-runbook.md)
8. [rollback-checklist.md](./rollback-checklist.md)

## Document Split

- [target-system-blueprint.md](./target-system-blueprint.md)
  - 最终目标架构、核心对象、主流程、端到端关系
- [kernel-and-stack-boundary.md](./kernel-and-stack-boundary.md)
  - TS Pi 内核与 Python worker 的职责划分、边界规则、禁区
- [plugin-workflow-model.md](./plugin-workflow-model.md)
  - 插件分类、workflow graph、业务组合方式、autoresearch 如何联动 benchmark/review
- [data-and-execution-adapters.md](./data-and-execution-adapters.md)
  - 数据源与执行层适配器模型、接口设计、替换策略
- [migration-phases.md](./migration-phases.md)
  - 分阶段迁移路线、每阶段产物、验收标准、风险控制
- [team-delivery-plan.md](./team-delivery-plan.md)
  - 研发团队并行分工方案、流依赖、里程碑、交付节奏
- [kernel-runtime-runbook.md](./kernel-runtime-runbook.md)
  - TS kernel 默认路径的启动、验证、排错和 release gate
- [rollback-checklist.md](./rollback-checklist.md)
  - `python-compat` 回滚触发条件、操作步骤和退出标准

## Decision Summary

- Pi-agent 是系统内核，不再只是被 Python 调用的 reflection runtime
- distillation、autoresearch、review、benchmark 都是插件
- 插件技术上独立，业务上通过 workflow graph 编排
- 数据源和执行层一律走 adapter SPI
- Python 不是被淘汰，而是下沉为领域 worker 层
- `0t` 逐步从“总系统”转成“领域能力集合”
- `0t team` 的 team orchestration 能力逐步并入 Pi kernel 的 workflow orchestration

## Why This Package Exists

当前仓库已经暴露出三类问题：

- 架构文档直接把 AVE 固定为数据平面，把 OnchainOS 固定为执行平面
- Pi runtime 仍然是嵌入式运行时，不是一等内核
- provider 抽象初步存在，但 execution 仍然基本写死

这组文档的目标不是描述现状，而是给开发团队一套可以并行落地的下一阶段实施蓝图。
