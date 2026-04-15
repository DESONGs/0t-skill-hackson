# Reflection 阶段问题剖析与改进方案

> 背景：策略师反馈当前蒸馏出来的 skill 缺乏 insightful 的发现，没有抽提出真正的交易 pattern。
> 本文聚焦 reflection 阶段的两个核心问题，逐层剖析根因，并给出不改架构的 fix 方案。

---

## 一、问题全景

蒸馏链的 reflection 阶段存在**两个串联问题**，共同导致了"产出没有洞察力"的结果：

```
问题 1：Pi agent runtime 不稳定
    → LLM 输出失败/截断/不可解析
    → 触发 fallback（规则化兜底）
    → 输出变成确定性模板

问题 2：质量门禁过于严格 + 缺乏 pattern 引导
    → LLM 即使成功输出，也被拒绝
    → 同样触发 fallback
    → 输出还是确定性模板

两条路都通向同一个终点：WalletStyleExtractor.extract() 的规则化模板
→ 策略师看到的 skill 全是 "{risk_appetite}-{execution_tempo}" 拼接的泛化标签
→ 没有任何 insightful 的交易 pattern
```

---

## 二、问题 1 剖析：Pi Agent Runtime 不稳定

### 2.1 当前状态

`fix-pi-reflection-blocking.md` 文档中描述的 5 项修复已**全部实施到代码中**：

| # | 修复项 | 代码现状 | 结论 |
|---|--------|---------|------|
| 1 | `execution_intent` 移出 LLM 输出 | `build_wallet_style_output_schema()` 已不含 `execution_intent`；`parse_wallet_style_review_report()` 已通过参数接收 | ✅ 已实施 |
| 2 | TS 层容错 JSON 解析 + 保留原始文本 | `parseJsonObject()` 已引入 `parseStreamingJson` 容错；catch 块已保留 `raw_text` | ✅ 已实施 |
| 3 | retry 循环修复 | 异常捕获已合并 `(ReflectionQualityError, ValueError)`；retry 已使用 `_minimal_compact_payload` | ✅ 已实施 |
| 4 | 质量门禁从"拒绝"改为"修复" | wallet/chain 不匹配已改为 auto-fix + 记录 | ✅ 部分实施（见问题 2） |
| 5 | fallback 前原始文本抢救 | `_try_salvage_from_raw_text()` 已添加并在 retry 循环后调用 | ✅ 已实施 |

### 2.2 仍然存在的 runtime 风险

即使修复已实施，Pi runtime 仍有以下脆弱点：

**a) 模型可用性依赖环境变量链**

```typescript
// ot_reflection_mode.ts:256-283
async function resolveModel(job) {
    const configuredReference = [
        String(metadata["model"] || "").trim(),
        String(process.env.OT_PI_REFLECTION_MODEL || "").trim(),
        String(process.env.OT_PI_DEFAULT_MODEL || "").trim(),
    ].find((value) => value.length > 0);
    // ... 如果都没设 → 遍历所有 provider 找第一个有 API key 的
}
```

没有明确的 model fallback 策略，且依赖 `@mariozechner/pi-ai` 的 provider 发现机制。如果 API key 过期或限流，整个 reflection 直接失败。

**b) 超时设置偏紧**

```python
# service.py (Python 侧)
runtime_timeout_seconds = 180  # 默认 3 分钟
request_timeout_seconds = max(45, min(runtime_timeout_seconds - 15, 75))  # 45-75 秒
max_tokens = 3500
```

```typescript
// ot_reflection_mode.ts (TS 侧)
requestTimeoutSeconds = resolvePositiveNumber(...) ?? 90  // 默认 90 秒
maxTokens = resolvePositiveNumber(...) ?? 3000
```

Python 侧默认给 LLM 45-75 秒生成 3500 tokens 的 JSON，对于复杂钱包这可能不够。

**c) Pi runtime 是简化抽取版**

当前 vendor 下的 `pi_runtime` 是从 pi-mono 主仓库抽取的**最小子集**（只保留了 `ot_reflection_mode.ts`、`ot_runtime_entry.ts` 和必要的 util）。完整 pi-mono 仓库应该包含：
- 更健壮的 session 管理
- 更完善的 error recovery
- 可能的 streaming JSON 解析（边生成边解析，避免截断）
- 更丰富的 model routing 策略

**如果 pi-mono 副本有完整架构，建议对比以下模块是否缺失：**
- session lifecycle management（当前只有单次 request-response）
- model health check / circuit breaker
- streaming output collection（当前是等完整 response 再解析）
- structured output mode（如果底层模型支持 JSON mode / tool calling，可以跳过手动 JSON parse）

### 2.3 Fix 方案（Runtime 稳定性）

| 优先级 | 方案 | 改动范围 | 效果 |
|--------|------|---------|------|
| **P0** | 从 pi-mono 副本补全 session 管理和 error recovery | `vendor/pi_runtime/` | 减少因进程管理导致的失败 |
| **P0** | 检查是否支持 structured output / JSON mode（model 层面） | `ot_reflection_mode.ts` 中 `completeWithTimeout` 的 options | 从根源避免 JSON 截断/格式问题 |
| **P1** | 放宽超时：Python 侧 `request_timeout_seconds` 默认改为 120 秒 | `reflection/service.py` + 环境变量 | 减少因超时导致的截断 |
| **P1** | 增加 model fallback 链：主模型失败 → 备用模型 | `ot_reflection_mode.ts` 的 `resolveModel` | 减少 API 级别的不可用 |
| **P2** | 增加 Pi reflection 调用的 observability（成功率 / 失败原因 / 耗时统计） | `service.py` 的 `_resolve_reflection_report` | 建立数据基线，持续优化 |

---

## 三、问题 2 剖析：质量门禁过严 + 缺乏 Pattern 引导

这是**导致"没有 insightful 发现"的核心原因**。

### 3.1 当前质量门禁的完整判定标准

`reflection/service.py` → `parse_wallet_style_review_report()` 中有 **5 道校验关卡**：

#### 关卡 1：Profile 泛化标签检测（第 248 行）

```python
_GENERIC_STYLE_LABELS = {"balanced", "default", "generic", "neutral"}

if _lower_text(profile.style_label) in _GENERIC_STYLE_LABELS or _is_generic_summary(profile.summary):
    raise ReflectionQualityError("reflection output profile is too generic for wallet style generation")
```

**拒绝条件**：
- `style_label` 等于 `balanced` / `default` / `generic` / `neutral`（精确匹配，大小写不敏感）
- `summary` 包含 `"balanced risk profile with moderate conviction"` 或 `"standard entry and exit strategy"`（子串匹配）

**问题**：这里的 generic 检测只有 4 个词和 2 个短语，覆盖面太窄。LLM 可能生成 `"moderate"`, `"mixed"`, `"diversified"` 等同样泛化但不在黑名单中的标签 → 通过检测但仍然没有洞察力。反过来，如果 LLM 因为数据稀疏确实只能给出 `"balanced"`，则被直接打回 → 进入 fallback → 输出更差。

#### 关卡 2：行为证据完整性检测（第 250 行）

```python
if not profile.dominant_actions or not profile.preferred_tokens or not profile.execution_rules:
    raise ReflectionQualityError("reflection output profile is missing concrete trading behaviors")
```

**拒绝条件**：`dominant_actions`、`preferred_tokens`、`execution_rules` 三者任一为空。

**问题**：对于交易历史稀少的地址（例如只有 2-3 笔交易），LLM 可能无法给出有意义的 `execution_rules`，但这不意味着 profile 完全无价值。

#### 关卡 3：Strategy 泛化标签检测（第 252 行）

```python
_GENERIC_SETUP_LABELS = {"balanced", "default", "generic", "neutral"}

if _lower_text(strategy.setup_label) in _GENERIC_SETUP_LABELS or _is_generic_summary(strategy.summary):
    raise ReflectionQualityError("reflection output strategy is too generic for wallet style generation")
```

**问题**：与关卡 1 相同的黑名单策略，覆盖面窄。

#### 关卡 4：Entry Conditions 泛化检测（第 254-259 行）

```python
_GENERIC_ENTRY_CONDITIONS = {"price above support"}

if any(
    _lower_text(condition.condition) in _GENERIC_ENTRY_CONDITIONS
    or _lower_text(condition.data_source) in {"", "onchain"}
    for condition in strategy.entry_conditions
):
    raise ReflectionQualityError("reflection output strategy.entry_conditions are too generic")
```

**拒绝条件**：
- 任一 entry condition 的 `condition` 字段等于 `"price above support"`
- 任一 entry condition 的 `data_source` 字段为空或等于 `"onchain"`

**问题**：`data_source` 为空 → 拒绝整个输出。LLM 经常在个别 condition 上遗漏 `data_source` 字段，但其他 condition 可能是有价值的。一个空字段不应该杀掉整个结果。

#### 关卡 5（下游）：Memory 和 Quality 门禁

在 `service.py` 中还有额外的过滤逻辑：

```python
# _select_derived_memories (第 1853-1878 行) — 过滤历史记忆
if style_label in {"balanced", "default", "generic", "neutral"}: continue
if bool(payload.get("fallback_used")): continue
if str(payload.get("strategy_quality")).lower() in {"", "low", "insufficient_data"}: continue

# _remember_distilled_memory (第 1880-1918 行) — 是否记忆本次结果
if _is_generic_memory_summary(summary): return  # 不记忆
if bool(reflection_payload.get("fallback_used")): return  # 不记忆
if strategy_quality in {"", "low", "insufficient_data"}: return  # 不记忆
```

**问题**：`strategy_quality` 依赖 backtest 结果。backtest 对数据完整性要求高（需要 `market_context`、`entry_factors`），而很多地址的市场上下文不完整 → `strategy_quality = "insufficient_data"` → 结果不被记忆 → 下次蒸馏无法利用历史信息。

### 3.2 与"缺乏 Pattern 发现"的因果链

```
                                ┌───────────────────────────┐
                                │  DEFAULT_EXTRACTION_PROMPT │
                                │  只说"identify style"      │
                                │  没有 pattern taxonomy     │
                                └──────────┬────────────────┘
                                           ▼
                              ┌─────────────────────────┐
                              │  LLM 缺乏引导           │
                              │  不知道你要"剥头皮"还是   │
                              │  "浮盈加仓"级别的判断     │
                              └──────────┬──────────────┘
                                         ▼
                     ┌───────────────────────────────────────┐
                     │  LLM 输出泛化标签（如 "balanced"）     │
                     │  或缺少 data_source                   │
                     └──────────┬──────────┬─────────────────┘
                                │          │
                    ┌───────────▼───┐  ┌───▼───────────────┐
                    │ 关卡 1 拒绝    │  │ 关卡 4 拒绝        │
                    │ "too generic" │  │ "too generic"     │
                    └───────┬───────┘  └───────┬───────────┘
                            │                  │
                            ▼                  ▼
                  ┌────────────────────────────────────┐
                  │  全部进入 WalletStyleExtractor      │
                  │  fallback（规则化模板）              │
                  │  style_label = "aggressive-active-  │
                  │  swing"（变量拼接，无洞察力）        │
                  └────────────────────────────────────┘
                            │
                            ▼
                  ┌────────────────────────────────────┐
                  │  下游 strategy_quality 检测         │
                  │  → "low" 或 "insufficient_data"     │
                  │  → 结果不被记忆、不被复用            │
                  └────────────────────────────────────┘
```

**核心矛盾**：系统有能力拒绝"坏的输出"，但没有能力引导 LLM 生成"好的输出"。

### 3.3 Prompt 与 Schema 的具体缺陷

**DEFAULT_EXTRACTION_PROMPT（extractors.py 第 11-17 行）**：

```
"You are the wallet-style extractor for the 0T hackathon MVP.
Given compact wallet JSON, identify the address's trading style, including tempo,
risk appetite, conviction profile, token preference, sizing pattern, and execution
guardrails. Prefer concise reusable rules that can be compiled into a local skill.
If the evidence is sparse, still produce a best-effort profile and label confidence."
```

缺失：
- **没有给出 archetype taxonomy**（什么是剥头皮、什么是 meme hunter、什么是浮盈加仓）
- **没有告诉 LLM 什么标签会被拒绝**（不要写 balanced / default / generic / neutral）
- **没有引导 LLM 关注交易级别的证据**（首笔买入市值、买入金额对比、持仓时间分布）
- **没有要求 LLM 引用具体交易作为证据**

**build_wallet_style_output_schema（reflection/service.py 第 88-166 行）**：

schema 中 `profile` 只有：
- `style_label`（一个字符串，没有 enum 约束或示例）
- `execution_tempo`（一个字符串）
- `risk_appetite`（一个字符串）
- 通用列表字段：`dominant_actions`, `preferred_tokens`, `execution_rules`

缺失：
- **`trading_archetype`**：明确的交易风格原型（scalper / meme_hunter / swing_trader / diamond_hands ...）
- **`behavioral_patterns`**：被检测到的行为模式（浮盈加仓 / 以小搏大 / 买早买多 / 滚雪球 ...）
- **`token_preference`**：代币偏好类型（meme / small_cap / blue_chip / mixed）
- **`pattern_evidence`**：支撑 pattern 判定的具体交易证据

### 3.4 compact_input（`_preprocess_wallet_data` 输出）的数据缺陷

LLM 的输入中已经包含 `derived_stats`，但缺少让 LLM 做 pattern 判定所需的关键数据：

| 已有字段 | 已足够？ | 缺什么 |
|----------|---------|--------|
| `holding_classification: "day_trading"` | 有标签但太粗 | 缺少持仓时长的分布（p25/p50/p75），无法区分"稳定日内"和"偶尔持仓" |
| `averaging_pattern: "martingale"` | 只看金额递增/递减 | **不知道加仓时是否已浮盈**（浮盈加仓 vs 补仓摊薄是两种完全不同的行为） |
| `win_rate / profit_factor` | 有 | 但**缺少最大单笔回报倍数**（识别"以小搏大"需要 max return multiplier） |
| `avg_activity_usd` | 有均值 | **缺少首笔买入金额 vs 均值对比**（识别"买大"需要 per-token first buy amount） |
| 无 | — | **完全没有 token 市值/市值级别数据**（无法判断 meme / 小市值偏好） |
| `open_position_count` | 有数量 | **缺少 open_position_ratio**（open / total），这是"喜欢 hold"的核心指标 |
| 无 | — | **没有 trades_per_day**（只有 activity_count，无法算交易密度 → 无法判高频） |

---

## 四、Fix 方案

### 方案总览

```
层次 1：扩充特征计算（trade_pairing.py）
  → 让系统"算出"更多 pattern 相关数据
  → 不动配对算法核心，只扩展统计输出

层次 2：新增 Archetype 分类器（新文件 archetype.py）
  → 基于扩展后的统计数据，规则化地判定 archetype 和 behavioral pattern
  → 作为 LLM 的"预分类"输入，也作为 fallback 的改良版

层次 3：改造 Prompt + Schema
  → 给 LLM 明确的 pattern taxonomy 和证据引用要求
  → 扩展 output schema 承接 archetype + pattern 字段

层次 4：放宽质量门禁
  → 从"拒绝泛化"改为"分级接受"
  → 减少不必要的 fallback

层次 5：注入新特征到 compact_input
  → 让 LLM 看到 pattern 判定所需的数据
```

### Fix 4.1：放宽质量门禁（直接减少 fallback 率，最高优先级）

**目标**：让更多 LLM 输出能通过校验，而不是一刀切拒绝。

#### 4.1.1 关卡 1 改造：style_label 从黑名单改为灰名单

**文件**：`reflection/service.py`

**当前逻辑**（第 248-249 行）：
```python
if _lower_text(profile.style_label) in _GENERIC_STYLE_LABELS:
    raise ReflectionQualityError(...)  # 直接拒绝
```

**改为**：
- 泛化标签不再直接拒绝，而是降低 confidence 并标记 `_auto_degraded`
- 只有当 **style_label 泛化 AND summary 泛化 AND dominant_actions 为空** 三者同时成立时才拒绝
- 理由：LLM 可能在 `style_label` 上偶尔写 `"balanced"`，但 `summary` 和 `execution_rules` 里给了有价值的分析

建议新逻辑伪代码：
```
if style_label 在黑名单:
    if summary 也泛化 AND (dominant_actions 为空 OR preferred_tokens 为空):
        raise ReflectionQualityError  # 真正无价值
    else:
        profile.confidence *= 0.7   # 降分但保留
        记录 auto_fix
```

#### 4.1.2 关卡 4 改造：entry_conditions 从"任一失败即全拒"改为"过滤坏条目"

**文件**：`reflection/service.py`

**当前逻辑**（第 254-259 行）：
```python
if any(
    _lower_text(condition.condition) in _GENERIC_ENTRY_CONDITIONS
    or _lower_text(condition.data_source) in {"", "onchain"}
    for condition in strategy.entry_conditions
):
    raise ReflectionQualityError(...)  # 只要有一个坏的就全部拒绝
```

**改为**：
- 过滤掉泛化的 condition，保留有价值的
- 只有过滤后**剩余 0 条**时才拒绝

建议新逻辑伪代码：
```
valid_conditions = [c for c in entry_conditions
                    if c.condition 不在黑名单
                    AND c.data_source 不在 {"", "onchain"}]
if len(valid_conditions) == 0:
    raise ReflectionQualityError
else:
    strategy.entry_conditions = valid_conditions  # 只保留好的
    记录被过滤掉的条目到 auto_fixes
```

#### 4.1.3 关卡 2 改造：行为证据允许部分缺失

**当前逻辑**（第 250-251 行）：
```python
if not profile.dominant_actions or not profile.preferred_tokens or not profile.execution_rules:
    raise ReflectionQualityError(...)
```

**改为**：
- `dominant_actions` 和 `preferred_tokens` 仍然必须，这是最低限度的行为证据
- `execution_rules` 改为可选：如果缺失，从 `preprocessed` 的 `derived_stats` 自动补全一条规则

### Fix 4.2：改造 Prompt（引导 LLM 生成有洞察力的输出）

**文件**：`extractors.py` → `DEFAULT_EXTRACTION_PROMPT`

**在当前 prompt 基础上追加 pattern taxonomy 引导段**（不替换原有文本，追加即可）：

```
PATTERN_TAXONOMY_APPENDIX = """

## Required: Trading Archetype Classification

You MUST classify this wallet into one primary archetype:
- scalper: holds <30 min, >5 trades/day, small per-trade PnL (±1-5%)
- meme_hunter: trades newly launched / small liquidity tokens, seeks 5x+ returns
- swing_trader: holds 1-7 days, entries on momentum shifts or dips
- diamond_hands: high open_position_ratio (>40%), avg profitable hold >7 days
- degen_sniper: first buyer on low-liquidity tokens with outsized position
- high_frequency_rotator: >10 trades/day, cycles through tokens rapidly
- dca_builder: regular buy intervals, linear or pyramid sizing

## Required: Behavioral Pattern Detection

Identify which patterns are present (can be multiple):
- profit_add (浮盈加仓): adds to position while already in unrealized profit
- asymmetric_bet (以小搏大): bet size <30% of average, but target return >3x
- buy_early_buy_big (买早买多): enters token at low mcap with above-avg size
- snowball (滚雪球): reinvests realized profits into next position
- prefer_hold (喜欢Hold): holds profitable positions significantly longer than losing ones

For each pattern, cite specific trades from the compact input as evidence.

## CRITICAL: Forbidden Labels

NEVER use these as style_label: balanced, default, generic, neutral, moderate, mixed.
Instead use the archetype classifications above.
"""
```

### Fix 4.3：扩展 Output Schema

**文件**：`reflection/service.py` → `build_wallet_style_output_schema()`

在 `profile.properties` 中新增：

```python
"trading_archetype": {
    "type": "string",
    "description": "Primary trading archetype: scalper, meme_hunter, swing_trader, diamond_hands, degen_sniper, high_frequency_rotator, dca_builder"
},
"behavioral_patterns": {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "pattern_name": {"type": "string"},
            "strength": {"type": "number", "minimum": 0, "maximum": 1},
            "evidence_summary": {"type": "string"}
        }
    }
},
"token_preference": {
    "type": "string",
    "description": "meme, small_cap, blue_chip, defi, mixed"
},
```

在 `profile.required` 中追加 `"trading_archetype"`。

对应地，在质量门禁中增加一条：
```python
if _lower_text(profile.trading_archetype) in _GENERIC_STYLE_LABELS:
    raise ReflectionQualityError("trading_archetype must not be generic")
```

### Fix 4.4：扩展 TradeStatistics（提供 Pattern 判定所需数据）

**文件**：`trade_pairing.py` → `compute_trade_statistics()`

在 `TradeStatistics` 中新增以下字段（不改配对逻辑）：

| 新字段 | 计算方式 | 用途 |
|--------|---------|------|
| `pnl_multiplier_max` | `max(t.sell_amount_usd / t.buy_amount_usd)` for completed trades | 识别"以小搏大" |
| `profitable_avg_holding_seconds` | 盈利交易的平均持仓时长 | 识别"喜欢 hold"（盈利仓持有更久） |
| `losing_avg_holding_seconds` | 亏损交易的平均持仓时长 | 对比盈亏持仓偏好差异 |
| `open_position_ratio` | `open_count / (open_count + completed_count)` | 识别"喜欢 hold" |
| `trades_per_day` | `total_trades / 交易跨度天数` | 识别"高频" |
| `first_buy_relative_size` | 每个 token 首笔 buy 金额 / 该地址 avg buy 金额 | 识别"买多" |

### Fix 4.5：新增 Archetype 分类器（改良版 Fallback）

**文件**：新建 `style_distillation/archetype.py`

这不是一个额外模块，而是**改良版的 `WalletStyleExtractor`**。当 LLM reflection 失败需要 fallback 时，这个分类器能产出比当前模板更有洞察力的结果：

```
输入：TradeStatistics（含扩展字段）+ preprocessed derived_stats
输出：TradingArchetype + BehavioralPatterns

规则示例：
if trades_per_day > 5 AND median_holding < 1800:  → scalper
if small_cap_trade_ratio > 0.6 AND pnl_multiplier_max > 5:  → meme_hunter
if open_position_ratio > 0.4 AND profitable_avg_holding > 604800:  → diamond_hands
```

**与现有 `WalletStyleExtractor` 的关系**：
- `archetype.py` 输出的 `primary_label` 替代当前的 `"{risk}-{tempo}"` 拼接
- `archetype.py` 输出的 `behavioral_patterns` 填充到 `WalletStyleProfile.metadata`
- `WalletStyleExtractor` 调用 `archetype.py` 获取分类结果，而非自己做硬编码阈值判断

### Fix 4.6：在 `_preprocess_wallet_data` 中注入新特征

**文件**：`service.py` → `_preprocess_wallet_data()`

在 `derived_stats` 字典中追加：

```python
"trading_archetype": archetype_result.primary_label,
"behavioral_patterns": [p.to_dict() for p in archetype_result.patterns],
"token_preference": archetype_result.token_preference,
"pnl_multiplier_max": trade_stats.get("pnl_multiplier_max", 0),
"open_position_ratio": trade_stats.get("open_position_ratio", 0),
"trades_per_day": trade_stats.get("trades_per_day", 0),
"profitable_avg_holding_seconds": trade_stats.get("profitable_avg_holding_seconds", 0),
```

这样 LLM 在 compact_input 中能看到预分类结果，可以验证或修正。

---

## 五、实施优先级建议

```
Phase 1（立即见效，减少 fallback 率）：
  ├─ Fix 4.1：放宽质量门禁          ← 最快见效，改 1 个文件
  └─ Fix 4.2：改造 Prompt           ← 改 1 个字符串常量

Phase 2（让规则化输出也有洞察力）：
  ├─ Fix 4.4：扩展 TradeStatistics  ← 改 1 个文件，新增字段
  └─ Fix 4.5：新增 archetype.py     ← 新文件，不改现有代码

Phase 3（让 LLM 输出有结构化 pattern）：
  ├─ Fix 4.3：扩展 Output Schema    ← 改 1 个文件
  └─ Fix 4.6：注入新特征到 input    ← 改 1 个函数

Phase 0（如果 runtime 不稳定是主要瓶颈）：
  └─ Fix 2.3：Pi runtime 稳定性    ← 需要对比 pi-mono 完整代码
```

---

## 六、改动文件清单

| 文件 | Phase | 改什么 | 是否新文件 |
|------|-------|-------|-----------|
| `reflection/service.py` | 1+3 | 放宽质量门禁 + 扩展 schema | 否 |
| `extractors.py` | 1 | 追加 prompt taxonomy | 否 |
| `trade_pairing.py` | 2 | 扩展 TradeStatistics 字段 | 否 |
| `style_distillation/archetype.py` | 2 | 新增 archetype 分类器 | **是** |
| `service.py` | 3 | `_preprocess_wallet_data` 注入新特征 | 否 |
| `models.py` | 3 | `WalletStyleProfile` 新增 Optional 字段 | 否 |

不需要改动的：
- `pair_trades()` 的 FIFO 配对核心算法
- `PiReflectionService` 的运行时编排
- `SkillPackageCompiler` 的包结构
- `WalletStyleDistillationService` 的阶段流转
- `ot_reflection_mode.ts`（除非需要做 runtime 稳定性修复）
- 所有 vendor 层代码

---

## 七、风险与注意事项

1. **Schema 变更的向后兼容**：所有新增字段必须 Optional 有默认值，确保旧 artifact 不会破坏反序列化
2. **Prompt 长度控制**：taxonomy appendix 会增加约 400 tokens 的 system prompt，需确认不超过 Pi 的上下文窗口
3. **质量门禁放宽的副作用**：放宽后可能接受一些质量中等的输出，建议在 metadata 中标记 `quality_tier: "auto_degraded"` 以区分
4. **archetype 分类器的阈值**：初始阈值基于经验设定，需要用实际蒸馏结果迭代校准
5. **不要让 archetype.py 变成新的"万能模块"**：它只做分类判定，不做数据获取、不做 LLM 调用、不做 skill 编译
