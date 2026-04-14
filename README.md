# 0T-Skill — 链上钱包交易风格蒸馏与自动执行系统

> 输入任意链上地址，自动蒸馏其交易风格为可执行的 Skill，并通过 OKX OS Skill 实现实时链上交易。

## 项目概述

0T-Skill 是一个端到端的链上交易策略蒸馏系统。它将链上钱包的历史交易行为通过 **AVE 数据服务** 采集、**LLM 反射式推理** 分析、**Skill 编译器** 打包，最终生成可通过 **OKX OnchainOS Skill** 直接执行链上交易的标准化 Skill 包。

### 核心闭环

```
输入钱包地址 → AVE 数据采集 → 交易风格蒸馏 → LLM 反射分析 → Skill 编译打包 → OKX OS Skill 执行交易
```

## 关键能力

- **地址级交易风格蒸馏**：输入任意 EVM 链上地址，自动分析其持仓偏好、交易频率、风险偏好、入场因子、止盈止损模式
- **AVE 数据集成**：通过 AVE Skill 获取钱包画像、代币详情、市场数据、链上信号，作为蒸馏的唯一数据源
- **LLM 反射式推理**：使用 Pi/Kimi 大模型对蒸馏特征进行结构化审查，生成交易风格画像与策略规格
- **标准化 Skill 包输出**：生成包含 `SKILL.md`、`manifest.json`、`actions.yaml`、`primary.py`、`execute.py` 的完整 Skill 包
- **OKX OS Skill 链上执行**：通过 OnchainOS CLI 适配层实现 dry-run 模拟与 live 实盘交易

## 技术栈

| 层次 | 技术 | 职责 |
|------|------|------|
| 数据平面 | AVE REST API | 钱包、代币、市场、信号数据采集 |
| 反射平面 | Pi Runtime + Kimi K2 | LLM 结构化推理与交易风格审查 |
| 编译平面 | Skill-OS Compiler | Skill 包生成、验证、晋升 |
| 执行平面 | OKX OnchainOS CLI | 链上交易模拟与广播 |
| 控制平面 | Python CLI + HTTP API | 统一入口与流程编排 |
| 展示层 | 原生 HTML/CSS/JS Dashboard | 运行状态与 Skill 产物浏览 |

## 目录结构

```text
.
├── README.md                           # 项目主文档
├── CONFIGURATION.md                    # 环境变量与依赖恢复说明
├── docs/
│   ├── PROJECT_INTRODUCTION.md         # 项目详细介绍（含示例 Skill 说明）
│   ├── ARCHITECTURE.md                 # Agent 框架与系统架构
│   └── INTEGRATION.md                  # AVE / OKX OS Skill 集成说明
├── agent.md                            # 代理协作约束
└── 0t-skill_hackson_v2ing/
    ├── frontend/                       # Dashboard 前端
    ├── skills/                         # 蒸馏产出的示例 Skill 包
    │   ├── wallet-style-test-bsc-9048f6-...   # 示例 1: BSC Memecoin Scalper
    │   └── wallet-style-test-bsc-bac453-...   # 示例 2: BSC Microcap Day-Scalper
    └── src/ot_skill_enterprise/        # 核心业务源码
        ├── control_plane/              # CLI 与 API 入口
        ├── style_distillation/         # 交易风格蒸馏引擎
        ├── reflection/                 # LLM 反射服务
        ├── skills_compiler/            # Skill 包编译器
        ├── execution/                  # OKX OnchainOS 执行适配
        ├── providers/ave/              # AVE 数据适配层
        ├── runtime/                    # 运行时协调器
        ├── runs/                       # Run 生命周期管理
        ├── qa/                         # 质量评估
        └── shared/                     # 共享契约与客户端
```

## 示例 Skill 产物

本仓库包含两个真实地址蒸馏生成的示例 Skill：

### 示例 1 — BSC Memecoin Scalper

- **地址**：`0x9048f6...662b4dbfef`
- **风格标签**：高频 BSC Memecoin 剥头皮交易者
- **特征**：21 秒平均持仓、同分钟爆发执行、金字塔加仓、容忍 -61% 回撤
- **置信度**：0.5（medium）
- **胜率**：65.3%，盈亏比 2.34

### 示例 2 — BSC Microcap Day-Scalper

- **地址**：`0xbac453...f2567a89`
- **风格标签**：BSC 微型市值日内剥头皮交易者
- **特征**：同分钟爆发入场、金字塔加仓（平均 2.95 次分批）、容忍 -85% 回撤
- **置信度**：0.9（high）

每个 Skill 包包含完整的结构化产物：

```text
skill-package/
├── SKILL.md                   # 可读的策略说明
├── manifest.json              # 元数据与策略规格
├── actions.yaml               # 可执行动作定义
├── agents/interface.yaml      # Agent 接口描述
├── references/                # 蒸馏参考数据
│   ├── style_profile.json     # 交易风格画像
│   ├── strategy_spec.json     # 策略规格
│   ├── execution_intent.json  # 执行意图
│   └── token_catalog.json     # 代币目录
└── scripts/
    ├── primary.py             # 策略决策脚本
    └── execute.py             # 交易执行脚本
```

## 快速了解

| 文档 | 内容 |
|------|------|
| [项目介绍](docs/PROJECT_INTRODUCTION.md) | 项目背景、核心流程、示例 Skill 详解 |
| [系统架构](docs/ARCHITECTURE.md) | Agent 框架、四阶段流水线、分层设计 |
| [集成说明](docs/INTEGRATION.md) | AVE Skill 与 OKX OS Skill 如何与 Skill-OS 框架协同 |
| [环境配置](CONFIGURATION.md) | 恢复完整运行链路的环境变量与依赖 |

## 许可证

Apache License 2.0
