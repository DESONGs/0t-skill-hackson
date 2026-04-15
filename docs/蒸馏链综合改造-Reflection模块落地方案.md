# 蒸馏链综合改造-Reflection 模块落地方案

## 1. 目标

Reflection 模块本次改造不是一个任务，而是两条子线：

- 子线 R1：runtime 稳定性与失败分型
- 子线 R2：内容契约、quality gate 与 archetype 对接

这两条线不能混做。

## 2. 改动范围

### 核心文件

- `0t-skill_hackson_v2ing/src/ot_skill_enterprise/reflection/service.py`
- `0t-skill_hackson_v2ing/src/ot_skill_enterprise/style_distillation/service.py`
- `0t-skill_hackson_v2ing/src/ot_skill_enterprise/runtime/executor.py`
- `0t-skill_hackson_v2ing/vendor/pi_runtime/upstream/coding_agent/src/ot_reflection_mode.ts`
- `0t-skill_hackson_v2ing/vendor/pi_runtime/upstream/coding_agent/src/ot_runtime_entry.ts`

### 二级影响文件

- `0t-skill_hackson_v2ing/src/ot_skill_enterprise/style_distillation/context.py`
- `0t-skill_hackson_v2ing/src/ot_skill_enterprise/style_distillation/models.py`
- `0t-skill_hackson_v2ing/src/ot_skill_enterprise/qa/`

## 3. 当前问题拆分

### R1：runtime 稳定性问题

现有 reflection 调用本质上是一次 `completeSimple()`，缺少：

- provider fallback
- model fallback
- health check
- 失败原因细分
- 更完整的 structured output 策略

因此会直接出现：

- `Request was aborted.`
- timeout
- empty normalized output
- raw text 有内容但 parse 失败

### R2：内容契约问题

现有 validator 要求：

- 顶层结构完整
- `entry_conditions` 不能为空
- generic label / summary 直接拒绝
- `dominant_actions / preferred_tokens / execution_rules` 缺一不可

这会把“数据不足但分析合理”的结果也打入 fallback。

## 4. 推荐顺序

### 第一步：先做 R1

R1 是所有后续工作的底座。未完成前，不要扩大 schema。

必须先做：

- 失败类型标准化
- timeout 与 model fallback
- raw output salvage
- request/response observability

### 第二步：再做 R2 的状态机骨架

在不引入 archetype 新字段前，先把 validator 改成“分级接受”。

新增三类结果：

- `strategy_ready`
- `insufficient_signal`
- `runtime_failed`

这样系统先学会区分“没模式”和“系统坏了”。

### 第三步：等 Archetype 模块完成后，再做 R2 的 schema/prompt 升级

这一步才引入：

- archetype taxonomy
- behavioral patterns
- evidence 字段
- prompt 中的具体分类指令

## 5. R1 详细落地方案

### R1.1 failure taxonomy

在 Python 层和 TS 层统一失败类型：

- `runtime_abort`
- `runtime_timeout`
- `provider_unavailable`
- `empty_output`
- `json_parse_failed`
- `schema_rejected`
- `generic_rejected`

要求：

- artifact 中必须能看到原始类型
- 最终 job 结果不能只剩一句 `Request was aborted.`

### R1.2 timeout 策略

建议从固定超时改为分层超时：

- transport timeout
- model completion timeout
- overall reflection stage timeout

并把超时参数落在统一配置表，而不是 Python/TS 各自有默认值但没有统一口径。

### R1.3 model/provider fallback

当前策略过于单次调用化。建议最少支持：

1. 主模型
2. 备用模型
3. provider fallback

同时把“失败原因 + 使用的 provider/model”写入 artifact。

### R1.4 structured output 能力核查

优先级很高，但属于 feasibility work：

- 如果底层模型支持 JSON mode 或 tool/response schema，优先改用
- 如果不支持，再保留当前 `text -> parseJsonObject`

### R1.5 observability

至少落 4 个指标：

- reflection success rate
- fallback rate
- median latency
- failure type breakdown

没有这组基线，后续所有优化都无法证明效果。

## 6. R2 详细落地方案

### R2.1 validator 从硬拒绝改为分级接受

目标不是“放松标准”，而是把输出分流：

- A 类：可直接生成 skill
- B 类：分析成立但证据不足，不生成 skill
- C 类：系统错误，走 fallback

### R2.2 新增合法状态

建议在 `review.status` 体系中明确：

- `generate`
- `generate_with_low_confidence`
- `insufficient_signal`
- `no_pattern_detected`
- `needs_manual_review`

其中：

- `insufficient_signal` 与 `no_pattern_detected` 是成功态，不应直接算失败

### R2.3 generic gate 改造原则

不建议简单删除 generic gate。

建议改为：

- generic label 降低 `strategy_quality`
- generic summary 进入 warning
- 只有“结构缺失 + 无 evidence + 无合法 no-pattern 状态”时才 reject

### R2.4 `entry_conditions` 校验改造

现状对单个字段空缺过于敏感。建议：

- 单个 condition 缺 `data_source` 时允许自动修补/降级
- 只有整个 `entry_conditions` 毫无信息时才 reject

### R2.5 memory 与 quality gate 对齐

当前 `strategy_quality in {"", "low", "insufficient_data"}` 会导致结果不记忆。

建议改为：

- `strategy_ready` 结果正常记忆
- `insufficient_signal` 结果记忆为低权重 memory
- `runtime_failed` 不记忆

否则数据稀疏地址会永远失去被历史信息辅助的机会。

## 7. 与 Archetype 模块的接口约定

Reflection 升级前，先约定这些输入字段：

- `derived_stats.primary_archetype`
- `derived_stats.secondary_archetypes`
- `derived_stats.behavioral_patterns`
- `derived_stats.archetype_confidence`
- `derived_stats.archetype_evidence_summary`

Reflection 的 prompt/schema 升级必须直接消费这些字段，不要再让 LLM 从零发明 taxonomy。

## 8. 风险点

### 风险 1：先扩 schema，后补数据

会导致新的字段继续走泛化输出，fallback 反而更多。

### 风险 2：只修 runtime，不修 validator

会导致“能跑完，但还是全是 bland output”。

### 风险 3：只放松 gate，不补 taxonomy

会导致泛化内容更容易通过，但质量下降。

## 9. Definition of Done

- fallback 率可按失败类型拆解
- `runtime_failed` 与 `insufficient_signal` 完全区分
- generic 输出不再一刀切触发 fallback
- reflection schema 能承接 archetype/pattern 字段
- memory/quality gate 认识新的状态机

## 10. 建议开发拆分

### 任务 R1

- runtime 失败分类与 artifact 扩展

### 任务 R2

- timeout 与 model fallback

### 任务 R3

- validator 状态机改造

### 任务 R4

- prompt/schema 升级并对接 archetype

### 任务 R5

- memory/quality gate/QA 对齐

建议顺序：

`R1 -> R2 -> R3 -> 等待 Archetype 完成 -> R4 -> R5`
