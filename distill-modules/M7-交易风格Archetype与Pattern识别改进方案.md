# M7 - 交易风格 Archetype 与 Pattern 识别改进方案

## 背景与动机

策略师反馈：当前蒸馏产出的 skill **缺乏 insightful 的发现，没有抽提出 pattern**。有用的 skill 应该让人快速明白"这个人的风格是什么"——剥头皮、meme、小市值、高频、波段、浮盈加仓、滚雪球、以小搏大、买早买多、喜欢 hold。这些特征 AI 应该能从链上数据中抽提出来。

---

## 一、问题诊断：三个平面的缺口

### 平面一：特征计算层（`trade_pairing.py` + `signal_filters.py`）

**现状**：已实现 FIFO 配对、持仓时长、加仓模式检测（martingale/pyramid/linear_dca）、4 种入场因子（dip_buy / momentum_chase / volume_spike / volatility_play）、基础胜率和盈亏统计。

**缺口**：

| 策略师期望的 pattern | 当前代码状态 | 缺什么 |
|---|---|---|
| **剥头皮** | `holding_classification` 有 `scalping`（<1h），仅作统计字段传递 | 需结合**交易频率 + 持仓时长 + 盈亏幅度**综合判定 |
| **Meme / 小市值** | 完全缺失 | `TokenMarketContext` 有 `liquidity_usd` 但无 `market_cap`；无 token 类型分类 |
| **高频** | `execution_tempo` 仅用 `activity_count ≥ 6` 判定，阈值低且不考虑时间跨度 | 需要**单位时间交易密度**（trades/day） |
| **波段** | `holding_classification` 有 `swing`，但只是被动标签 | 需结合**持仓跨度分布**和**趋势跟踪特征** |
| **浮盈加仓** | `_detect_averaging_pattern` 只看金额递增/递减，不看加仓时是否已浮盈 | 需在配对阶段追踪「加仓时已有仓位的未实现盈亏」 |
| **滚雪球** | 完全缺失 | 需检测前一笔盈利交易的利润是否转入下一笔买入本金 |
| **以小搏大** | 完全缺失 | 需分析买入金额 vs 余额/均值比值 + 对应 PnL 倍数 |
| **买早买多** | 完全缺失 | 需分析 token 首笔 buy 时的市值、买入金额与其他交易的差异 |
| **喜欢 hold** | `loss_tolerance_label` 有 `diamond_hands`，但仅衡量亏损容忍度 | 需衡量盈利持仓的持有意愿、未平仓比例 |

### 平面二：Profile 合成层（`extractors.py`）

**现状**：`WalletStyleExtractor.extract()` 使用硬编码阈值做单变量判定。

```python
# extractors.py:140-148 — 仅靠 activity_count 一个变量决定 execution_tempo
if activity_count >= 6:
    execution_tempo = "high-frequency rotation"
elif activity_count >= 3:
    execution_tempo = "active swing"
```

`style_label` 只是 `"{risk_appetite}-{execution_tempo}"` 的拼接（如 `"aggressive-high-frequency-rotation"`），不是一个有辨识度的交易风格标签。

**核心问题**：没有多维度交叉分类。真正有区分度的 pattern 需要同时考虑频率、持仓、盈亏、市值偏好、仓位管理等多个维度。

### 平面三：LLM Reflection 层（Prompt + Schema）

**现状**：

```python
# extractors.py:11-17 — Prompt 过于笼统
DEFAULT_EXTRACTION_PROMPT = (
    "You are the wallet-style extractor for the 0T hackathon MVP.\n"
    "Given compact wallet JSON, identify the address's trading style..."
)
```

- Prompt 没有给 LLM 一个**明确的 pattern taxonomy**（分类体系）
- LLM 不知道应该输出"剥头皮"还是"浮盈加仓"这种级别的判断
- `build_wallet_style_output_schema()` 的 `profile` 字段缺少 `trading_archetype`、`behavioral_patterns` 等承接 pattern 的字段

---

## 二、改进方案

### 改进 1：在 `trade_pairing.py` 层扩展交易级特征

> 不改 `pair_trades` 的 FIFO 配对核心逻辑，仅在 `CompletedTrade.metadata` 和 `TradeStatistics` 中扩展字段。

#### 1.1 每笔交易级别（写入 `CompletedTrade.metadata`）

| 新增字段 | 说明 | 用途 |
|---|---|---|
| `buy_mcap_usd` | 买入时 token 的估算市值（可用 `liquidity_usd × 常数` 粗估，或从 `inspect_token.market_snapshot` 取） | 检测小市值偏好、买早 |
| `buy_amount_vs_avg_ratio` | 该笔买入金额 / 该地址所有买入的均值 | 检测"买多" |
| `is_first_buy_for_token` | 该地址对该 token 的第一笔买入 | 检测"买早" |
| `was_in_profit_when_added` | 加仓时已有同 token 仓位是否浮盈 | 检测"浮盈加仓" |

**实现要点**：

- `buy_mcap_usd`：在 `run_distill_features` 中将 `focus_market_contexts` 传入配对函数，按 `token_address` 匹配到对应的 `liquidity_usd`，粗估市值 = `liquidity_usd × 2`（常规 AMM 近似）
- `was_in_profit_when_added`：在 `pair_trades` 的 buy 入队逻辑中，检查该 token 的 `buy_queues[key]` 是否已有未匹配的 buy lot，若有，比较当前价格（需入参当笔交易的 `price_usd`）与已有 lot 的买入价格
- `is_first_buy_for_token`：在 buy 入队时检查 `buy_queues[key]` 是否为空
- `buy_amount_vs_avg_ratio`：在 `compute_trade_statistics` 阶段回填

#### 1.2 统计级别（扩展 `TradeStatistics`）

| 新增字段 | 说明 | 用途 |
|---|---|---|
| `pnl_multiplier_max` | 最大单笔回报倍数（`sell_amount / buy_amount`） | 检测"以小搏大" |
| `pnl_multiplier_median` | 中位回报倍数 | 区分运气型 vs 风格型 |
| `profitable_avg_holding_seconds` | 盈利交易的平均持仓时长 | 检测"喜欢 hold" |
| `losing_avg_holding_seconds` | 亏损交易的平均持仓时长 | 判断止损纪律 |
| `open_position_ratio` | 未平仓占比（`open_count / (open_count + completed_count)`） | 高值 = 喜欢 hold |
| `profit_reinvestment_rate` | 前一笔盈利交易的利润占下一笔买入金额的比例 | 检测"滚雪球" |
| `first_buy_avg_mcap_usd` | 首次买入时 token 的平均市值 | 检测"买早"偏好 |
| `small_cap_trade_ratio` | 交易中 mcap < 阈值（如 $1M）的占比 | 检测"小市值"偏好 |
| `trades_per_day` | 交易密度（总交易数 / 活跃天数） | 检测"高频" |
| `profit_add_ratio` | 在浮盈状态下加仓的次数 / 总加仓次数 | 检测"浮盈加仓" |

**实现要点**：

- `profit_reinvestment_rate`：遍历按时间排序的 `completed_trades`，如果 trade[i] 盈利且 trade[i+1] 发生在之后，计算 `trade[i].pnl_usd / trade[i+1].buy_amount_usd`
- `trades_per_day`：取第一笔和最后一笔交易的时间跨度，除以总交易数
- `small_cap_trade_ratio`：需要 `buy_mcap_usd` 字段，统计 mcap < 阈值的交易占比

---

### 改进 2：新增 Archetype 分类器

> 建议新建 `style_distillation/archetype.py`，纯新增文件，不改现有代码结构。

#### 2.1 数据模型

```python
@dataclass
class BehavioralPattern:
    pattern_name: str         # "浮盈加仓" / "以小搏大" / "买早买多" / "滚雪球" / "喜欢hold"
    strength: float           # 0-1，该 pattern 的明显程度
    evidence_summary: str     # 人可读的证据摘要，如"在 PEPE 上 3 次浮盈加仓，平均浮盈 +45% 时追加"
    supporting_trades: list[str]  # 支撑该判定的交易 tx_hash 列表

@dataclass
class TradingArchetype:
    primary_label: str            # 主标签，如 "meme_hunter"
    secondary_labels: list[str]   # 次要标签，如 ["scalper", "degen_sniper"]
    behavioral_patterns: list[BehavioralPattern]
    confidence: float
    evidence: dict[str, Any]      # 判定依据的原始数据
```

#### 2.2 Archetype 分类规则

多维度交叉判定，不再是单阈值：

| Archetype | 判定条件 | 中文标签 |
|---|---|---|
| `scalper` | `median_holding < 1800s` AND `trades_per_day > 5` AND `abs(avg_pnl_pct) < 5` | 剥头皮 |
| `meme_hunter` | `small_cap_trade_ratio > 0.6` AND `pnl_multiplier_max > 5` | Meme 猎手 |
| `swing_trader` | `median_holding` 在 1-7 天 AND entry_factor 含 `momentum` 或 `dip_buy` | 波段手 |
| `diamond_hands` | `open_position_ratio > 0.5` OR `profitable_avg_holding > 7天` | 钻石手 / 喜欢 Hold |
| `degen_sniper` | `first_buy_avg_mcap < $100K` AND `buy_amount_vs_avg > 2x` | 买早买多型 |
| `compounding_builder` | `profit_reinvestment_rate > 0.3` | 滚雪球型 |
| `high_freq_rotator` | `trades_per_day > 10` AND `holding_classification` 为 `scalping` 或 `day_trading` | 高频轮动 |
| `asymmetric_bettor` | 小仓位（< avg × 0.3）交易中 pnl_multiplier > 3x 的占比 > 20% | 以小搏大型 |

**注意**：一个地址可能同时匹配多个 archetype。`primary_label` 取置信度最高的，其余放入 `secondary_labels`。

#### 2.3 Behavioral Pattern 检测规则

| Pattern | 中文 | 判定条件 | 证据要求 |
|---|---|---|---|
| `profit_add` | 浮盈加仓 | 同 token 多次 buy，且后续 buy 时已有仓位浮盈 | 列出具体 token + 加仓时的浮盈百分比 |
| `asymmetric_bet` | 以小搏大 | `buy_amount < avg_buy × 0.3` 且 `pnl_multiplier > 3x` 的交易占比 > 20% | 列出最具代表性的 3 笔交易 |
| `early_big_buy` | 买早买多 | `is_first_buy_for_token` 时 mcap < $500K 且 `buy_amount > avg_buy × 1.5` | 列出 token 名、买入时 mcap、买入金额 |
| `snowball` | 滚雪球 | 相邻盈利交易之间，后一笔 buy_amount ≈ 前一笔 pnl + 前一笔 buy_amount（容差 30%） | 列出连续的交易对 |
| `hold_preference` | 喜欢 Hold | `open_position_ratio > 0.4` 且 `profitable_avg_holding > median_holding × 2` | 列出仍持有的仓位及持有时长 |
| `strict_stop` | 严格止损 | `loss_tolerance_label == "tight_stop"` 且亏损交易持仓时长集中在一个窄区间 | 列出止损交易及止损幅度 |

#### 2.4 调用位置

在 `service.py` 的 `run_distill_features` 中，`compute_trade_statistics` 之后调用：

```python
# 现有代码
trade_statistics = compute_trade_statistics(...)

# 新增
archetype = classify_archetype(
    trade_statistics=trade_statistics,
    completed_trades=completed_trades,
    open_positions=open_positions,
    buy_splits=buy_splits,
    market_contexts=focus_market_contexts,
)
```

分类结果写入 `stage_payload` 和 `preprocessed.derived_stats`。

---

### 改进 3：改造 `extractors.py` 的 Profile 合成

#### 3.1 `style_label` 替换

**现有**：`style_label = f"{risk_appetite}-{execution_tempo.replace(' ', '-')}"`

**改为**：直接使用 archetype 分类器的 `primary_label`：

```python
style_label = archetype.primary_label  # 如 "meme_hunter"、"swing_trader"
```

当 archetype 分类器不可用时（数据极度稀疏），fallback 到现有逻辑。

#### 3.2 `summary` 改造

**现有**：变量拼接式摘要。

```
"0xABC on bsc trades with high-frequency rotation, leans aggressive, shows single-name conviction..."
```

**改为**：基于 archetype + pattern 的人可读描述：

```
"Meme 猎手风格：偏好小市值 token（72% 交易市值 < $1M），善于买早买多（首笔买入时平均市值 $180K，
金额高于均值 1.8 倍），有明显的浮盈加仓行为（在 PEPE、WOJAK 上均有 3 次以上浮盈状态追加）。
最大单笔回报 12.5 倍，胜率 67%。"
```

#### 3.3 `WalletStyleProfile` 新增字段

在 `models.py` 中扩展（使用默认值保证向后兼容）：

```python
@dataclass
class WalletStyleProfile:
    # ...现有字段保留...
    
    # 新增
    trading_archetype: str = ""                    # "meme_hunter" / "scalper" / ...
    secondary_archetypes: tuple[str, ...] = ()     # 次要标签
    behavioral_patterns: tuple[str, ...] = ()      # ("浮盈加仓", "买早买多", ...)
    token_preference: str = ""                     # "small_cap" / "blue_chip" / "meme" / "mixed"
    trades_per_day: float = 0.0
    pnl_multiplier_max: float = 0.0
    open_position_ratio: float = 0.0
```

---

### 改进 4：改造 Prompt 和 Reflection Schema

#### 4.1 `DEFAULT_EXTRACTION_PROMPT` 增强

在现有 prompt 基础上追加 pattern taxonomy：

```
...existing prompt content...

When analyzing this wallet, classify the trader into one or more of these archetypes:
- Scalper (剥头皮): ultra-short holds (<30min), many trades/day, small per-trade PnL
- Meme Hunter (Meme 猎手): focuses on newly launched / small cap tokens, seeks 5-50x returns
- Swing Trader (波段手): holds 1-7 days, enters on momentum or dips, uses laddered exits
- Diamond Hands (钻石手): holds positions for weeks+, high open position ratio
- Degen Sniper (买早买多型): buys early at low mcap with large size relative to typical trades
- Compounding Builder (滚雪球型): reinvests profits into next positions
- High Freq Rotator (高频轮动): 10+ trades/day with rapid rotation
- Asymmetric Bettor (以小搏大型): small bets seeking 3x+ asymmetric returns

Also identify behavioral patterns present:
- 浮盈加仓 (Profit Add): Adds to winners while already in profit
- 以小搏大 (Asymmetric Bet): Small bets seeking outsized returns (>3x)
- 买早买多 (Early Big Buy): Enters tokens early (low mcap) with outsized position
- 滚雪球 (Snowball): Compounds realized profits into next trade
- 喜欢Hold (Hold Preference): Prefers to hold rather than take quick profits

For each pattern found, cite specific trades as evidence.
Assign a primary archetype and list any secondary ones.
```

#### 4.2 `build_wallet_style_output_schema()` 扩展

在 `profile` 的 `properties` 中新增：

```python
"trading_archetype": {
    "type": "string",
    "description": "Primary trading style archetype"
},
"secondary_archetypes": {
    "type": "array",
    "items": {"type": "string"}
},
"behavioral_patterns": {
    "type": "array",
    "items": {
        "type": "object",
        "required": ["pattern_name", "strength", "evidence_summary"],
        "properties": {
            "pattern_name": {"type": "string"},
            "strength": {"type": "number"},
            "evidence_summary": {"type": "string"},
        }
    }
},
"token_preference": {
    "type": "string",
    "enum": ["small_cap", "meme", "blue_chip", "defi", "mixed"]
},
```

将 `"trading_archetype"` 加入 `profile` 的 `required` 列表。

---

### 改进 5：在 `_preprocess_wallet_data` 中注入新特征

在 `derived_stats` 字典中添加 archetype 分类器的输出，使其进入 `compact_input`（LLM 输入）：

```python
"derived_stats": {
    # ...现有字段全部保留...
    
    # 新增 archetype 层
    "trading_archetype": archetype.primary_label,           # "meme_hunter"
    "secondary_archetypes": archetype.secondary_labels,     # ["scalper"]
    "behavioral_patterns": [p.pattern_name for p in archetype.behavioral_patterns],
    "token_preference": "small_cap",
    "pnl_multiplier_max": 12.5,
    "pnl_multiplier_median": 1.8,
    "open_position_ratio": 0.35,
    "trades_per_day": 8.2,
    "first_buy_avg_mcap_usd": 180000,
    "small_cap_trade_ratio": 0.72,
    "profit_reinvestment_rate": 0.28,
    "profit_add_ratio": 0.45,
    "profitable_avg_holding_seconds": 259200,
    "losing_avg_holding_seconds": 14400,
}
```

---

## 三、改动影响范围

### 需要改动的文件

| 优先级 | 改动 | 文件 | 类型 | 对架构的影响 |
|---|---|---|---|---|
| **P0** | 新增 archetype 分类器 | `style_distillation/archetype.py` | **新文件** | 纯新增，零侵入 |
| **P0** | 扩展 `TradeStatistics` 字段 + 新增统计函数 | `trade_pairing.py` | 扩展 | 仅新增字段和函数，不改配对逻辑 |
| **P1** | 改造 `style_label` 和 `summary` 生成 | `extractors.py` | 修改 | 替换 label 生成逻辑，不改数据流 |
| **P1** | 增强 `DEFAULT_EXTRACTION_PROMPT` | `extractors.py` | 修改 | 仅改字符串常量 |
| **P1** | 扩展 `build_wallet_style_output_schema` | `reflection/service.py` | 扩展 | 新增 schema 字段，向后兼容 |
| **P2** | 注入新特征到 `derived_stats` | `service.py` (`_preprocess_wallet_data`) | 扩展 | 在字典中添加 key |
| **P2** | 新增 Optional 字段 | `models.py` (`WalletStyleProfile`) | 扩展 | 默认值保证向后兼容 |
| **P2** | 传入 market 数据以丰富交易级特征 | `service.py` (`run_distill_features`) | 扩展 | 在调用 `pair_trades` / `compute_trade_statistics` 时传入额外参数 |

### 不需要动的部分

- `pair_trades` 的 FIFO 配对核心算法
- `PiReflectionService` 的运行时编排（`reflection/service.py` 的 `run` 方法）
- `SkillPackageCompiler` 的包结构生成
- `WalletStyleDistillationService` 的阶段流转（`resume_job` / `run_reflection_stage` / `run_build_stage`）
- 所有 `vendor/` 层代码
- `frontend/` 前端代码
- `execution/` 执行适配层

---

## 四、数据流变化示意

```
                        现有流程（不变）
                        ════════════
  AVE inspect_wallet ──→ pair_trades ──→ compute_trade_statistics
        │                    │                    │
        │                    │                    ▼
        │                    │           TradeStatistics (扩展字段)
        │                    │                    │
        │                    │        ┌───────────┘
        │                    │        │
        │                    ▼        ▼
        │              ┌─────────────────────┐
        │              │  classify_archetype  │  ◀── 新增模块
        │              │  (archetype.py)      │
        │              └──────────┬──────────┘
        │                         │
        │                         ▼
        │                  TradingArchetype
        │                  + BehavioralPattern
        │                         │
        ▼                         ▼
  _preprocess_wallet_data ◀── 注入 archetype 结果到 derived_stats
        │
        ▼
  compact_input (LLM 输入)  ◀── 包含 archetype + patterns
        │
        ▼
  Pi Reflection / WalletStyleExtractor  ◀── 增强 prompt + schema
        │
        ▼
  WalletStyleProfile  ◀── 包含 trading_archetype + behavioral_patterns
        │
        ▼
  Skill 编译输出
```

---

## 五、风险与注意事项

1. **市值数据的可用性**：`buy_mcap_usd` 依赖 `inspect_token` 或 `inspect_market` 返回的 `liquidity_usd`。当 AVE 无法返回流动性数据时，需要 fallback 到"unknown"并跳过小市值相关 pattern 的判定。

2. **样本量不足时的行为**：当 `completed_trade_count < 3` 时，archetype 分类器应返回低置信度结果或 fallback 到现有的简单分类逻辑，避免从 2 笔交易中过度解读 pattern。

3. **compact_input 体积控制**：新增的 `derived_stats` 字段是数值型/短字符串，体积增量约 200-300 bytes，在 `_MAX_COMPACT_BYTES` 限制内安全。但 `behavioral_patterns` 的 `evidence_summary` 需要控制长度（建议每条 < 100 字符）。

4. **向后兼容**：所有新增字段使用 Optional/默认值，旧的 job artifact 不会因为缺少新字段而 break。`build_wallet_style_output_schema` 新增的字段不放入 `required`（除 `trading_archetype` 外），确保 LLM 未返回时 `parse_wallet_style_review_report` 不会抛异常。

5. **Prompt 版本管理**：`DEFAULT_EXTRACTION_PROMPT` 的变更会影响所有后续蒸馏结果，建议同步更新 `_DISTILL_STAGE_VERSION`，使缓存自动失效。

6. **archetype 分类器的可测试性**：`archetype.py` 应设计为纯函数（输入 `TradeStatistics` + 交易列表，输出 `TradingArchetype`），便于单元测试。建议在 `tests/` 下新增 `test_archetype_classification.py`，用已知风格的钱包数据验证分类准确性。

---

## 六、实施建议顺序

1. **先写 `archetype.py`**（P0）—— 纯新增文件，可独立开发和测试，不影响任何现有功能
2. **扩展 `TradeStatistics`**（P0）—— 新增字段和计算函数，现有字段不变
3. **改 `extractors.py` 的 prompt 和 label 逻辑**（P1）—— 与 archetype.py 联调
4. **改 `reflection/service.py` 的 schema**（P1）—— 扩展 LLM 输出结构
5. **改 `service.py` 和 `models.py`**（P2）—— 将新特征注入数据流
6. **补充测试用例**—— 用已知风格的钱包数据验证端到端效果

---

## 七、一句话总结

当前蒸馏链的核心问题是：**统计特征已经算了，但没有跨维度交叉做 pattern recognition，也没有给 LLM 一个明确的 archetype taxonomy 来引导输出有辨识度的风格标签**。修复路径是在 `trade_pairing` → 新增 `archetype` 分类器 → 改造 `extractors` + `prompt` + `schema`，整条链路从下到上让 pattern 自然涌现，不需要动大架构。
