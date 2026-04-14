# 0T-Skill 系统架构

## 总体架构

0T-Skill 采用分层分平面架构，将数据、推理、编译、执行职责严格解耦。

```
┌─────────────────────────────────────────────────────────────────┐
│                        控制平面 (Control Plane)                   │
│   CLI (ot-enterprise)  ·  HTTP API  ·  Frontend Dashboard       │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐   │
│  │ 数据平面  │  │ 反射平面  │  │ 编译平面  │  │   执行平面    │   │
│  │ (AVE)    │  │ (Pi/Kimi)│  │(Skill-OS)│  │(OKX OnchainOS)│  │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └─────┬────────┘   │
│       │              │              │               │           │
│  ┌────▼──────────────▼──────────────▼───────────────▼──────┐   │
│  │              运行时协调层 (Runtime Coordinator)            │   │
│  │     Session · Invocation · Transcript · Pipeline          │   │
│  └──────────────────────────┬───────────────────────────────┘   │
│                             │                                   │
│  ┌──────────────────────────▼───────────────────────────────┐   │
│  │              上下文与产物层 (Context & Artifact)            │   │
│  │   Stage Cache · Derived Memory · Review Hints · Ledger    │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│                 工作区持久层 (.ot-workspace)                      │
│   candidates · registry · runtime-sessions · data · artifacts   │
└─────────────────────────────────────────────────────────────────┘
```

## 四平面设计

### 数据平面 — AVE

职责：提供蒸馏所需的全部链上数据。

```
AVE REST API
├── discover_tokens    → 代币发现与搜索
├── inspect_token      → 代币详情、持有者快照、风险评估
├── inspect_market     → 市场数据、K 线、流动性
├── inspect_wallet     → 钱包画像、持仓、历史交易
└── review_signals     → 链上信号与异常检测
```

设计约束：
- 蒸馏与回测阶段**只允许 AVE** 作为数据源
- 不从 OnchainOS 读取市场、信号或持仓数据，避免双数据路径导致口径漂移

### 反射平面 — Pi/Kimi

职责：对蒸馏特征进行 LLM 结构化推理。

```
PiReflectionService
├── 构建 ReflectionJobSpec（compact_input + output_schema + 约束）
├── 启动 Pi Runtime 子进程（TypeScript）
├── 等待结构化 JSON 输出
├── 解析为 profile + strategy + execution_intent + review
└── 失败时 fallback 到 WalletStyleExtractor（纯规则提取）
```

三级降级策略：
1. **Mock 模式**：使用预设响应，开发调试用
2. **Live 模式**：调用 Pi/Kimi 大模型实时推理
3. **Fallback 模式**：LLM 失败时回退到规则提取器

### 编译平面 — Skill-OS

职责：将蒸馏结果编译为标准化 Skill 包。

```
SkillPackageCompiler
├── compile    → 生成 Skill 包文件结构
├── validate   → 契约验证（结构 + 内容完整性）
├── promote    → 晋升到本地 skills 注册表
└── smoke      → 冒烟测试验证脚本可执行
```

### 执行平面 — OKX OnchainOS

职责：将策略 Skill 的交易计划映射为链上操作。

```
OnchainOS CLI 执行链路
├── wallet login/status      → 钱包认证
├── wallet addresses/balance → 余额查询
├── security scan            → 代币安全扫描
├── quote                    → 报价获取
├── approval                 → 授权审批
├── simulate                 → 交易模拟（dry-run）
└── broadcast                → 链上广播（live）
```

---

## Agent 框架

### 核心 Agent 模式

0T-Skill 的 Agent 框架围绕 **Skill 生命周期** 设计，不同于传统的对话式 Agent。

```
                    ┌─────────────────────┐
                    │   WalletStyleAgent   │
                    │  (蒸馏任务编排者)      │
                    └─────────┬───────────┘
                              │
              ┌───────────────┼───────────────┐
              │               │               │
    ┌─────────▼──────┐ ┌─────▼──────┐ ┌──────▼─────────┐
    │  DataAgent     │ │ ReflectAgent│ │ ExecutionAgent │
    │ (AVE 数据采集)  │ │ (LLM 推理)  │ │ (链上执行)     │
    └────────────────┘ └────────────┘ └────────────────┘
```

### WalletStyleDistillationService

蒸馏服务是系统的核心编排器，职责包括：

1. **任务调度**：接收钱包地址，创建蒸馏 Job，分配唯一 Job ID
2. **阶段管理**：串联四个阶段（distill → reflect → build → execute），每阶段产出不可变的 stage artifact
3. **上下文组装**：从 stage artifact 构建 LLM 需要的 compact_input（≤6KB）
4. **重试与降级**：reflection 失败时自动重试，超限后 fallback 到规则提取
5. **缓存复用**：通过 StageCacheRegistry 实现阶段级缓存，相同输入不重复计算

### RuntimeRunCoordinator

运行时协调器管理 Agent 的会话与执行：

```
RuntimeRunCoordinator
├── session_store       → 会话持久化
├── executor            → 子进程执行器
├── translator          → 运行时事件翻译
└── registry_root       → Run 注册与回溯
```

工作流程：
1. 创建 RuntimeSession，分配 session_id
2. 创建 RuntimeInvocation，记录调用上下文
3. 通过 RuntimeExecutor 执行子进程
4. DefaultRuntimeTranslator 将子进程输出翻译为标准化事件
5. RunIngestionPipeline 将运行结果持久化并关联到 Candidate 生命周期

### Candidate 生命周期

```
┌─────────┐    ┌──────────┐    ┌──────────┐    ┌─────────┐    ┌────────┐
│ Distill │ →  │ Compile  │ →  │ Validate │ →  │ Promote │ →  │ Smoke  │
│ 蒸馏     │    │ 编译      │    │ 验证      │    │ 晋升     │    │ 冒烟    │
└─────────┘    └──────────┘    └──────────┘    └─────────┘    └────────┘
                                                                  │
                                                            ┌─────▼─────┐
                                                            │ Execute   │
                                                            │ dry/live  │
                                                            └───────────┘
```

---

## 四阶段蒸馏管线

### Stage 1: distill_features

```python
输入: wallet_address + chain
操作:
  - AVE inspect_wallet → 钱包画像 + 持仓 + 历史交易
  - AVE inspect_token × N → 焦点代币详情（并行）
  - AVE inspect_market × N → 市场数据（并行）
  - AVE review_signals → 链上信号
  - 交易配对 (FIFO) → 胜率/盈亏比/持仓周期
  - 市场上下文 → 动量/波动率/宏观状态
  - 信号过滤 → 入场因子/风控规则
  - compact_input 组装（≤6KB）
输出: stage_distill_features.json
```

### Stage 2: reflection_report

```python
输入: compact_input + output_schema + constraints
操作:
  - 构建 ReflectionJobSpec
  - Pi/Kimi 结构化反射
  - 解析输出为 profile + strategy + execution_intent + review
  - 质量检查（拒绝 generic/placeholder 输出）
输出: stage_reflection.json
```

### Stage 3: skill_build

```python
输入: reflection 输出 + 蒸馏统计
操作:
  - 回测验证（信号回放）
  - 置信度评分（strategy_quality + data_quality + backtest_score）
  - SkillPackageCompiler 编译完整 Skill 包
  - 契约验证 + 结构验证
  - 晋升到本地 skills 注册表
输出: stage_build.json + 完整 Skill 包目录
```

### Stage 4: execution_outcome

```python
输入: Skill 包 + execution_intent
操作:
  - prepare_only: 仅准备执行计划
  - dry_run: 通过 OnchainOS CLI 模拟执行
  - live: 通过 OnchainOS CLI 广播上链
  固定链路: login → balance → security → quote → approval → simulate → broadcast
输出: stage_execution.json
```

---

## 上下文分层

系统采用 artifact-backed 的分层上下文管理：

| 层次 | 内容 | 生命周期 |
|------|------|---------|
| 静态指令 | 固定的 stage/reflection 提示词 | 全局不变 |
| Canonical Ledger | Job 元信息、stage 状态、lineage | 随 Job 创建 |
| Stage Artifacts | 四阶段不可变快照 | 阶段完成后冻结 |
| Ephemeral Envelopes | reflection 调用前临时注入的上下文 | 单次 reflection 调用内有效 |
| Derived Memory | 可复用的风格短记忆与提示 | 跨 Job 复用 |

### 上下文预算

LLM 调用的输入受严格预算约束：

```
compact_input 上限: 6KB (≈2000 tokens)

分配:
  wallet_summary     0.3 KB
  holdings Top 5     0.5 KB
  recent_activity    1.0 KB
  derived_stats      0.5 KB
  market_context     0.9 KB
  signal_context     0.5 KB
  token_snapshots    0.6 KB
  signals Top 5      0.7 KB
```

---

## 数据流

```
                         AVE REST API
                              │
                    ┌─────────▼──────────┐
                    │  AveDataProvider    │
                    │  Adapter            │
                    └─────────┬──────────┘
                              │
                   ┌──────────▼───────────┐
                   │ WalletStyleDistill   │
                   │ ationService         │
                   │                      │
                   │  ┌─────────────┐     │
                   │  │ M1 数据采集  │     │
                   │  │ (并行化)     │     │
                   │  └──────┬──────┘     │
                   │         │            │
                   │  ┌──────▼──────┐     │
                   │  │ M2-M4 预处理 │     │
                   │  │ (并行化)     │     │
                   │  └──────┬──────┘     │
                   │         │            │
                   │  ┌──────▼──────┐     │
                   │  │ compact_input│    │
                   │  │ 组装 (≤6KB) │     │
                   │  └──────┬──────┘     │
                   └─────────┼────────────┘
                             │
                   ┌─────────▼───────────┐
                   │ PiReflectionService │
                   │ (Pi Runtime 子进程)  │
                   └─────────┬───────────┘
                             │
                   ┌─────────▼───────────┐
                   │ SkillPackageCompiler │
                   │ + BacktestEngine     │
                   └─────────┬───────────┘
                             │
                   ┌─────────▼───────────┐
                   │ OnchainOS CLI       │
                   │ (dry-run / live)     │
                   └─────────────────────┘
```

## 并行执行策略

### M1 内部并行

```
inspect_wallet ──┬──→ inspect_token × 4 (ThreadPool)  ──→ 汇总
                 ├──→ review_signals                   ──→ 汇总
                 └──→ inspect_market × 4 (ThreadPool)  ──→ 汇总
```

### M2/M3/M4 并行

M1 完成后，三个纯计算任务完全独立：

| 任务 | 输入 | 预计耗时 |
|------|------|---------|
| M2 交易配对 + 统计 | full_activity_history | ~1s |
| M3 市场上下文压缩 | token_profiles + market_data | ~1s |
| M4 风控过滤器构建 | token_profiles + signals | ~1s |

### 关键路径

```
M1(15s) → [M2+M3+M4 并行](1s) → M4 入场因子(1s) → M5 LLM(5-10s) → M6(1s)
总计预计: 23-28s
```

## 技术决策记录

| 决策 | 选择 | 理由 |
|------|------|------|
| 并行化方案 | ThreadPoolExecutor | AVE Provider 是阻塞 subprocess，asyncio 无法加速 |
| 市场数据给 LLM | 预计算摘要指标 | 原始 K 线太大（16KB+），会导致上下文爆炸 |
| compact_input 上限 | 6KB | Pi maxTokens=3000(output)，input 安全区约 4000 tokens |
| 买卖配对算法 | FIFO | 链上交易天然有时间序，FIFO 最直觉 |
| 入场因子蒸馏 | 频率统计 | 样本量 <20 时回归分析无统计显著性 |
| 执行适配层 | OnchainOS CLI | 执行能力成熟，与 AVE-only 数据平面解耦 |
| 数据边界 | AVE-only | 避免双路径导致蒸馏、回测、执行口径漂移 |
