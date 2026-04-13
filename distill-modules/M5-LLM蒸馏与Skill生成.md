# M5 - LLM蒸馏与Skill生成模块

## 模块定位

对应 harness 中的「语义化标注与 Prompt (LLM Input)」和「策略抽象化 (Skill Generation)」。这是将 M1-M4 的结构化数据通过 LLM 转化为**结构化策略规格（StrategySpec）+ 执行意图（ExecutionIntent）**的核心环节。  

开发前冻结口径：
- **所有读路径、回测路径、蒸馏路径统一走 AVE**，避免双数据路径。
- **onchainos CLI 只作为执行适配层**，负责登录钱包、签名、模拟、广播、安全扫描，不参与策略蒸馏的数据输入。
- **M5 产物不直接等于“立刻下单”**，而是先输出可编译的策略规格，再由编译器决定生成 `plan` action 和可选的 `execute` action。
- **默认保留当前 no-network 的 `primary.py`** 作为策略规划入口；真正的 live 执行必须通过单独 action 显式触发。

---

## 现有代码能力评估

### 已实现 ✓

| 能力 | 代码位置 | 评价 |
|------|---------|------|
| Extraction Prompt | `extractors.py → DEFAULT_EXTRACTION_PROMPT` | **不足**。过于笼统，缺乏四大核心模块引导 |
| Pi Reflection 集成 | `ot_reflection_mode.ts → runReflectionMode` | **好**。完整的 mock/live 双路径，artifact 持久化 |
| Output Schema 定义 | `reflection/service.py → build_wallet_style_output_schema` | **好**。但 schema 只覆盖了 profile + review，缺少触发信号和执行逻辑 |
| Fallback 机制 | `service.py → WalletStyleExtractor（fallback）` | **好**。Pi 失败时本地启发式兜底 |
| Skill 编译 | `compiler.py → SkillPackageCompiler` | **好**。完整生成 SKILL.md + manifest + actions + interface + primary.py |
| primary.py 生成 | `compiler.py → _write_type_specific_files` | **不足**。生成的代码逻辑过于简单（见下文分析）|
| 外部执行能力候选 | `../onchainos-skills` | **可用**。具备 swap / simulate / broadcast / wallet contract-call，适合作为执行适配层，但当前 0T 尚未接入 |

### 问题分析

**问题 1：Extraction Prompt 太笼统**

现有 prompt：
```
You are the wallet-style extractor for the 0T hackathon MVP.
Given compact wallet JSON, identify the address's trading style...
```

这个 prompt 没有告诉 LLM：
- 要输出哪些具体的入场条件
- 止损/止盈策略该如何表达
- 如何利用市场上下文数据做判断
- 什么样的规则是「可编译为 Skill」的

**问题 2：Output Schema 不包含关键字段**

```python
# 现有 schema (reflection/service.py:61-111)
# profile 只有：style_label, summary, confidence, tempo, risk, conviction, ...
# 缺少：entry_conditions, exit_conditions, position_sizing, stop_loss_model
```

**问题 3：生成的 primary.py 决策逻辑过于简单**

```python
# compiler.py 生成的核心逻辑：
if market_bias in {"bullish", "up"} and risk_appetite in {"aggressive", "balanced"}:
    action = "buy"
elif market_bias in {"bearish", "down"} and "sell" in dominant_actions:
    action = "sell"
```

这离 harness 描述的 `IF RSI < 30 AND WhaleInflow > $50k THEN BuySmallPosition` 差距很大。

---

## 产物定义收敛

为避免“自动交易策略”与“模拟计划生成”混淆，M5 产物在文档层明确拆成三层：

1. **Profile**
   - 钱包风格画像，供展示和解释使用
2. **StrategySpec**
   - 可回测、可解释、可编译的策略规则
   - 只引用 AVE 预处理出来的特征和因子
3. **ExecutionIntent**
   - 将 StrategySpec 映射到执行层的意图参数
   - 例如：分几腿下单、是否要求先过 `security tx-scan`、是否要求 `gateway simulate`

其中：
- **StrategySpec 是 M6 回测的唯一输入**，必须保持 AVE-only。
- **ExecutionIntent 不参与回测打分**，只决定后续如何调用 onchainos CLI。
- **最终可执行 skill** 应至少包含两个动作：
  - `plan` / `primary`：无网络，产出 recommendation + trade_plan
  - `execute`：有网络，显式调用 onchainos CLI 完成 dry-run 或 live 执行
- **执行接入优先走 CLI 适配**，不是直接复用 onchainos MCP；原因是当前 MCP 主要暴露行情/quote/simulate/broadcast，钱包登录、签名和一键执行链路仍以 CLI 合同最完整

---

## 改进方案

### 1. 增强版 Extraction Prompt

```python
ENHANCED_EXTRACTION_PROMPT = """You are a quantitative trading strategy distiller.

Given the compact wallet JSON containing:
- Wallet activity and holdings data
- Statistical trading metrics (win rate, holding period, profit factor)
- Market context at time of trades (momentum, volatility, macro)
- Signal and risk filter data

Your task is to produce a **reusable trading strategy specification** with these sections:

## 1. Strategy DNA
- Risk profile, capital management model, typical holding period

## 2. Entry Conditions (CRITICAL - be specific)
- List 2-5 concrete, testable conditions that trigger a BUY
- Use data from market_context and signal_context
- Example: "Buy when price_24h_pct < -15 AND volatility_regime != 'extreme'"

## 3. Exit Conditions  
- Stop-loss model: percentage-based or ATR-based, with specific thresholds
- Take-profit model: fixed target, trailing stop, or ladder
- Example: "Sell 50% at +30%, remaining with 15% trailing stop"

## 4. Position Sizing
- How much of portfolio per trade
- Whether to split into legs (DCA pattern)
- Max single position size

## 5. Anti-patterns (what to AVOID)
- Based on risk filters and losing trade patterns

Return strict JSON. Every condition must be a testable expression."""
```

### 2. 扩展 Output Schema

```python
def build_enhanced_output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["profile", "strategy", "execution_intent", "review"],
        "properties": {
            "profile": {
                # ... 现有字段保留 ...
            },
            "strategy": {
                "type": "object",
                "required": ["entry_conditions", "exit_conditions", "position_sizing"],
                "properties": {
                    "entry_conditions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "condition": {"type": "string"},
                                "data_source": {"type": "string"},
                                "weight": {"type": "number"},
                            }
                        }
                    },
                    "exit_conditions": {
                        "type": "object",
                        "properties": {
                            "stop_loss_model": {"type": "string"},
                            "stop_loss_pct": {"type": "number"},
                            "take_profit_model": {"type": "string"},
                            "take_profit_targets": {
                                "type": "array",
                                "items": {"type": "object"}
                            }
                        }
                    },
                    "position_sizing": {
                        "type": "object",
                        "properties": {
                            "model": {"type": "string"},
                            "max_position_pct": {"type": "number"},
                            "split_legs": {"type": "boolean"},
                            "leg_count": {"type": "integer"}
                        }
                    }
                }
            },
            "execution_intent": {
                "type": "object",
                "required": ["adapter", "mode", "preflight_checks"],
                "properties": {
                    "adapter": {
                        "type": "string",
                        "enum": ["onchainos_cli"]
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["plan_only", "dry_run_ready", "live_ready"]
                    },
                    "preferred_workflow": {
                        "type": "string",
                        "enum": [
                            "swap_execute",
                            "swap_swap_then_gateway_simulate_then_broadcast"
                        ]
                    },
                    "preflight_checks": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": [
                                "security_token_scan",
                                "security_tx_scan",
                                "gateway_simulate",
                                "mev_protection"
                            ]
                        }
                    },
                    "route_preferences": {
                        "type": "array",
                        "items": {"type": "string"}
                    },
                    "split_legs": {"type": "boolean"},
                    "leg_count": {"type": "integer"},
                    "max_position_pct": {"type": "number"}
                }
            },
            "review": {
                # ... 现有字段保留 ...
            }
        }
    }
```

### 3. 改进 primary.py 生成模板 + onchainos 执行入口

```python
# compiler.py 中新增的策略驱动模板片段

STRATEGY_RECOMMEND_TEMPLATE = '''
STRATEGY = json.loads({strategy_json!r})

def _check_entry_conditions(context: dict) -> tuple[bool, list[str]]:
    """检查入场条件是否满足"""
    conditions = STRATEGY.get("entry_conditions", [])
    met = []
    for cond in conditions:
        # 简单的条件评估器
        expr = cond.get("condition", "")
        if _evaluate_condition(expr, context):
            met.append(expr)
    threshold = max(1, len(conditions) // 2)  # 至少一半条件满足
    return len(met) >= threshold, met

def _check_exit_conditions(context: dict) -> dict:
    """检查是否触发止损/止盈"""
    exit_config = STRATEGY.get("exit_conditions", {})
    current_pnl = context.get("current_pnl_pct", 0)
    
    stop_loss_pct = exit_config.get("stop_loss_pct", -15)
    if current_pnl <= stop_loss_pct:
        return {"action": "sell", "reason": f"stop_loss at {stop_loss_pct}%"}
    
    targets = exit_config.get("take_profit_targets", [])
    for target in targets:
        if current_pnl >= target.get("pct", 100):
            return {"action": "sell", "reason": f"take_profit at {target['pct']}%",
                    "sell_pct": target.get("sell_pct", 100)}
    
    return {"action": "hold", "reason": "within range"}

def _recommend(context: dict) -> dict:
    entry_ok, met_conditions = _check_entry_conditions(context)
    exit_signal = _check_exit_conditions(context)
    
    if exit_signal["action"] == "sell":
        return {**exit_signal, "confidence": 0.85}
    
    if entry_ok:
        sizing = STRATEGY.get("position_sizing", {})
        return {
            "action": "buy",
            "confidence": min(0.9, len(met_conditions) * 0.2 + 0.3),
            "met_conditions": met_conditions,
            "max_position_pct": sizing.get("max_position_pct", 5),
            "split_legs": sizing.get("split_legs", False),
        }
    
    return {"action": "watch", "confidence": 0.3, "reason": "conditions not met"}
'''
```

同时新增单独的执行入口，避免把 `primary.py` 从 no-network 规划器直接变成链上执行器：

```python
EXECUTION_ADAPTER_TEMPLATE = '''
def execute_with_onchainos(context: dict, recommendation: dict, trade_plan: dict) -> dict:
    """
    仅在用户明确授权 live execution 时调用。

    执行顺序：
    1. 用 AVE-only 产出的 recommendation / trade_plan 组装执行参数
    2. 调 onchainos security / swap / gateway CLI
    3. 返回 txHash / orderId / simulation result
    """
    ...
'''
```

建议的编译产物合同：
- `primary.py`：默认 action，`allow_network: false`
- `execute.py`：显式执行 action，`allow_network: true`
- `execute.py` 不重新拉行情、不重新拉信号，只消费 M5/M6 已产出的 `trade_plan` 和 `execution_intent`

---

## Prompt 上下文装配

最终注入 LLM 的 `compact_input` 结构（M1-M4 汇总）：

```json
{
  "wallet": "0x...",
  "chain": "bsc",
  "wallet_summary": { "balance_usd": 7740, "total_win_ratio": 0.67 },
  "holdings": [ ... ],                     // M1, 5条
  "recent_activity": [ ... ],              // M1, 8条
  "derived_stats": {                       // M1 + M2 扩展
    "activity_count": 15,
    "buy_count": 10, "sell_count": 5,
    "win_rate": 0.67,                      // M2
    "profit_factor": 2.3,                  // M2
    "holding_classification": "day_trading", // M2
    "loss_tolerance_label": "moderate",     // M2
    "averaging_pattern": "linear_dca"       // M2
  },
  "market_context": {                       // M3
    "macro": { "btc_24h_pct": -2.3, "regime": "risk_off" },
    "focus_token_context": [ ... ]
  },
  "signal_context": {                       // M4
    "top_entry_factors": ["dip_buy:72%", "volume_spike:55%"],
    "hard_blocks": [],
    "warnings": ["holder_concentration>50%"]
  },
  "token_snapshots": [ ... ],              // M1, 4条
  "signals": [ ... ]                        // M1, 5条
}
```

**预计总大小：5-6 KB（约 1800-2200 tokens）**，在 Pi 的输入窗口内安全。

---

## 与现有代码的结合点

### 保留的好设计

1. **`PiReflectionService` 的 mock/live 双路径** — 完美支持开发测试
2. **`WalletStyleExtractor` 作为 fallback** — Pi 失败时的安全网
3. **`SkillPackageCompiler` 的包结构生成** — SKILL.md/manifest/actions/interface 的格式化已完善
4. **`_build_reflection_spec` 的 artifact 持久化** — 每步都有 JSON 可追溯

### 需要修改的

1. **`DEFAULT_EXTRACTION_PROMPT`** → 替换为 `ENHANCED_EXTRACTION_PROMPT`
2. **`build_wallet_style_output_schema`** → 扩展增加 `strategy + execution_intent` 部分
3. **`_write_type_specific_files` 中的 primary.py 模板** → 增加条件评估逻辑，保留 no-network
4. **`_write_type_specific_files` 中新增 `execute.py` 模板** → 调用 onchainos CLI 执行适配层
5. **`WalletStyleProfile` 数据模型** → 新增 `entry_conditions`、`exit_conditions`、`position_sizing` 字段
6. **新增 `WalletStyleExecutionIntent` 数据模型** → 描述 dry-run/live-ready 的执行意图
7. **`_preprocess_wallet_data`** → 接收 M2-M4 的输出并组装
8. **新增 `execution_adapters/onchainos_cli.py`** → 封装 swap / simulate / broadcast / security 调用

### 不应修改的

- `runReflectionMode` 的 TypeScript 实现 — 通用反射框架，不应耦合业务逻辑
- `RunIngestionPipeline` — candidate 生命周期管理保持不变
- `SkillPackageCompiler.promote` — 晋升流程保持不变
- **AVE Provider 的数据读取边界** — 保持 AVE-only，不让 onchainos 回流成为第二数据源

---

## 与其他模块的依赖关系

- **上游**: M1（基础数据）、M2（统计特征）、M3（市场上下文）、M4（信号与过滤器）
- **下游**: M6（生成的 Skill 需要回测验证）
- **并行性**: M5 是**串行瓶颈** — 必须等 M2、M3、M4 全部完成后才能执行

---

## 风险与注意事项

1. **LLM 输出不确定性**：即使 schema 约束了格式，LLM 可能生成不合理的入场条件或阈值。需在 `parse_wallet_style_review_report` 中增加合理性校验
2. **条件评估器的安全性**：`_evaluate_condition` 不能使用 `eval()`，需实现安全的表达式解析器
3. **向后兼容**：`WalletStyleProfile` 新增字段时使用 Optional 默认值，确保旧数据不会破坏反序列化
4. **Prompt 版本管理**：Prompt 变更会影响所有后续蒸馏结果，建议版本化并记录在 artifact 中
5. **执行权限必须显式授权**：`execute.py` 只能在用户明确授权后触发，且要保留 dry-run / live-run 分离
6. **onchainos 不进入蒸馏输入**：禁止把 OKX/OnchainOS 的 market/signal/PnL 数据重新注入 compact_input，否则会形成双路径和回测口径漂移
