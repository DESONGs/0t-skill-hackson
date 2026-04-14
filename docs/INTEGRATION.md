# AVE Skill、OKX OS Skill 与 Skill-OS 框架集成说明

## 三者关系概览

0T-Skill 系统的核心架构由三个关键组件协同工作：

```
┌─────────────────────────────────────────────────────────────┐
│                  Skill-OS 框架（自研编排层）                    │
│                                                             │
│   ┌───────────────┐                    ┌────────────────┐   │
│   │   AVE Skill   │  ← 数据输入        │ OKX OS Skill   │   │
│   │  （数据平面）   │                    │ （执行平面）     │   │
│   └───────┬───────┘                    └───────┬────────┘   │
│           │                                     │           │
│           │         ┌──────────────┐            │           │
│           └────────→│  蒸馏引擎     │←───────────┘           │
│                     │  + LLM 反射   │                       │
│                     │  + Skill 编译  │                       │
│                     └──────┬───────┘                        │
│                            │                                │
│                     ┌──────▼───────┐                        │
│                     │  Skill 包     │                        │
│                     │  （标准产物）  │                        │
│                     └──────────────┘                        │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

- **AVE Skill**：系统的"眼睛"——负责看见链上世界的所有数据
- **OKX OS Skill**：系统的"手"——负责在链上执行实际交易操作
- **Skill-OS 框架**：系统的"大脑"——负责将数据蒸馏为策略并编排执行

---

## AVE Skill — 数据平面

### 定位与作用

AVE 是 0T-Skill 的**唯一数据源**。蒸馏、分析、回测阶段所需的全部链上数据都通过 AVE 获取，确保数据口径的一致性。

### 提供的能力

| AVE 接口 | 在蒸馏中的作用 | 消费阶段 |
|----------|---------------|---------|
| `inspect_wallet` | 获取钱包持仓、余额、历史交易流水 | M1 数据采集 |
| `inspect_token` | 获取代币详情、持有者分布、风险评估（合约漏洞扫描） | M1 数据采集 → M4 风控过滤 |
| `inspect_market` | 获取 K 线、流动性、成交量 | M1 数据采集 → M3 市场上下文 |
| `review_signals` | 获取链上异常信号（大额转账、鲸鱼动向） | M1 数据采集 → M4 信号过滤 |
| `discover_tokens` | 代币搜索与发现 | 辅助代币解析 |

### 适配层实现

AVE 数据通过 `AveDataProviderAdapter` 接入 Skill-OS 框架：

```
AveDataProviderAdapter
├── 统一的 run(action_name, payload) 接口
├── 请求模型验证（Pydantic）
├── 响应归一化（统一 envelope 格式）
├── 产物持久化（每次调用保存 artifact JSON）
└── 错误标准化（HTTP / 验证 / 内部错误分类）
```

**适配层的关键设计**：

1. **请求模型验证**：每个 AVE 接口对应一个 Pydantic 请求模型（`InspectWalletRequest`、`InspectTokenRequest` 等），确保调用参数合法
2. **响应归一化**：将 AVE 返回的各种格式统一为 `ProviderActionResult`，包含 `ok`、`summary`、`response`、`error`、`artifacts`
3. **产物追溯**：每次 AVE 调用的请求和响应都以 `{action}-{request_id}.json` 形式保存到工作区，支持事后审计

### 数据流转

```
用户输入钱包地址
       │
       ▼
AveDataProviderAdapter.run("inspect_wallet", {wallet, chain})
       │
       ▼
AveDataServiceClient → AVE REST API
       │
       ▼
ProviderActionResult {
  ok: true,
  provider: "ave",
  action: "inspect_wallet",
  summary: "inspected wallet 0x9048f6...",
  response: { holdings, activity, balance, ... },
  artifacts: [{ uri: "data/inspect_wallet-abc123.json" }]
}
       │
       ▼
蒸馏引擎提取 focus_tokens、historical_trades、holdings
       │
       ▼
并行调用 inspect_token × N + inspect_market × N + review_signals
       │
       ▼
compact_input 组装（≤6KB）→ 送入 LLM 反射
```

### AVE 在两层数据架构中的角色

```
┌──────────────────────── 完整数据层 ────────────────────────┐
│  AVE 原始返回数据（可能数 MB）                               │
│  - full_activity_history (100+ 条)                        │
│  - 完整 token_profiles (含 holder_snapshot)                │
│  - 完整 market_data (含 OHLCV K 线)                       │
│  用途：M2 统计计算、M4 入场因子分析、M6 回测                 │
└─────────────────────────┬─────────────────────────────────┘
                          │ 压缩 + 截断
┌─────────────────────────▼─────────────────────────────────┐
│                   紧凑数据层（≤6KB）                         │
│  用途：Pi/Kimi LLM reflection compact_input               │
│  - wallet_summary (0.3KB)                                 │
│  - holdings Top 5 (0.5KB)                                 │
│  - recent_activity Top 8 (1.0KB)                          │
│  - derived_stats + M2 统计 (0.5KB)                        │
│  - market_context 摘要 (0.9KB)                            │
│  - signal_context 摘要 (0.5KB)                            │
└───────────────────────────────────────────────────────────┘
```

---

## OKX OS Skill — 执行平面

### 定位与作用

OKX OnchainOS 是 0T-Skill 的**唯一执行层**。当 Skill 包编译完成且通过回测验证后，交易操作通过 OnchainOS CLI 执行。

### 提供的能力

| OnchainOS 操作 | 作用 | 阶段 |
|---------------|------|------|
| `wallet login/status` | 钱包认证与状态检查 | 执行前预检 |
| `wallet addresses` | 获取钱包地址列表 | 执行前预检 |
| `wallet balance` | 查询余额，验证资金充足 | 执行前预检 |
| `security scan` | 目标代币安全扫描 | 预检：合约风险二次确认 |
| `quote` | 获取 DEX 报价（路由、滑点） | 交易准备 |
| `approval` | 代币授权审批 | 交易准备 |
| `simulate` (dry-run) | 模拟执行，验证交易可行性 | dry-run 模式 |
| `broadcast` (live) | 签名并广播交易上链 | live 模式 |

### 执行链路

Skill 包的 `execute.py` 脚本通过 OnchainOS CLI 执行完整交易流程：

```
┌──────────┐    ┌──────────┐    ┌──────────┐    ┌─────────┐
│ Preflight│ →  │ Prepare  │ →  │ Simulate │ →  │ Execute │
│ 预检      │    │ 准备      │    │ 模拟      │    │ 执行     │
└────┬─────┘    └────┬─────┘    └────┬─────┘    └────┬────┘
     │               │               │               │
  wallet           security        quote →         broadcast
  login/status     scan            simulate        (需人工审批)
  addresses        balance
```

### 执行模式

| 模式 | 行为 | 适用场景 |
|------|------|---------|
| `prepare_only` | 仅生成执行计划，不调用 OnchainOS | 策略审查 |
| `dry_run` | 通过 OnchainOS 模拟执行，不上链 | 验证交易可行性 |
| `live` | 签名并广播交易上链（需 OKX 凭证） | 实盘交易 |

### OKX 凭证与安全

执行平面需要以下 OKX 凭证（通过环境变量配置）：

| 环境变量 | 用途 |
|---------|------|
| `OKX_API_KEY` | OKX API 密钥 |
| `OKX_SECRET_KEY` | OKX 私钥 |
| `OKX_PASSPHRASE` | OKX 密码短语 |

安全约束：
- 所有 live 执行需要 `requires_explicit_approval: true`
- 单笔交易 USD 上限由 `OT_ONCHAINOS_LIVE_CAP_USD` 控制（默认 $10）
- 最小交易腿金额由 `OT_ONCHAINOS_MIN_LEG_USD` 控制（默认 $5）
- 代币安全扫描是强制预检步骤

### 支持的链

| 链 | Chain ID | 状态 |
|----|----------|------|
| BSC | 56 | 已验证 |
| Ethereum | 1 | 支持 |
| Base | 8453 | 支持 |
| Polygon | 137 | 支持 |
| Arbitrum | 42161 | 支持 |
| Optimism | 10 | 支持 |

---

## Skill-OS 框架 — 自研编排层

### 定位与作用

Skill-OS 是连接 AVE（数据）和 OKX OnchainOS（执行）的**自研核心框架**。它不直接与链上交互，而是负责：

1. **蒸馏编排**：协调 M1-M6 六个模块的执行顺序与数据流转
2. **LLM 反射调度**：构建 compact_input，管理 Pi/Kimi 调用，处理重试与降级
3. **Skill 编译**：将蒸馏结果打包为标准化 Skill 包
4. **生命周期管理**：管理 Candidate 从蒸馏到晋升的完整生命周期
5. **上下文管理**：维护分层上下文（Stage Cache、Derived Memory、Review Hints）
6. **执行桥接**：将 Skill 的 execution_intent 翻译为 OnchainOS CLI 调用

### 框架组件

```
Skill-OS Framework
│
├── Control Plane (控制平面入口)
│   ├── CLI (ot-enterprise)
│   │   ├── style distill    → 启动蒸馏
│   │   ├── style list       → 列出已有风格
│   │   ├── candidate list   → 列出候选 Skill
│   │   ├── candidate compile → 编译 Skill 包
│   │   ├── candidate validate → 验证 Skill 包
│   │   └── candidate promote → 晋升到注册表
│   └── HTTP API + Frontend
│
├── Style Distillation (蒸馏引擎)
│   ├── service.py             → 主编排器（WalletStyleDistillationService）
│   ├── trade_pairing.py       → M2: FIFO 交易配对
│   ├── market_context.py      → M3: 市场上下文预计算
│   ├── signal_filters.py      → M4: 信号过滤与风控
│   ├── extractors.py          → M5: LLM 提取 prompt
│   ├── backtesting.py         → M6: 信号回放回测
│   ├── context.py             → 上下文组装与缓存
│   └── models.py              → 数据模型定义
│
├── Reflection (反射服务)
│   ├── service.py             → PiReflectionService
│   └── models.py              → ReflectionJobSpec / Result
│
├── Skills Compiler (Skill 编译器)
│   ├── compiler.py            → SkillPackageCompiler
│   ├── wallet_style_runtime.py → 钱包风格 Skill 运行时
│   └── models.py              → Candidate / Package 模型
│
├── Execution (执行适配)
│   └── onchainos_cli.py       → OnchainOS CLI 封装
│
├── Runtime (运行时)
│   ├── coordinator.py         → RuntimeRunCoordinator
│   ├── executor.py            → 子进程执行器
│   ├── translator.py          → 事件翻译器
│   └── pi/                    → Pi Runtime 适配
│       ├── adapter.py
│       ├── session.py
│       ├── bootstrap.py
│       └── tool_bridge.py
│
├── Providers (数据提供者)
│   ├── ave/adapter.py         → AVE 适配层
│   ├── contracts/             → Provider 接口契约
│   └── registry/              → Provider 注册
│
├── Runs (运行管理)
│   ├── pipeline.py            → RunIngestionPipeline
│   ├── recorder.py            → 运行记录器
│   └── artifacts/             → 产物管理
│
└── QA (质量保障)
    ├── evaluator.py           → 评估器
    ├── feedback.py            → 反馈收集
    └── diagnostics/           → 诊断工具
```

### 核心编排流程

Skill-OS 框架的编排是整个系统的核心价值所在。以下是 `WalletStyleDistillationService.distill_wallet_style()` 的编排逻辑：

```
def distill_wallet_style(wallet, chain):

    # ═══ Phase 1: AVE 数据采集 (M1) ═══
    # Skill-OS 调用 AVE Skill 获取原始数据

    wallet_profile = ave_provider.inspect_wallet(wallet, chain)
    focus_tokens = pick_focus_tokens(wallet_profile)

    # 并行拉取代币详情 + 市场数据 + 信号
    with ThreadPoolExecutor(max_workers=6):
        token_profiles = [inspect_token(ref) for ref in focus_tokens]     # AVE
        market_data = [inspect_market(ref) for ref in focus_tokens]       # AVE
        signals = review_signals(chain)                                    # AVE

    # ═══ Phase 2: 纯计算预处理 (M2+M3+M4) ═══
    # Skill-OS 自研的统计与分析模块

    with ThreadPoolExecutor(max_workers=3):
        trade_stats = pair_trades(history)              # FIFO 配对
        market_ctx = compute_market_context(market_data) # 动量/波动率
        risk_filters = build_risk_filters(token_profiles) # 风控过滤

    entry_factors = distill_entry_factors(trades, market_ctx)

    # ═══ Phase 3: compact_input 组装 ═══
    # Skill-OS 将完整数据压缩为 ≤6KB 的 LLM 输入

    compact_input = assemble_compact_input(
        wallet_profile, token_profiles, signals,
        trade_stats, market_ctx, risk_filters, entry_factors
    )  # 强制 ≤6KB

    # ═══ Phase 4: LLM 反射 (M5) ═══
    # Skill-OS 调用 Pi/Kimi 进行结构化推理

    reflection_result = pi_reflection_service.run(
        compact_input, output_schema, constraints
    )
    profile, strategy, execution_intent = parse(reflection_result)

    # ═══ Phase 5: Skill 编译 + 回测 (M5+M6) ═══
    # Skill-OS 编译标准化 Skill 包

    backtest = run_backtest(strategy, completed_trades, market_ctx)
    skill_package = compiler.compile(profile, strategy, execution_intent)
    compiler.validate(skill_package)
    compiler.promote(skill_package)

    # ═══ Phase 6: 执行就绪 ═══
    # Skill-OS 桥接到 OKX OnchainOS 执行

    if mode == "dry_run":
        onchainos_cli.simulate(execution_intent)    # OKX OS Skill
    elif mode == "live":
        onchainos_cli.broadcast(execution_intent)   # OKX OS Skill
```

### Skill-OS 的桥接作用

Skill-OS 框架在 AVE 和 OKX OnchainOS 之间扮演核心桥接角色：

```
AVE (数据)                    Skill-OS (编排)               OKX OnchainOS (执行)
                                                           
inspect_wallet ──────→ M1 数据采集                          
inspect_token  ──────→ │                                   
inspect_market ──────→ │                                   
review_signals ──────→ │                                   
                       ▼                                   
                  M2-M4 预处理                              
                       │                                   
                       ▼                                   
                  M5 LLM 蒸馏                              
                       │                                   
                       ▼                                   
                  M6 回测验证                               
                       │                                   
                       ▼                                   
                  Skill 包编译                              
                       │                                   
                       ▼                                   
              execution_intent ────────→ wallet login      
                                ────────→ security scan    
                                ────────→ quote            
                                ────────→ simulate/broadcast
```

### 严格的平面隔离

Skill-OS 框架强制执行以下隔离规则：

| 规则 | 说明 |
|------|------|
| AVE-only 数据读取 | 蒸馏和回测只从 AVE 读取数据 |
| OnchainOS-only 执行 | 链上操作只通过 OnchainOS CLI |
| 无数据回灌 | OnchainOS 的 market/signal/PnL 数据不回灌到蒸馏输入 |
| 无跨平面泄漏 | AVE 不执行交易，OnchainOS 不提供蒸馏数据 |

这种设计确保了：
- **数据一致性**：蒸馏和回测使用相同的数据源，避免口径漂移
- **职责清晰**：每个外部依赖有且仅有一个明确职责
- **可替换性**：理论上可以替换 AVE 或 OnchainOS 而不影响核心蒸馏逻辑

---

## Skill 包标准

Skill-OS 框架定义了标准化的 Skill 包格式，使 AVE 的数据和 OKX 的执行能力在统一的产物结构中结合：

```
skill-package/
│
├── SKILL.md                    ← 可读说明（来自 LLM 反射输出）
│
├── manifest.json               ← 包元数据
│   ├── wallet_style_profile    ← AVE 数据蒸馏产出
│   ├── strategy_spec           ← LLM 反射产出 + 回测验证
│   └── execution_intent        ← OKX OnchainOS 执行配置
│
├── actions.yaml                ← 动作定义（primary + execute）
│
├── agents/interface.yaml       ← Agent 接口描述
│
├── references/                 ← 蒸馏参考数据
│   ├── style_profile.json      ← 完整交易风格画像
│   ├── strategy_spec.json      ← 完整策略规格
│   ├── execution_intent.json   ← 完整执行意图
│   └── token_catalog.json      ← 焦点代币目录
│
└── scripts/
    ├── primary.py              ← 策略决策脚本（读取市场 → 判断是否入场）
    └── execute.py              ← 交易执行脚本（调用 OnchainOS CLI）
```

### 两个脚本的分工

| 脚本 | 网络权限 | 数据源 | 职责 |
|------|---------|--------|------|
| `primary.py` | 可访问 AVE | AVE 市场数据 | 读取实时数据，评估入场条件，输出交易计划 |
| `execute.py` | 可访问 OnchainOS | OnchainOS CLI | 接收交易计划，执行 security → quote → approve → swap |

这种分离确保了 **数据判断** 和 **交易执行** 的解耦，任一侧可以独立迭代。

---

## 环境变量汇总

### AVE 相关

| 变量 | 用途 | 必需 |
|------|------|------|
| `AVE_API_KEY` | AVE API 认证密钥 | 是 |
| `API_PLAN` | AVE API 套餐级别 | 否 |
| `AVE_DATA_PROVIDER` | 数据提供者标识 | 否 |
| `AVE_DATA_SERVICE_URL` | AVE 数据服务地址（默认 localhost:8080） | 否 |

### OKX 相关

| 变量 | 用途 | 必需 |
|------|------|------|
| `OKX_API_KEY` | OKX API 密钥 | live 模式必需 |
| `OKX_SECRET_KEY` | OKX 私钥 | live 模式必需 |
| `OKX_PASSPHRASE` | OKX 密码短语 | live 模式必需 |
| `ONCHAINOS_HOME` | OnchainOS 安装目录 | 否 |
| `OT_ONCHAINOS_CLI_BIN` | OnchainOS CLI 二进制路径 | 否 |
| `OT_ONCHAINOS_LIVE_CAP_USD` | 单笔交易 USD 上限 | 否（默认 $10） |

### LLM 反射相关

| 变量 | 用途 | 必需 |
|------|------|------|
| `KIMI_API_KEY` | Kimi K2 模型密钥 | 是 |
| `OT_PI_REFLECTION_MODEL` | 反射模型选择 | 否 |
| `OT_PI_REFLECTION_MOCK` | 启用 mock 模式 | 否 |
