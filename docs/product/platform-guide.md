# 平台说明

这份文档只回答三个问题：

1. 这个项目现在做什么
2. 一次钱包风格蒸馏怎么走完
3. 配置和执行要从哪里看

## 1. 项目现在做什么

项目会把一个链上地址蒸馏成可运行的本地 skill。

它不是单纯输出一段分析文本，而是会：

- 拉取 AVE 钱包和代币数据
- 提取交易节奏、偏好、统计特征和市场上下文
- 用 Pi/Kimi 生成结构化策略规格
- 编译成本地 skill
- 验证 `primary` 和 `execute` 两个动作

## 2. 一次任务怎么走

```mermaid
flowchart LR
    A["输入地址"] --> B["distill_features"]
    B --> C["reflection_report"]
    C --> D["skill_build"]
    D --> E["execution_outcome"]
```

### `distill_features`

输出特征，不生成 skill。

### `reflection_report`

输出：

- `profile`
- `strategy`
- `execution_intent`
- `review`

### `skill_build`

输出：

- 编译后的 skill 包
- `confidence`
- `strategy_quality`
- `example_readiness`

### `execution_outcome`

输出：

- `dry_run_ready`
- `live_ready`
- 或阻塞原因

## 3. 两个动作分别做什么

### `primary`

- 无网络
- 负责决策和生成 `trade_plan`
- 会返回 `decision_trace`

### `execute`

- 有网络
- 负责调用 onchainos CLI 做执行前检查和 dry-run/live
- 会返回 `simulation_result`、`broadcast_results` 和 `tx_hashes`

## 4. 配置从哪里来

所有外部依赖都走环境变量，不写死在代码里。

关键分组：

- AVE：数据
- Pi/Kimi：反射
- onchainos / OKX：执行

CLI 和前端服务都会自动加载主工程目录里的 `.env`。公开版只保留 `.env.example`。

## 5. 如何判断这次任务是否有效

看这几个字段：

- `reflection_status`
- `fallback_used`
- `strategy_quality`
- `execution_readiness`
- `example_readiness`

常见判断：

- `fallback_used=false`
  - 说明真实走了 Pi/Kimi
- `execution_readiness=dry_run_ready`
  - 说明策略和执行链都已到 dry-run
- `execution_readiness=blocked_by_config`
  - 通常是执行钱包、密钥或余额不满足条件
