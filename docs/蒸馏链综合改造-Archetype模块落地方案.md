# 蒸馏链综合改造-Archetype 模块落地方案

## 1. 目标

把现有 `trade_pairing + derived_stats` 从“统计字段集合”升级成“可供 reflection 和 fallback 直接消费的 pattern/archetype 中间层”。

本模块的目标不是替代 reflection，而是解决两个问题：

- 让风格标签不再只是 `risk_appetite + execution_tempo` 的机械拼接
- 让数据稀疏时 fallback 也能输出有辨识度的 pattern，而不是纯模板描述

## 2. 改动范围

### 核心文件

- `0t-skill_hackson_v2ing/src/ot_skill_enterprise/style_distillation/trade_pairing.py`
- `0t-skill_hackson_v2ing/src/ot_skill_enterprise/style_distillation/service.py`
- `0t-skill_hackson_v2ing/src/ot_skill_enterprise/style_distillation/extractors.py`
- `0t-skill_hackson_v2ing/src/ot_skill_enterprise/style_distillation/models.py`

### 新增文件

- `0t-skill_hackson_v2ing/src/ot_skill_enterprise/style_distillation/archetype.py`

### 下游同步文件

- `0t-skill_hackson_v2ing/src/ot_skill_enterprise/skills_compiler/compiler.py`
- `0t-skill_hackson_v2ing/src/ot_skill_enterprise/skills_compiler/wallet_style_runtime.py`

## 3. 当前问题

当前 `trade_pairing.py` 已经能输出：

- `holding_classification`
- `loss_tolerance_label`
- `averaging_pattern`
- `avg_position_splits`
- `win_rate / profit_factor / expectancy`

但这些字段只是被平铺进 `derived_stats`，没有被交叉成上层语义【现状见现有方案，不在本文件重复引用】。

同时 `WalletStyleExtractor` 仍按单变量阈值构造：

- `execution_tempo`
- `risk_appetite`
- `conviction_profile`
- `style_label = "{risk_appetite}-{execution_tempo}"`

所以现在的问题不是“没算特征”，而是“没有 classification layer”。

## 4. 模块设计

### 4.1 中间产物

定义两个中间对象：

- `BehavioralPattern`
- `TradingArchetype`

### 4.2 输出职责

`TradingArchetype` 至少给出：

- `primary_label`
- `secondary_labels`
- `behavioral_patterns`
- `confidence`
- `evidence`

### 4.3 使用方

Archetype 输出需要被三处消费：

1. reflection 的 compact input
2. fallback extractor
3. skill 编译文案

如果只给 reflection 用，不给 fallback 用，这次改造是不完整的。

## 5. 实施顺序

### 步骤 1：扩展交易级特征

在不改 FIFO 核心配对逻辑的前提下扩展 `CompletedTrade.metadata` 和 `OpenPosition.metadata`。

优先新增：

- `buy_mcap_usd`
- `buy_amount_vs_avg_ratio`
- `is_first_buy_for_token`
- `was_in_profit_when_added`
- `buy_price_usd`
- `sell_price_usd`

说明：

- 这里的重点不是“绝对准确的市场微结构”，而是给 pattern classifier 足够信号
- 市值可接受粗估，只要口径稳定

### 步骤 2：扩展统计级特征

在 `TradeStatistics` 中新增：

- `trades_per_day`
- `open_position_ratio`
- `pnl_multiplier_max`
- `pnl_multiplier_median`
- `profitable_avg_holding_seconds`
- `losing_avg_holding_seconds`
- `profit_reinvestment_rate`
- `first_buy_avg_mcap_usd`
- `small_cap_trade_ratio`
- `profit_add_ratio`

说明：

- 这些特征中，`trades_per_day`、`open_position_ratio`、`small_cap_trade_ratio` 是 archetype 主判定条件
- `profit_add_ratio`、`profit_reinvestment_rate` 更适合 behavioral pattern

### 步骤 3：新增 `archetype.py`

职责分三层：

1. `derive_behavioral_patterns(...)`
2. `score_archetypes(...)`
3. `select_primary_and_secondary(...)`

不要把所有规则堆在一个函数里。

### 步骤 4：回写到 `service.py`

在 `run_distill_features` 内的顺序建议固定为：

1. `pair_trades`
2. `compute_trade_statistics`
3. `classify_archetype`
4. 写入 `trade_pairing` artifact
5. 写入 `preprocessed.derived_stats`
6. 写入 `preprocessed.behavioral_patterns / archetype`

### 步骤 5：升级 `extractors.py`

Extractor 不再负责“创造风格”，而负责：

- 读取 archetype
- 组织 profile narrative
- 组织 review reasoning
- 在 archetype 缺失时才退回原有阈值逻辑

## 6. 建议 taxonomy

第一版不要贪多，控制在 8 个主 archetype 内：

- `scalper`
- `high_freq_rotator`
- `swing_trader`
- `meme_hunter`
- `diamond_hands`
- `degen_sniper`
- `compounding_builder`
- `asymmetric_bettor`

再加一个保底状态：

- `no_stable_archetype`

说明：

- `no_stable_archetype` 必须存在
- 它不是失败，而是合法分类结果

## 7. 模型与字段建议

### `models.py` 建议新增

- `trading_archetype`
- `secondary_archetypes`
- `behavioral_patterns`
- `token_preference`
- `trades_per_day`
- `open_position_ratio`
- `pnl_multiplier_max`

### `derived_stats` 建议新增

- `primary_archetype`
- `secondary_archetypes`
- `behavioral_patterns`
- `archetype_confidence`
- `archetype_evidence_summary`

## 8. 与 Reflection 的耦合点

Archetype 模块先完成，但不要一步到位改 reflection schema。先满足下面两个输出口：

- `compact_input` 可见
- fallback extractor 可消费

Reflection 内容层的 schema/prompt 升级放到下一阶段。

## 9. 风险点

### 风险 1：规则过多导致 classifier 不稳定

控制方法：

- 第一版只保留高信号规则
- 低置信 pattern 只进 evidence，不进 `primary_label`

### 风险 2：特征来源不一致

控制方法：

- 市值、流动性、价格字段统一口径
- 所有 pattern 规则都写明依赖字段

### 风险 3：fallback 与 reflection 输出割裂

控制方法：

- archetype classifier 作为两者共同输入
- 不允许 reflection 有 taxonomy 而 fallback 没 taxonomy

## 10. Definition of Done

- 新增 archetype 与 pattern 中间层
- fallback profile 不再生成 `aggressive-high-frequency-rotation` 这类机械标签
- `compact_input` 中可直接看到 archetype 与 pattern evidence 摘要
- 至少 3 类 archetype 能在现有样本中稳定区分
- `no_stable_archetype` 可作为合法输出，不触发系统级失败

## 11. 建议开发拆分

### 任务 A1

- 扩展交易级字段

### 任务 A2

- 扩展统计级字段

### 任务 A3

- 实现 `archetype.py`

### 任务 A4

- `service.py` 注入 archetype 到 `preprocessed`

### 任务 A5

- `extractors.py` 消费 archetype

### 任务 A6

- compiler/runtime 文案与字段透传

建议按 A1 -> A2 -> A3 -> A4 -> A5 -> A6 顺序开发。
