# System Overview

## 1. 项目目标

`0t-skill_enterprise` 是一个新的实现工作区，用来落地：

- AVE 作为外部数据源
- `skill_enterprise` 作为分析、编排、反馈和演化核心

本项目的最终系统由三层组成：

1. `ave-data-service`
   - 对接 AVE REST
   - 只负责取数
2. `ave-data-gateway`
   - enterprise 内部 skill
   - 只负责把数据服务包装成稳定 action
3. `analysis-core`
   - enterprise 分析主脑
   - 负责编排、分析、报告、自我迭代

## 2. 非目标

v1 不做：

- trade
- self-custody / proxy wallet
- AVE WSS
- 常驻多代理产品能力
- 大规模 fork `skill_enterprise` 内核

## 3. 固定边界

- AVE 只负责数据
- `ave-data-gateway` 只负责数据动作包装
- `analysis-core` 只负责分析和报告
- 只有 `analysis-core` 可以进入反馈演化闭环

## 4. 代码落点

- `services/`
  - 外部服务实现
- `skills/`
  - skill package
- `workflows/`
  - preset 和 service 流程
- `src/ot_skill_enterprise/shared/`
  - contract 和 client
- `tests/`
  - 单测、集成、验收、演化闭环

## 5. 交付基线

v1 交付物固定为：

- 一个 AVE 数据服务
- 两个正式 skill
- 三个 preset
- 一条 `analysis-core` 的自我迭代闭环
