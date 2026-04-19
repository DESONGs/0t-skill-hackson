# Agent Execution Contract

这份文件不是项目启动文档。  
项目怎么跑，看 [AGENTS.md](./AGENTS.md)。

这份文件只管一件事：  
当任务很长、需要多 agent 并行、需要主 agent 收敛时，团队怎么执行。

## 适用场景

只有下面这些任务才需要看这份文件：

- 大型重构
- 多模块并行开发
- 需要主 agent 拆任务给多个 subagent
- 最后还要单独 QA 收口

普通修 bug、普通命令运行、普通单文件改动，不需要按这份文件走。

## 总原则

- 仓库根目录是唯一工作目录
- 主启动和运行契约只认 [AGENTS.md](./AGENTS.md)
- 对外命令只认 `0t`
- 主 agent 负责切任务、收代码、做最终裁决
- subagent 只负责自己那一块，不抢别人边界
- QA subagent 最后再上，只做验收，不接管业务实现

## 任务拆分方式

### Main agent

负责：

- 读清楚当前代码和文档
- 冻结这轮任务边界
- 给 subagent 分配互不重叠的写入范围
- 做最终集成
- 跑最后一轮收敛检查

主 agent 应该优先改：

- 顶层入口
- CLI stitching
- docs 总入口
- cross-module wiring

### Subagent

每个 subagent 只负责自己那块。

例子：

- Kernel subagent：只改 kernel/runtime
- Adapter subagent：只改 data/execution adapter
- Workflow subagent：只改 distillation / benchmark / review / autoresearch 相关
- Docs subagent：只改文档

原则：

- 不重叠写文件
- 不替别的 subagent 回滚代码
- 不扩散任务范围

### QA subagent

最后才启用。

只负责：

- 跑测试
- 做回归
- 报 blocker
- 必要时修最小补丁

不负责：

- 重新设计架构
- 趁机重构不相关代码

## 写代码时的执行纪律

- 先把 contract 定清楚，再写实现
- 先把边界定清楚，再并行开发
- 先做最小可验收闭环，再做扩展
- 文档必须跟着代码一起更新
- 旧文档如果已经误导，就删，不保留废话 redirect

## 什么时候可以并行

适合并行的任务：

- 不同目录、不同模块、不同写入边界
- kernel / adapter / workflow / docs / QA 这类天然可拆开的工作

不适合并行的任务：

- 同一批文件同时大改
- 尚未冻结接口的模块
- 主 agent 还没决定 source of truth 的问题

## 交付标准

一轮 agent-team 任务，至少要满足：

1. 代码完成
2. 文档同步
3. 回归跑过
4. 新旧状态 owner 没混回去
5. 主 agent 能清楚说明还剩什么、为什么还剩

## 验收顺序

默认顺序：

1. 主 agent 本地检查
2. 定向 pytest
3. `./scripts/doctor.sh`
4. `./scripts/verify.sh`
5. 必要时补充 e2e 命令
6. QA subagent 最终复核

## 冲突处理

如果这份文件和 [AGENTS.md](./AGENTS.md) 冲突：

- 运行方式、仓库入口、启动命令，永远听 `AGENTS.md`
- 多 agent 拆任务和协作方式，听这份文件
