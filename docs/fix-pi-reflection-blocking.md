# Pi Reflection 阶段阻塞修复方案

> 状态：待实施  
> 影响文件：3 个（`reflection/service.py`、`style_distillation/service.py`、`ot_reflection_mode.ts`）  
> 预计 fallback 率：从 ~70% 降至 <15%

---

## 一、问题是什么

蒸馏链在 Pi reflection 阶段几乎必然失败，产出的 skill 全是模板数据，无法真实执行。

**两条必败路径：**

| 路径 | 原因 | 真实 artifact 证据 |
|------|------|--------------------|
| JSON 截断/损坏 | LLM 输出到一半被截断，`JSON.parse` 失败 | `style-job-344fcc5a65`：`"Expected ',' or ']' after array element in JSON at position 2931"` |
| 质量门禁拒绝 | LLM 成功输出高质量分析，但某个字段值不匹配系统常量 | `style-job-6ff896f117`：LLM 写了 `adapter: "bsc_dex"`，门禁要求必须是 `"onchainos_cli"` |

两条路都通向同一个结果：调用 `WalletStyleExtractor.extract()` 生成确定性模板 → Pi 调用白费。

---

## 二、根因分析

核心问题：**让 LLM 做了它不擅长的事，再用它无法满足的标准验证。**

```
让 LLM 在一次调用中同时完成 4 件事：
  ① 分析钱包风格 → profile    ← LLM 擅长
  ② 设计交易策略 → strategy   ← LLM 擅长
  ③ 填系统标识符 → execution_intent ← LLM 不可能猜对
  ④ 做审批判断   → review     ← LLM 擅长

然后要求输出为完美的嵌套 JSON（~3000-4000 tokens），
  用 max_tokens=2200 的预算 ← 不够
  用 ~4KB 的 JSON Schema 做 prompt ← 浪费 token 且 LLM 遵从率低
  再用硬编码的字符串比较做验证 ← 几乎必定失败
```

---

## 三、修复方案总览

| # | 改动 | 一句话描述 | 解决什么 |
|---|------|-----------|---------|
| 1 | `execution_intent` 移出 LLM 输出 | 系统常量由代码填，不让 LLM 猜 | 质量门禁拒绝 |
| 2 | TS 层引入容错 JSON 解析 + 保留原始文本 | 截断的 JSON 能修复，失败了也保留原文 | JSON 损坏 |
| 3 | retry 循环修复 | `ValueError` 也能重试，不再只试一次 | 重试失效 |
| 4 | 质量门禁从"拒绝"改为"修复" | 字段值不对就替换，不丢弃整个结果 | 高质量输出被丢弃 |
| 5 | fallback 前增加原始文本抢救 | 从损坏的 JSON 中提取可用部分 | fallback 率过高 |

下面逐条给出精确的代码改动说明。

---

## 四、改动 1：`execution_intent` 从 LLM 输出中移除

### 4.1 为什么

`execution_intent` 包含 `adapter`、`mode`、`preferred_workflow` 等字段，这些是系统内部的标识符常量（`onchainos_cli`、`swap_execute`），不是 LLM 能推理出来的。当前代码已经有一个函数 `_fallback_execution_intent()` 能正确生成这些值。让 LLM 生成只会带来两个害处：

1. 浪费 ~500 output tokens
2. 生成的值（如 `bsc_dex`）被质量门禁拒绝

### 4.2 改什么

#### 文件 A：`src/ot_skill_enterprise/reflection/service.py`

**4.2.1 — 修改 `build_wallet_style_output_schema()`（第 92-186 行）**

从 schema 中删除 `execution_intent` 整个节点。

修改前 `required` 数组（第 95 行）：

```python
"required": ["profile", "strategy", "execution_intent", "review"],
```

修改后：

```python
"required": ["profile", "strategy", "review"],
```

同时删除 `properties` 中整个 `"execution_intent": { ... }` 块（第 158-172 行）。

**4.2.2 — 修改 `parse_wallet_style_review_report()`（第 189-305 行）**

核心改动：不再从 LLM 输出解析 `execution_intent`，改为接收外部传入。

修改前函数签名（第 189 行）：

```python
def parse_wallet_style_review_report(
    normalized_output: Mapping[str, Any],
    *,
    wallet: str,
    chain: str,
) -> WalletStyleReviewReport:
```

修改后：

```python
def parse_wallet_style_review_report(
    normalized_output: Mapping[str, Any],
    *,
    wallet: str,
    chain: str,
    execution_intent: ExecutionIntent | None = None,
) -> WalletStyleReviewReport:
```

修改前解析逻辑（第 196-200 行）：

```python
    profile_payload = _mapping(payload.get("profile"))
    strategy_payload = _mapping(payload.get("strategy"))
    execution_intent_payload = _mapping(payload.get("execution_intent"))
    review_payload = _mapping(payload.get("review"))
    if not profile_payload or not strategy_payload or not execution_intent_payload or not review_payload:
        raise ValueError("reflection output must include profile, strategy, execution_intent, and review objects")
```

修改后：

```python
    profile_payload = _mapping(payload.get("profile"))
    strategy_payload = _mapping(payload.get("strategy"))
    review_payload = _mapping(payload.get("review"))
    if not profile_payload or not strategy_payload or not review_payload:
        raise ValueError("reflection output must include profile, strategy, and review objects")
```

删除第 246-261 行（从 LLM 输出解析 `ExecutionIntent` 的代码）和第 286-298 行（所有 `execution_intent` 相关的质量校验）。

在返回 `WalletStyleReviewReport` 前（第 299 行附近），使用外部传入的 `execution_intent`：

```python
    resolved_execution_intent = execution_intent  # 由调用方确定性生成
    if resolved_execution_intent is None:
        raise ValueError("execution_intent must be provided by caller")
    return WalletStyleReviewReport(
        profile=profile,
        strategy=strategy,
        execution_intent=resolved_execution_intent,
        review=review,
        normalized_output=payload,
    )
```

#### 文件 B：`src/ot_skill_enterprise/style_distillation/service.py`

**4.2.3 — 修改 `_resolve_reflection_report()` 中的调用（第 1453-1468 行）**

修改前：

```python
                reflection_report = parse_wallet_style_review_report(
                    reflection_result.normalized_output,
                    wallet=wallet,
                    chain=chain,
                )
                return (
                    reflection_report.profile,
                    reflection_report.strategy,
                    reflection_report.execution_intent,
                    ...
                )
```

修改后：

```python
                deterministic_execution_intent = _fallback_execution_intent(
                    preprocessed, _fallback_strategy_spec(preprocessed, {})
                )
                reflection_report = parse_wallet_style_review_report(
                    reflection_result.normalized_output,
                    wallet=wallet,
                    chain=chain,
                    execution_intent=deterministic_execution_intent,
                )
                return (
                    reflection_report.profile,
                    reflection_report.strategy,
                    reflection_report.execution_intent,
                    ...
                )
```

注意：`_fallback_execution_intent` 在这里不是"降级"的意思，而是"确定性生成系统配置"。可以考虑将函数重命名为 `_build_execution_intent` 以消除语义歧义，但不是本次必须改动。

**4.2.4 — 修改 `_reflection_hard_constraints()`（第 1382-1395 行）**

删除两行不再需要的约束：

```python
            "execution_intent.adapter must be onchainos_cli.",
            "execution_intent.preferred_workflow must be concrete and executable.",
```

**4.2.5 — 修改 `_build_reflection_spec()` 中 mock 路径（第 1621-1632 行）**

删除 mock_response 中的 `"execution_intent"` 键，因为 schema 不再要求它。

#### 文件 C：`vendor/pi_runtime/upstream/coding_agent/src/ot_reflection_mode.ts`

**4.2.6 — `buildReflectionPrompt()` 无需改动**

该函数直接读 `job.expected_output_schema` 并序列化到 prompt 中。上游 Python 传入的 schema 已经不包含 `execution_intent`，TS 侧自动继承。

---

## 五、改动 2：TS 层引入容错 JSON 解析 + 保留原始文本

### 5.1 为什么

当前 `parseJsonObject` 用原生 `JSON.parse`，LLM 输出中一个 trailing comma 或截断就导致整个结果报废。且失败后 **原始 LLM 文本被丢弃**（只存了 error message），导致：

- 无法诊断 LLM 到底写了什么
- Python 侧无法做二次解析尝试

而同一仓库中已有现成的容错解析函数：

```
vendor/pi_runtime/upstream/ai/src/utils/json-parse.ts
```

```typescript
// 已有代码，先 JSON.parse，失败用 partial-json 修复
export function parseStreamingJson<T = any>(partialJson: string | undefined): T { ... }
```

### 5.2 改什么

#### 文件：`vendor/pi_runtime/upstream/coding_agent/src/ot_reflection_mode.ts`

**5.2.1 — 添加 import（文件顶部）**

```typescript
import { parseStreamingJson } from "../ai/src/utils/json-parse";
```

> 注意：需要确认 TS 项目的路径别名配置。如果 `coding_agent` 和 `ai` 是独立包，可能需要通过包名引入，或将 `parseStreamingJson` 复制到 `ot_reflection_mode.ts` 本地（函数体只有 15 行）。

**5.2.2 — 修改 `parseJsonObject` 函数（第 117-124 行）**

修改前：

```typescript
function parseJsonObject(text: string): Record<string, unknown> {
	const normalized = normalizeJsonText(text);
	const parsed = JSON.parse(normalized);
	if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
		throw new Error("Reflection output must be a JSON object");
	}
	return parsed as Record<string, unknown>;
}
```

修改后：

```typescript
function parseJsonObject(text: string): Record<string, unknown> {
	const normalized = normalizeJsonText(text);
	let parsed: unknown;
	try {
		parsed = JSON.parse(normalized);
	} catch {
		parsed = parseStreamingJson(normalized);
	}
	if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
		throw new Error("Reflection output must be a JSON object");
	}
	return parsed as Record<string, unknown>;
}
```

**5.2.3 — 修改 catch 块，保留原始文本（第 525-527 行）**

修改前：

```typescript
	} catch (error) {
		const errorMessage = String(error instanceof Error ? error.message : error);
		await writeJson(rawPath, { error: errorMessage });
```

修改后：

```typescript
	} catch (error) {
		const errorMessage = String(error instanceof Error ? error.message : error);
		const preservedText = typeof rawOutput?.text === "string" ? rawOutput.text : undefined;
		await writeJson(rawPath, { error: errorMessage, raw_text: preservedText });
```

同时修改返回体中 `raw_output`（第 553 行）：

修改前：

```typescript
				raw_output: { error: errorMessage },
```

修改后：

```typescript
				raw_output: { error: errorMessage, raw_text: preservedText },
```

---

## 六、改动 3：修复 retry 循环

### 6.1 为什么

当前 retry 循环有两个问题：

1. **`ValueError` 直接 break**：JSON 解析失败抛 `ValueError`，不是 `ReflectionQualityError`，所以跳过了第二次尝试
2. **retry 没有降级策略**：第二次尝试的 prompt 比第一次更大（多了 retry hint），如果第一次因 prompt 太大而截断，第二次只会更糟

### 6.2 改什么

#### 文件：`src/ot_skill_enterprise/style_distillation/service.py`

**6.2.1 — 修改 `_resolve_reflection_report()` 的异常处理（第 1470-1477 行）**

修改前：

```python
            except ReflectionQualityError as exc:
                last_error = exc
                if attempt == 0:
                    continue
                break
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                break
```

修改后：

```python
            except (ValueError, ReflectionQualityError) as exc:
                last_error = exc
                if attempt == 0:
                    continue
                break
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                break
```

**6.2.2 — retry 时缩减 compact_input（第 1441-1447 行）**

修改前，每次 attempt 都用完整的 `preprocessed`：

```python
            reflection_spec = self._build_reflection_spec(
                wallet=wallet,
                chain=chain,
                prompt=prompt,
                preprocessed=preprocessed,
                artifacts_dir=artifacts_dir,
            )
```

修改后，attempt > 0 时使用精简版：

```python
            retry_preprocessed = preprocessed
            if attempt > 0:
                retry_preprocessed = _minimal_compact_payload(preprocessed)
            reflection_spec = self._build_reflection_spec(
                wallet=wallet,
                chain=chain,
                prompt=prompt,
                preprocessed=retry_preprocessed,
                artifacts_dir=artifacts_dir,
            )
```

`_minimal_compact_payload` 已存在于同文件第 526 行，无需新增。

---

## 七、改动 4：质量门禁从"拒绝"改为"修复 + 标记"

### 7.1 为什么

当前 `parse_wallet_style_review_report` 中有 11 个 `raise ReflectionQualityError` 调用。其中大部分是**可修复的字段级问题**（如 wallet 大小写不匹配、style_label 太泛化），不应该因此丢弃整个输出。

### 7.2 改什么

#### 文件：`src/ot_skill_enterprise/reflection/service.py`

**7.2.1 — 在函数内部增加 `auto_fixes` 列表追踪修复记录**

在 `parse_wallet_style_review_report` 函数开头（第 195 行后）添加：

```python
    auto_fixes: list[str] = []
```

**7.2.2 — wallet/chain 不匹配：覆盖而非拒绝（第 270-273 行）**

修改前：

```python
    if _lower_text(profile.wallet) != _lower_text(wallet):
        raise ReflectionQualityError(...)
    if _lower_text(profile.chain) != _lower_text(chain):
        raise ReflectionQualityError(...)
```

修改后：

```python
    if _lower_text(profile.wallet) != _lower_text(wallet):
        auto_fixes.append(f"profile.wallet fixed: {profile.wallet} -> {wallet}")
        profile.wallet = wallet
    if _lower_text(profile.chain) != _lower_text(chain):
        auto_fixes.append(f"profile.chain fixed: {profile.chain} -> {chain}")
        profile.chain = chain
```

**7.2.3 — generic 标签：保留 raise（第 274-285 行）**

这些校验是**真正不可修复的质量问题**，应保留 `raise`：

- `style_label in GENERIC_STYLE_LABELS`
- `setup_label in GENERIC_SETUP_LABELS`
- `entry_conditions too generic`

**7.2.4 — 将修复记录写入 metadata**

在返回前（第 299 行附近）：

```python
    final_normalized = dict(payload)
    if auto_fixes:
        final_normalized["_auto_fixes"] = auto_fixes
    return WalletStyleReviewReport(
        profile=profile,
        strategy=strategy,
        execution_intent=resolved_execution_intent,
        review=review,
        normalized_output=final_normalized,
    )
```

---

## 八、改动 5：fallback 前增加原始文本抢救

### 8.1 为什么

经过改动 2，即使 TS 层 JSON 解析最终失败，原始 LLM 文本也会保留在 `raw_output.raw_text` 中。在 Python 侧进入 fallback 之前，应该尝试从这段文本中抢救可用数据。

### 8.2 改什么

#### 文件：`src/ot_skill_enterprise/style_distillation/service.py`

**8.2.1 — 在 `_resolve_reflection_report()` 第 1479 行前添加原始文本抢救逻辑**

修改前（第 1478-1481 行）：

```python
        # retry 循环结束，直接进入 fallback
        profile, review = extractor.extract(preprocessed, system_prompt=prompt)
        strategy = _fallback_strategy_spec(preprocessed, profile.to_dict())
        execution_intent = _fallback_execution_intent(preprocessed, strategy)
```

修改后：

```python
        # --- 原始文本抢救：在 fallback 之前尝试从 raw_text 中提取 ---
        if last_result is not None:
            salvaged = _try_salvage_from_raw_text(
                last_result.raw_output,
                wallet=wallet,
                chain=chain,
                preprocessed=preprocessed,
            )
            if salvaged is not None:
                sal_profile, sal_strategy, sal_review = salvaged
                sal_execution_intent = _fallback_execution_intent(preprocessed, sal_strategy)
                last_result.metadata = {
                    **dict(last_result.metadata or {}),
                    "salvaged_from_raw_text": True,
                }
                return (
                    sal_profile,
                    sal_strategy,
                    sal_execution_intent,
                    sal_review,
                    last_result,
                    last_spec,
                    last_envelope,
                    False,  # 不算 fallback，因为数据来自 LLM
                    last_result.review_backend + ":salvaged",
                )

        # 真正的 fallback：LLM 输出完全不可用
        profile, review = extractor.extract(preprocessed, system_prompt=prompt)
        strategy = _fallback_strategy_spec(preprocessed, profile.to_dict())
        execution_intent = _fallback_execution_intent(preprocessed, strategy)
```

**8.2.2 — 添加 `_try_salvage_from_raw_text` 函数**

在 `_fallback_execution_intent` 函数之后（第 1101 行附近）添加：

```python
def _try_salvage_from_raw_text(
    raw_output: dict[str, Any],
    *,
    wallet: str,
    chain: str,
    preprocessed: dict[str, Any],
) -> tuple[Any, StrategySpec, Any] | None:
    """尝试从 LLM 原始文本中提取 profile + strategy + review。
    
    当 TS 层 JSON.parse 失败但保留了 raw_text 时调用。
    不做质量校验（那是调用方的责任），只做结构提取。
    """
    raw_text = str(raw_output.get("raw_text") or "").strip()
    if not raw_text or len(raw_text) < 100:
        return None

    # 尝试修复常见的截断 JSON
    text = raw_text
    # 去除 markdown fence
    import re
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()
    # 截取到最外层 {}
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        text = text[first_brace : last_brace + 1]
    # 尝试补全可能的截断
    open_braces = text.count("{") - text.count("}")
    open_brackets = text.count("[") - text.count("]")
    if open_braces > 0 or open_brackets > 0:
        text = text.rstrip().rstrip(",")
        text += "]" * max(0, open_brackets) + "}" * max(0, open_braces)

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None

    if not isinstance(payload, dict):
        return None

    try:
        from ot_skill_enterprise.reflection.service import parse_wallet_style_review_report
        execution_intent = _fallback_execution_intent(
            preprocessed, _fallback_strategy_spec(preprocessed, {})
        )
        report = parse_wallet_style_review_report(
            payload, wallet=wallet, chain=chain, execution_intent=execution_intent,
        )
        return (report.profile, report.strategy, report.review)
    except Exception:  # noqa: BLE001
        return None
```

---

## 九、改动顺序与依赖关系

```
改动 2（TS 保留原始文本）  ← 改动 5 依赖它
      ↓
改动 1（移除 execution_intent）
      ↓
改动 4（质量门禁修复）
      ↓
改动 3（retry 循环修复）
      ↓
改动 5（原始文本抢救）
```

建议按 2 → 1 → 4 → 3 → 5 的顺序实施。每个改动可独立验证。

---

## 十、验证方法

### 10.1 单元测试

已有测试文件：`tests/test_wallet_style_reflection.py`（~83KB，覆盖了现有的 parse / quality gate 逻辑）。

每个改动需要对应的测试更新：

| 改动 | 需要更新/新增的测试 |
|------|-------------------|
| 1 | `test_parse_wallet_style_review_report` 系列：移除 `execution_intent` 相关断言，验证 `execution_intent=None` 时 raise |
| 2 | 在 TS 侧添加 `parseJsonObject` 测试：truncated JSON、trailing comma、markdown fence 包裹 |
| 3 | `test_generic_reflection_output_uses_single_outer_fallback`：验证 `ValueError` 触发 retry 而非 break |
| 4 | 新增 `test_wallet_chain_mismatch_auto_fixed`：验证 wallet/chain 被覆盖而非拒绝 |
| 5 | 新增 `test_salvage_from_truncated_raw_text`：验证截断 JSON 能被修复并使用 |

### 10.2 端到端验证

用现有的失败 job 重跑：

```bash
# 重跑 style-job-344fcc5a65（之前因 JSON 截断失败的 job）
ot-enterprise style distill \
  --wallet 0xd5b63edd7cdf4c23718cc8a6a83e312dc8ae3fe1 \
  --chain bsc
```

验证标准：
- `reflection_result.json` 中 `fallback_used` 为 `false`
- `stage_reflection.json` 中 `profile.style_label` 不是模板值（不是 `{risk_appetite}-{execution_tempo}` 格式）
- `execution_intent.adapter` 是 `onchainos_cli`（由代码确定性生成）
- `summary.json` 中 `strategy_quality` 不是 `"low"`

### 10.3 回归验证

```bash
# 跑已有测试套件
python -m pytest tests/test_wallet_style_reflection.py -v
python -m pytest tests/test_wallet_style_context_layering.py -v
```

---

## 十一、回滚方案

每个改动都可以独立 revert。如果需要紧急回滚：

1. 改动 1 回滚：恢复 schema 中的 `execution_intent`，恢复质量门禁 → 回到原有行为
2. 改动 2 回滚：恢复 `parseJsonObject` 为原生 `JSON.parse` → 回到原有行为
3. 改动 3-5 回滚：恢复 `_resolve_reflection_report` → 回到原有行为

环境变量控制（可选，用于灰度上线）：

```bash
# 关闭所有新行为，回到旧逻辑
OT_REFLECTION_LEGACY_MODE=1
```

---

## 十二、效果预估

| 指标 | 修复前 | 修复后 |
|------|--------|--------|
| Pi reflection 成功率 | ~10-20% | ~70-85% |
| fallback 率 | ~70-80% | <15% |
| 下游 skill 包含 LLM 分析 | 几乎不可能 | 绝大多数情况 |
| 单次 Pi 调用的 output token 需求 | ~3500（含 execution_intent） | ~2500（不含） |
| 质量门禁自动修复覆盖 | 0% | wallet/chain mismatch 100% |
