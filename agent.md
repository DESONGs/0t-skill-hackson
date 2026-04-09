# Agent Operating Guide

这份文档描述 `0t-skill_enterprise/` 当前的开发、维护和验收方式。

项目已经具备可运行基线，所以从现在开始，`mainagent + subagent` 的协作重点不再是“从零规划”，而是围绕真实代码做增量开发、回归验证、清理发布物，并持续把项目维持在可交付状态。

## 1. 固定原则

- 所有代码、脚本、文档和测试都只写在 `0t-skill_enterprise/`
- `mainagent` 负责调度、集成、验收和 QA 回路
- `subagent` 负责明确边界内的实现任务
- `subagent` 完成阶段目标后必须汇报，并等待 `mainagent` 再调度
- `ave-data-gateway` 是稳定层，不参与自动演化
- `analysis-core` 是演化层，允许进入反馈闭环
- 本项目不实现 trade、私钥管理或 WSS 实时链路

## 2. mainagent 职责

`mainagent` 是唯一调度者和验收者，负责：

- 维护阶段优先级和任务顺序
- 给各 subagent 指派具体开发任务
- 处理跨目录、跨模块依赖
- 审核阶段汇报
- 做跨模块集成测试和回归验证
- 判定交付结论
- 在问题未闭合前持续 loop，直到任务完成

`mainagent` 可以为解决系统性阻塞直接改代码，但默认不长期占用具体功能模块。

### mainagent 验收结论

- `accept`
- `revise`
- `blocked`
- `reassign`

## 3. subagent 职责

`subagent` 负责明确 owner 范围内的实现，不负责最终验收。

每个 subagent 必须：

- 只修改自己负责的目录
- 按既有 contract 和运行边界实现
- 在阶段结束时完成最小自测
- 向 `mainagent` 汇报修改、结果和风险
- 汇报后暂停，等待下一轮调度

不允许的行为：

- 擅自改公共 contract
- 擅自扩大 scope
- 未汇报先继续下一阶段
- 绕过 `mainagent` 修改其他 owner 区域

## 4. 当前分工

### subagent-1: runtime-and-bridge

owner:

- `bin/`
- `src/ot_skill_enterprise/root_cli.py`
- `src/ot_skill_enterprise/root_runtime.py`
- `src/ot_skill_enterprise/service_entrypoints.py`
- `src/ot_skill_enterprise/enterprise_bridge/`

职责：

- 单根目录入口
- vendored runtime bridge
- 服务启动入口
- 根级 smoke 路径

### subagent-2: ave-data-service

owner:

- `services/ave-data-service/`

职责：

- provider 选择和真实 AVE REST 调用
- service envelope
- 错误码、超时、服务启动行为

### subagent-3: gateway-skill

owner:

- `skills/ave-data-gateway/`
- `src/ot_skill_enterprise/gateway/`

职责：

- gateway skill package
- action wrapper
- artifact 写出

### subagent-4: analysis-core

owner:

- `skills/analysis-core/`
- `src/ot_skill_enterprise/analysis/`

职责：

- 计划生成
- 证据整合
- 报告输出
- 与 gateway artifact 的兼容读取

### subagent-5: workflows-and-quality

owner:

- `src/ot_skill_enterprise/workflows/`
- `workflows/`
- `tests/` 中 workflow、runtime、bridge 相关部分

职责：

- preset 定义
- workflow runtime
- 集成测试与 smoke 用例

### subagent-6: evolution-and-registry

owner:

- `src/ot_skill_enterprise/lab/`
- `src/ot_skill_enterprise/registry/`
- `tests/` 中演化闭环相关部分

职责：

- `analysis-core` 演化闭环
- case / proposal / submission
- 本地 registry 落盘和审计产物

## 5. 工作循环

### mainagent loop

1. 汇总上一轮结果与 blocker
2. 确认优先级和依赖关系
3. 向 subagent 派发阶段任务
4. 接收阶段汇报
5. 做交叉验证和 QA
6. 给出 `accept / revise / blocked / reassign`
7. 继续下一轮，直到目标完成

### subagent loop

1. 接收阶段任务
2. 在 owner 范围内实现
3. 做最小自测
4. 提交阶段汇报
5. 等待 `mainagent` 下一轮调度

## 6. 汇报要求

阶段汇报统一放在：

- `reports/<phase>/<agent-name>.md`

每份汇报至少包含：

- 目标
- 已完成项
- 修改文件
- 自测命令和结果
- 剩余风险
- 需要 `mainagent` 决策的事项
- 建议下一步

## 7. QA 基线

`mainagent` 每轮至少检查：

- 修改是否落在允许目录
- 是否破坏 `gateway -> analysis-core` 边界
- 是否把 AVE 特定字段泄漏进分析层
- 是否引入 trade 或 WSS
- 是否补齐测试和必要文档
- 是否保留了可复现的启动与验证方式

## 8. 完成定义

一个阶段只有在下面条件都满足时才能算完成：

- 代码已经落在约定目录
- 自测通过
- 主线程验收通过
- 文档与脚本同步更新
- 没有未声明的 blocker

项目对外发布前，还必须完成：

- `./scripts/verify.sh`
- README 与 docs 索引校准
- `.env.example`、启动脚本、staging 流程可用
- 运行产物和缓存已清理
