# 架构层问题修复方案（Phase 2）

> 状态：待实施  
> 前置：`fix-pi-reflection-blocking.md` 中 5 项改动已落地并通过 QA  
> 影响文件：8 个  
> 按优先级排序，可分批交付

---

## 一、问题全景

Phase 1 修复了 reflection 阶段的 JSON 解析、retry 循环和质量门禁问题。本轮 QA 在更深的架构层面发现 8 个问题。


| #   | 严重度    | 一句话描述                                                  | 影响文件                                      |
| --- | ------ | ------------------------------------------------------ | ----------------------------------------- |
| 1   | **P0** | 上下文组装的 context/hard_constraints/memory 在序列化时全部丢失       | `reflection/models.py`                    |
| 2   | **P1** | `parseStreamingJson` 解析失败返回 `{}`，TS 层虚假成功              | `json-parse.ts` + `ot_reflection_mode.ts` |
| 3   | **P1** | runtime/onchainos 子进程无默认超时，可无限阻塞                       | `executor.py` + `onchainos_cli.py`        |
| 4   | **P1** | Trade pairing 无 `token_amount` 时只配一笔 buy，统计失真          | `trade_pairing.py`                        |
| 5   | **P2** | coordinator 异常路径未收口 session/invocation                 | `coordinator.py`                          |
| 6   | **P3** | `prepare_only` 模式下 `execution_readiness` 标记不精确（仅影响无凭证的开发/CI 环境，生产环境必配 OKX 凭证，不受影响） | `onchainos_cli.py`                        |
| 7   | **P2** | `StyleDistillationSummary.to_dict` 的 `created_at` 未序列化 | `style_distillation/models.py`            |
| 8   | **P2** | `executor.py` 成功路径 stdout 为空/非 JSON 时未防护               | `executor.py`                             |


---

## 二、改动 1（P0）：修复上下文组装序列化断裂

### 2.1 问题是什么

这是当前系统最严重的隐性缺陷。Python 侧组装了完整的 reflection 上下文（derived memory、review hints、hard constraints、retry reason），但在序列化传给 TS 运行时的过程中**全部丢失**。LLM 实际从未收到这些约束。

**根因**：两个 Envelope 类的字段名不匹配。

生产者 `EphemeralContextEnvelope.to_dict()`（`context.py` 第 75-82 行）输出：

```python
{
    "context": self.context,           # 组装好的 fenced 文本
    "sources": ...,
    "review_hints": ...,               # review hints 列表
    "memory_items": ...,               # derived memory 列表
    "hard_constraints": [...],         # 硬约束列表
}
```

消费者 `ReflectionContextEnvelope.from_value()`（`reflection/models.py` 第 79-83 行）读取：

```python
memory    = payload.get("memory") or payload.get("memories")      # 找不到 → 空
hints     = payload.get("hints") or payload.get("hint_blocks")    # 找不到 → 空
context_sources = payload.get("context_sources") or payload.get("sources")  # OK
metadata  = payload.get("metadata")                                # 找不到 → 空
# context、hard_constraints → 完全不读取 → 丢弃
```

**数据流追踪**：

```
build_reflection_envelope()  →  EphemeralContextEnvelope
        ↓ .to_dict()
reflection_spec.injected_context = envelope.to_dict()   [service.py 第 1584 行]
        ↓
spec.injected_context_envelope()                        [models.py 第 132-133 行]
  → ReflectionContextEnvelope.from_value(self.injected_context)
        ↓ 字段名不匹配，context/hard_constraints/memory/hints 全部丢失
spec.to_dict()["injected_context"]                      [models.py 第 159 行]
        ↓
runtime_input["injected_context"]                       [reflection/service.py 第 341 行]
        ↓
TS buildInjectedContextSection()                        [ot_reflection_mode.ts 第 193-194 行]
  injectedContext["context"]          → undefined
  injectedContext["hard_constraints"] → undefined
  fencedBlocks["memory"]              → ""
  fencedBlocks["hints"]               → ""
```

**结论**：Phase 1 中所有围绕 `_reflection_hard_constraints()` 的改动（添加/删除约束文本），以及 retry hint、derived memory 注入，**在 LLM 侧完全不生效**。

### 2.2 改什么

#### 文件：`src/ot_skill_enterprise/reflection/models.py`

**方案 A（推荐）**：在 `ReflectionContextEnvelope` 中增加缺失字段，并在 `from_value` 中正确映射。

**2.2.1 — 修改 `ReflectionContextEnvelope` 类定义（第 65-70 行）**

修改前：

```python
@dataclass(slots=True)
class ReflectionContextEnvelope:
    memory: tuple[str, ...] = field(default_factory=tuple)
    hints: tuple[str, ...] = field(default_factory=tuple)
    context_sources: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    metadata: dict[str, Any] = field(default_factory=dict)
```

修改后：

```python
@dataclass(slots=True)
class ReflectionContextEnvelope:
    memory: tuple[str, ...] = field(default_factory=tuple)
    hints: tuple[str, ...] = field(default_factory=tuple)
    context_sources: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    metadata: dict[str, Any] = field(default_factory=dict)
    context: str = ""
    hard_constraints: tuple[str, ...] = field(default_factory=tuple)
    memory_items: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    review_hints: tuple[dict[str, Any], ...] = field(default_factory=tuple)
```

**2.2.2 — 修改 `from_value()`（第 72-84 行）**

修改前：

```python
    @classmethod
    def from_value(cls, value: Any) -> "ReflectionContextEnvelope":
        if isinstance(value, cls):
            return value
        if hasattr(value, "model_dump"):
            value = value.model_dump(mode="json")
        payload = dict(value or {}) if isinstance(value, dict) else {}
        return cls(
            memory=_strings(payload.get("memory") or payload.get("memories")),
            hints=_strings(payload.get("hints") or payload.get("hint_blocks")),
            context_sources=_context_sources(payload.get("context_sources") or payload.get("sources")),
            metadata=dict(payload.get("metadata") or {}),
        )
```

修改后：

```python
    @classmethod
    def from_value(cls, value: Any) -> "ReflectionContextEnvelope":
        if isinstance(value, cls):
            return value
        if hasattr(value, "model_dump"):
            value = value.model_dump(mode="json")
        payload = dict(value or {}) if isinstance(value, dict) else {}
        return cls(
            memory=_strings(payload.get("memory") or payload.get("memories")),
            hints=_strings(payload.get("hints") or payload.get("hint_blocks")),
            context_sources=_context_sources(payload.get("context_sources") or payload.get("sources")),
            metadata=dict(payload.get("metadata") or {}),
            context=str(payload.get("context") or ""),
            hard_constraints=_strings(payload.get("hard_constraints")),
            memory_items=tuple(
                dict(item) for item in (payload.get("memory_items") or []) if isinstance(item, dict)
            ),
            review_hints=tuple(
                dict(item) for item in (payload.get("review_hints") or []) if isinstance(item, dict)
            ),
        )
```

**2.2.3 — 修改 `has_context` 属性（第 86-88 行）**

修改前：

```python
    @property
    def has_context(self) -> bool:
        return bool(self.memory or self.hints or self.context_sources or self.metadata)
```

修改后：

```python
    @property
    def has_context(self) -> bool:
        return bool(
            self.memory or self.hints or self.context_sources or self.metadata
            or self.context or self.hard_constraints or self.memory_items or self.review_hints
        )
```

**2.2.4 — 修改 `to_dict()`（第 90-101 行）**

修改前：

```python
    def to_dict(self) -> dict[str, Any]:
        return {
            "memory": list(self.memory),
            "hints": list(self.hints),
            "context_sources": [_json_safe(source) for source in self.context_sources],
            "metadata": _json_safe(self.metadata),
            "fenced_blocks": {
                "memory": _fenced_block("memory", self.memory),
                "hints": _fenced_block("hint", self.hints),
            },
            "has_context": self.has_context,
        }
```

修改后：

```python
    def to_dict(self) -> dict[str, Any]:
        return {
            "memory": list(self.memory),
            "hints": list(self.hints),
            "context_sources": [_json_safe(source) for source in self.context_sources],
            "metadata": _json_safe(self.metadata),
            "context": self.context,
            "hard_constraints": list(self.hard_constraints),
            "memory_items": [_json_safe(item) for item in self.memory_items],
            "review_hints": [_json_safe(item) for item in self.review_hints],
            "fenced_blocks": {
                "memory": _fenced_block("memory", self.memory),
                "hints": _fenced_block("hint", self.hints),
            },
            "has_context": self.has_context,
        }
```

**2.2.5 — 修改 `user_payload()`（第 103-112 行）**

在 `user_payload` 返回的 dict 中同步透传新字段：

修改前：

```python
    def user_payload(self) -> dict[str, Any]:
        payload = self.to_dict()
        return {
            "memory": payload["memory"],
            "hints": payload["hints"],
            "context_sources": payload["context_sources"],
            "metadata": payload["metadata"],
            "fenced_blocks": payload["fenced_blocks"],
            "has_context": payload["has_context"],
        }
```

修改后：

```python
    def user_payload(self) -> dict[str, Any]:
        payload = self.to_dict()
        return {
            "memory": payload["memory"],
            "hints": payload["hints"],
            "context_sources": payload["context_sources"],
            "metadata": payload["metadata"],
            "context": payload["context"],
            "hard_constraints": payload["hard_constraints"],
            "memory_items": payload["memory_items"],
            "review_hints": payload["review_hints"],
            "fenced_blocks": payload["fenced_blocks"],
            "has_context": payload["has_context"],
        }
```

### 2.3 验证方法

**单元测试**：新增测试用例验证 round-trip 不丢失数据。

```python
def test_ephemeral_envelope_round_trip_through_reflection_envelope():
    from ot_skill_enterprise.style_distillation.context import EphemeralContextEnvelope
    from ot_skill_enterprise.reflection.models import ReflectionContextEnvelope

    original = EphemeralContextEnvelope(
        context="```hard-constraints\n- wallet must be 0xabc\n```",
        sources=[{"kind": "hard_constraint", "value": "wallet must be 0xabc"}],
        review_hints=[{"stage": "review", "next_stage_hints": ["focus on risk"]}],
        memory_items=[{"summary": "historical pattern", "memory_id": "m1"}],
        hard_constraints=["wallet must be 0xabc", "chain must be bsc"],
    )
    serialized = original.to_dict()
    restored = ReflectionContextEnvelope.from_value(serialized)
    restored_dict = restored.to_dict()

    assert restored_dict["context"] == serialized["context"]
    assert restored_dict["hard_constraints"] == serialized["hard_constraints"]
    assert restored_dict["memory_items"] == serialized["memory_items"]
    assert restored_dict["review_hints"] == serialized["review_hints"]
    assert restored.has_context is True
```

**端到端验证**：修复后重跑 style distillation，检查 TS 侧 artifact `*.reflection.request.json` 中 `injected_context` 包含非空 `context`、非空 `hard_constraints`。

---

## 三、改动 2（P1）：修复 `parseStreamingJson` 虚假成功

### 3.1 问题是什么

当前 `parseStreamingJson`（`json-parse.ts` 第 21-26 行）在所有解析手段失败后返回 `{}`。

```typescript
// json-parse.ts 第 21-26 行
try {
    const result = partialParse(partialJson);
    return (result ?? {}) as T;
} catch {
    return {} as T;   // ← 完全不可解析时返回空对象
}
```

`parseJsonObject`（`ot_reflection_mode.ts` 第 118-129 行）在 `JSON.parse` 失败后调用 `parseStreamingJson`，得到 `{}`。`{}` 通过了类型检查（第 126 行：不是 null、是 object、不是 array），被当作有效结果返回。

后续 TS 层以 `ok: true, status: "succeeded"` 上报（第 479-481 行），`normalizedOutput` 为 `{}`。

**影响**：

- 监控/日志显示"成功"，实际是空数据
- Python 侧 `parse_wallet_style_review_report` 会捕获，但错误归因不准确（看不出是解析层失败）
- `rawOutput` 有完整 LLM 文本，`normalizedOutput` 为 `{}`，两者不一致

### 3.2 改什么

#### 文件 A：`vendor/pi_runtime/upstream/ai/src/utils/json-parse.ts`

**3.2.1 — 修改 `parseStreamingJson` 的失败路径（第 10-28 行）**

修改前：

```typescript
export function parseStreamingJson<T = any>(partialJson: string | undefined): T {
	if (!partialJson || partialJson.trim() === "") {
		return {} as T;
	}
	try {
		return JSON.parse(partialJson) as T;
	} catch {
		try {
			const result = partialParse(partialJson);
			return (result ?? {}) as T;
		} catch {
			return {} as T;
		}
	}
}
```

修改后：

```typescript
export function parseStreamingJson<T = any>(partialJson: string | undefined): T {
	if (!partialJson || partialJson.trim() === "") {
		throw new Error("parseStreamingJson: input is empty or undefined");
	}
	try {
		return JSON.parse(partialJson) as T;
	} catch {
		try {
			const result = partialParse(partialJson);
			if (result === null || result === undefined) {
				throw new Error("parseStreamingJson: partial-json returned null/undefined");
			}
			return result as T;
		} catch (innerError) {
			throw new Error(
				`parseStreamingJson: all parsing attempts failed (input length=${partialJson.length}): ${innerError}`,
			);
		}
	}
}
```

#### 文件 B：`vendor/pi_runtime/upstream/coding_agent/src/ot_reflection_mode.ts`

**3.2.2 — 修改 `parseJsonObject` 以区分"部分成功"与"完全失败"（第 118-130 行）**

修改前：

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

修改后：

```typescript
function parseJsonObject(text: string): Record<string, unknown> {
	const normalized = normalizeJsonText(text);
	let parsed: unknown;
	try {
		parsed = JSON.parse(normalized);
	} catch {
		parsed = parseStreamingJson(normalized);  // 现在失败会 throw
	}
	if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
		throw new Error("Reflection output must be a JSON object");
	}
	const obj = parsed as Record<string, unknown>;
	if (Object.keys(obj).length === 0 && normalized.trim().length > 2) {
		throw new Error("Reflection output parsed to empty object from non-empty input");
	}
	return obj;
}
```

### 3.3 风险评估

`parseStreamingJson` 是 vendor 共享函数，可能被其他模块调用。修改前需搜索所有调用方：

```bash
rg "parseStreamingJson" vendor/
```

若有调用方依赖"失败返回 `{}`"的行为，需要在该调用方加 try/catch 兜底。`ot_reflection_mode.ts` 的 `parseJsonObject` 已有 try/catch 包裹，安全。

### 3.4 验证方法

在 TS 侧添加测试（或手动验证）：

```typescript
// 完全不可解析 → 应该 throw
assert.throws(() => parseStreamingJson("this is not json at all"));

// 空输入 → 应该 throw
assert.throws(() => parseStreamingJson(""));

// 有效的截断 JSON → 应该返回部分结果
const result = parseStreamingJson('{"profile":{"wallet":"0xabc"');
assert.deepEqual(result, { profile: { wallet: "0xabc" } });

// 完整 JSON → 正常返回
const full = parseStreamingJson('{"ok":true}');
assert.deepEqual(full, { ok: true });
```

---

## 四、改动 3（P1）：为子进程添加默认超时

### 4.1 问题是什么

三个独立位置的 `subprocess.run` 调用在未配置环境变量时 `timeout=None`（无限等待）：


| 位置            | 文件                              | 行号      | 影响                        |
| ------------- | ------------------------------- | ------- | ------------------------- |
| Runtime 执行    | `runtime/executor.py`           | 57-64   | Pi reflection 子进程可挂死      |
| OnchainOS CLI | `execution/onchainos_cli.py`    | 589-601 | RPC/CLI 子进程可挂死            |
| 蒸馏子进程         | `style_distillation/service.py` | 多处      | primary.py/execute.py 可挂死 |


任意一层卡住 → 整条链路无限阻塞 → 上游队列积压。

### 4.2 改什么

#### 文件 A：`src/ot_skill_enterprise/runtime/executor.py`

**4.2.1 — 修改 `_optional_timeout_seconds()` 添加硬兜底（第 16-26 行）**

修改前：

```python
def _optional_timeout_seconds(request: RuntimeExecutionRequest) -> float | None:
    raw = (
        request.metadata.get("runtime_timeout_seconds")
        or request.launch_spec.metadata.get("timeout_seconds")
        or os.environ.get("OT_RUNTIME_EXEC_TIMEOUT_SECONDS")
    )
    try:
        timeout = float(raw)
    except (TypeError, ValueError):
        return None
    return timeout if timeout > 0 else None
```

修改后：

```python
_DEFAULT_RUNTIME_TIMEOUT_SECONDS = 300.0

def _optional_timeout_seconds(request: RuntimeExecutionRequest) -> float:
    raw = (
        request.metadata.get("runtime_timeout_seconds")
        or request.launch_spec.metadata.get("timeout_seconds")
        or os.environ.get("OT_RUNTIME_EXEC_TIMEOUT_SECONDS")
    )
    try:
        timeout = float(raw)
    except (TypeError, ValueError):
        return _DEFAULT_RUNTIME_TIMEOUT_SECONDS
    return timeout if timeout > 0 else _DEFAULT_RUNTIME_TIMEOUT_SECONDS
```

返回类型从 `float | None` 改为 `float`，下游 `subprocess.run(timeout=...)` 始终有值。

#### 文件 B：`src/ot_skill_enterprise/execution/onchainos_cli.py`

**4.2.2 — 修改 `_run_command()` 添加 timeout 参数（第 589-601 行）**

修改前：

```python
def _run_command(
    command: list[str],
    *,
    env: dict[str, str] | None = None,
    executor: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    completed = executor(
        command,
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )
```

修改后：

```python
_DEFAULT_CLI_TIMEOUT_SECONDS = 120.0

def _run_command(
    command: list[str],
    *,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
    executor: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    effective_timeout = timeout or float(
        os.environ.get("OT_ONCHAINOS_CLI_TIMEOUT_SECONDS") or _DEFAULT_CLI_TIMEOUT_SECONDS
    )
    try:
        completed = executor(
            command,
            text=True,
            capture_output=True,
            check=False,
            env=env,
            timeout=effective_timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "error": f"CLI command timed out after {effective_timeout:.0f}s",
            "stderr": str(exc.stderr or ""),
            "stdout": str(exc.stdout or ""),
            "returncode": -9,
        }
```

**注意**：`_run_command` 的所有调用方（login、quote、simulate、execute 等）需要确认能处理 `"ok": False` 返回。搜索验证：

```bash
rg "_run_command\(" src/ot_skill_enterprise/execution/onchainos_cli.py
```

### 4.3 验证方法

```python
def test_executor_default_timeout():
    request = RuntimeExecutionRequest(...)  # 不设 metadata 中的 timeout
    timeout = _optional_timeout_seconds(request)
    assert timeout == 300.0  # 不再是 None
```

---

## 五、改动 4（P1）：修复 Trade Pairing 无 `token_amount` 时少配

### 5.1 问题是什么

`pair_activities()`（`trade_pairing.py` 第 145-254 行）中：

- **有 `token_amount`** 的 sell：走 `while` 循环（第 180-224 行），可拆分匹配多笔 buy ✅
- **无 `token_amount`** 的 sell：走 `else` 分支（第 225-254 行），只执行一次 `popleft()`，大单卖对应多笔小买时**仅匹配首笔** ❌

**示例**：3 笔 buy 各 $100，1 笔 sell $300 但无 `token_amount`。当前结果：只配第一笔 buy（$100），剩余 2 笔 buy 变成 open position。正确结果：应逐笔扣减所有 3 笔 buy。

**影响**：`matching_coverage`、`win_rate`、持仓分析等风格指标失真。

### 5.2 改什么

#### 文件：`src/ot_skill_enterprise/style_distillation/trade_pairing.py`

**5.2.1 — 将 `else` 分支改为按 USD 逐笔扣减的循环（第 225-254 行）**

修改前：

```python
        else:
            buy_leg = buy_queues[key].popleft()
            buy_amount = _safe_float(buy_leg.get("_remaining_amount_usd")) or _safe_float(buy_leg.get("amount_usd")) or 0.0
            sell_amount = amount_usd
            buy_ts = _parse_timestamp(buy_leg.get("timestamp")) or current_time
            sell_ts = _parse_timestamp(item.get("timestamp")) or current_time
            pnl_usd = sell_amount - buy_amount
            pnl_pct = (pnl_usd / buy_amount) * 100.0 if buy_amount > 0 else 0.0
            completed.append(
                CompletedTrade(
                    # ... 单笔匹配 ...
                )
            )
```

修改后：

```python
        else:
            sell_remaining_usd = amount_usd
            while sell_remaining_usd > 1e-8 and buy_queues[key]:
                buy_leg = buy_queues[key][0]
                buy_remaining_usd = _safe_float(buy_leg.get("_remaining_amount_usd")) or _safe_float(buy_leg.get("amount_usd")) or 0.0
                if buy_remaining_usd <= 1e-8:
                    buy_queues[key].popleft()
                    continue
                matched_usd = min(buy_remaining_usd, sell_remaining_usd)
                buy_fraction = matched_usd / buy_remaining_usd if buy_remaining_usd > 0 else 0.0
                sell_fraction = matched_usd / sell_remaining_usd if sell_remaining_usd > 0 else 0.0
                buy_ts = _parse_timestamp(buy_leg.get("timestamp")) or current_time
                sell_ts = _parse_timestamp(item.get("timestamp")) or current_time
                pnl_usd = matched_usd * sell_fraction - matched_usd
                pnl_pct = ((matched_usd * (sell_fraction / buy_fraction) - matched_usd) / matched_usd) * 100.0 if matched_usd > 0 else 0.0
                token_ref = dict(item.get("token_ref") or {})
                completed.append(
                    CompletedTrade(
                        token_symbol=str(token_ref.get("symbol") or buy_leg.get("token_ref", {}).get("symbol") or "").strip(),
                        token_address=_safe_text(token_ref.get("token_address") or buy_leg.get("token_ref", {}).get("token_address")),
                        token_identifier=_safe_text(token_ref.get("identifier") or buy_leg.get("token_ref", {}).get("identifier")),
                        buy_timestamp=buy_ts.isoformat(),
                        sell_timestamp=sell_ts.isoformat(),
                        buy_amount_usd=matched_usd,
                        sell_amount_usd=amount_usd * sell_fraction,
                        holding_seconds=max(0, int((sell_ts - buy_ts).total_seconds())),
                        pnl_usd=amount_usd * sell_fraction - matched_usd,
                        pnl_pct=((amount_usd * sell_fraction - matched_usd) / matched_usd) * 100.0 if matched_usd > 0 else 0.0,
                        is_profitable=(amount_usd * sell_fraction - matched_usd) > 0,
                        buy_tx_hash=_safe_text(buy_leg.get("tx_hash")),
                        sell_tx_hash=_safe_text(item.get("tx_hash")),
                        metadata={
                            "buy_note": buy_leg.get("note"),
                            "sell_note": item.get("note"),
                            "quote_symbol": item.get("quote_symbol") or buy_leg.get("quote_symbol"),
                            "usd_only_matching": True,
                        },
                    )
                )
                buy_leg["_remaining_amount_usd"] = max(0.0, buy_remaining_usd - matched_usd)
                sell_remaining_usd = max(0.0, sell_remaining_usd - matched_usd)
                if (_safe_float(buy_leg.get("_remaining_amount_usd")) or 0.0) <= 1e-8:
                    buy_queues[key].popleft()
```

**注意**：PnL 计算逻辑需要仔细对齐。核心思路：按 sell_remaining_usd 逐笔扣减 buy 队列中每笔的 `_remaining_amount_usd`，直到 sell 金额耗尽或 buy 队列为空。

### 5.3 验证方法

```python
def test_sell_without_token_amount_matches_multiple_buys():
    activities = [
        {"action": "buy", "amount_usd": 100, "token_ref": {"symbol": "TOKEN"}, "timestamp": "2025-01-01T00:00:00Z"},
        {"action": "buy", "amount_usd": 100, "token_ref": {"symbol": "TOKEN"}, "timestamp": "2025-01-01T01:00:00Z"},
        {"action": "buy", "amount_usd": 100, "token_ref": {"symbol": "TOKEN"}, "timestamp": "2025-01-01T02:00:00Z"},
        {"action": "sell", "amount_usd": 300, "token_ref": {"symbol": "TOKEN"}, "timestamp": "2025-01-02T00:00:00Z"},
    ]
    completed, open_positions, _ = pair_activities(activities)
    assert len(completed) == 3  # 之前只有 1
    assert len(open_positions) == 0  # 之前有 2 个 open position
    total_buy = sum(t.buy_amount_usd for t in completed)
    assert abs(total_buy - 300.0) < 0.01
```

---

## 六、改动 5（P2）：coordinator 异常路径 try/finally 收口

### 6.1 问题是什么

`RuntimeRunCoordinator.run()`（`coordinator.py` 第 104-210 行）中，`executor.execute()`（第 138 行）或 `translator.apply()`（第 139-144 行）抛异常时，`finish_invocation`、`close_session`、`record_session` 不会执行。

### 6.2 改什么

#### 文件：`src/ot_skill_enterprise/runtime/coordinator.py`

**6.2.1 — 在 `run()` 方法中添加 try/finally（第 125-210 行）**

在 `request = RuntimeExecutionRequest(...)` 之后，将执行+收口逻辑包在 try/finally 中：

修改前（简化结构）：

```python
    request = RuntimeExecutionRequest(...)
    execution = self.executor.execute(request)
    translation = self.translator.apply(...)
    invocation = adapter.finish_invocation(...)
    session = adapter.close_session(...)
    self.session_store.record_session(session)
    # ... pipeline + return
```

修改后：

```python
    request = RuntimeExecutionRequest(...)
    execution: RuntimeExecutionResult | None = None
    try:
        execution = self.executor.execute(request)
        translation = self.translator.apply(
            adapter,
            transcript=execution.transcript,
            session_id=session.session_id,
            invocation_id=invocation.invocation_id,
        )
    except Exception:
        adapter.finish_invocation(
            session.session_id,
            invocation.invocation_id,
            status="failed",
            summary="runtime execution raised unhandled exception",
            output_payload={},
            metadata={},
        )
        adapter.close_session(session.session_id, status="failed", metadata={})
        self.session_store.record_session(
            adapter.snapshot_session(session.session_id) if hasattr(adapter, "snapshot_session") else session
        )
        raise
    invocation = adapter.finish_invocation(...)
    session = adapter.close_session(...)
    self.session_store.record_session(session)
    # ... pipeline + return（保持不变）
```

### 6.3 验证方法

mock `executor.execute` 使其抛异常，验证 `finish_invocation` 和 `close_session` 仍被调用。

---

## 七、改动 6（P3 — 技术债，非本轮必须）：`prepare_only` 模式语义修正

### 7.1 说明

无 OKX 凭证时走 `prepare_only` 路径（`service.py` 第 3646-3657 行），`collect_execution_result` 以 `mode='dry_run'` 聚合，可能将 `execution_readiness` 标为 `dry_run_ready`。

**生产环境不受影响**：每个使用者必须自行配置 OKX 凭证，生产路径始终走真实 dry run，不会进入 `prepare_only` 分支。此问题仅在本地开发或 CI 无凭证环境中产生语义偏差。

**暂缓处理**，后续有需要时在 `collect_execution_result` 中为 `prepare_only` 模式添加 `execution_readiness=not_verified` 标记即可。

---

## 八、改动 7（P2）：`StyleDistillationSummary.to_dict` 序列化 `created_at`

### 8.1 问题是什么

`to_dict()` 返回的 dict 中 `created_at` 保留为 `datetime` 对象（`models.py` 第 192 行），直接 `json.dumps` 会抛 `TypeError`。

### 8.2 改什么

#### 文件：`src/ot_skill_enterprise/style_distillation/models.py`

**8.2.1 — 修改 `to_dict()` 中 `created_at` 行（第 192 行）**

修改前：

```python
            "created_at": self.created_at,
```

修改后：

```python
            "created_at": self.created_at.isoformat() if isinstance(self.created_at, datetime) else str(self.created_at),
```

### 8.3 验证方法

```python
def test_summary_to_dict_is_json_serializable():
    summary = StyleDistillationSummary(...)
    payload = summary.to_dict()
    json.dumps(payload)  # 不应抛异常
```

---

## 九、改动 8（P2）：executor 成功路径 stdout 防护

### 9.1 问题是什么

`executor.py` 第 147 行 `json.loads(completed.stdout)` 在子进程 returncode=0 但 stdout 为空或非 JSON 时抛 `JSONDecodeError`。该异常未被捕获，会导致 coordinator 异常退出（与改动 5 相关）。

### 9.2 改什么

#### 文件：`src/ot_skill_enterprise/runtime/executor.py`

**9.2.1 — 在第 147 行添加 try/except（第 147-155 行）**

修改前：

```python
        payload = json.loads(completed.stdout)
        transcript = RuntimeTranscript.from_payload(...)
```

修改后：

```python
        try:
            payload = json.loads(completed.stdout)
        except (json.JSONDecodeError, ValueError) as exc:
            transcript = RuntimeTranscript(
                runtime_id=request.runtime_id,
                session_id=request.session_id,
                invocation_id=request.invocation_id,
                ok=False,
                status="failed",
                summary=f"runtime process returned exit 0 but stdout is not valid JSON: {exc}",
                output_payload={"stdout": completed.stdout[:2000], "stderr": completed.stderr.strip()},
                events=[],
                metadata={"returncode": 0},
                stdout=completed.stdout,
                stderr=completed.stderr,
            )
            return RuntimeExecutionResult(
                runtime_id=request.runtime_id,
                session_id=request.session_id,
                invocation_id=request.invocation_id,
                launch_spec=launch_spec,
                command=command,
                returncode=0,
                transcript=transcript,
                started_at=started_at,
                finished_at=finished_at,
                error=ServiceError(
                    code="runtime_stdout_parse_failed",
                    message=transcript.summary,
                    details={"returncode": 0},
                ),
            )
        transcript = RuntimeTranscript.from_payload(...)
```

---

## 十、改动顺序与依赖关系

```
改动 1（P0 上下文断裂）  ← 独立，最高优先
      ↓
改动 2（P1 虚假成功）    ← 独立
改动 3（P1 子进程超时）  ← 独立
改动 4（P1 Trade pairing）← 独立

改动 5（P2 coordinator 收口）+ 改动 8（P2 stdout 防护）← 同一文件域，一起改
      ↓
改动 6（P2 prepare_only）← 依赖对执行流的理解
改动 7（P2 datetime）    ← 独立，最简单
```

建议顺序：**1 → 2 → 3 → 4 → 5+8 → 7**

改动 1-4 为 P0/P1，应在本轮全部完成。改动 5、7、8 为 P2，可进入下轮迭代。改动 6 降为 P3 技术债（生产环境必配 OKX 凭证，不受影响），暂缓处理。

---

## 十一、验证清单


| 改动  | 验证手段                            | 通过标准                                                 |
| --- | ------------------------------- | ---------------------------------------------------- |
| 1   | round-trip 单测 + artifact 检查     | `injected_context` 中 `context`/`hard_constraints` 非空 |
| 2   | 解析不可恢复 JSON 时应 throw            | 监控中不再出现 `ok:true` + 空 normalized                     |
| 3   | 无环境变量时默认超时生效                    | `_optional_timeout_seconds` 返回 300.0                 |
| 4   | 多笔 buy 对单笔 sell 测试              | `len(completed)` 与预期匹配                               |
| 5   | mock execute 抛异常                | `finish_invocation` 被调用                              |
| 6   | 无凭证环境 distill                   | `execution_readiness` 为 `not_verified`               |
| 7   | `json.dumps(summary.to_dict())` | 不抛异常                                                 |
| 8   | 子进程 stdout 为空但 exit 0           | 返回 `ok=False` 而非崩溃                                   |


回归测试：

```bash
python -m pytest tests/test_wallet_style_reflection.py -v
python -m pytest tests/test_wallet_style_context_layering.py -v
```

---

## 十二、回滚方案

每个改动独立可 revert。

- 改动 1 回滚：恢复 `ReflectionContextEnvelope` 原始 4 字段 → 回到"上下文丢失"状态但不影响运行
- 改动 2 回滚：恢复 `parseStreamingJson` 返回 `{}` → 回到虚假成功
- 改动 3 回滚：恢复 `_optional_timeout_seconds` 返回 `None` → 回到无默认超时
- 改动 4 回滚：恢复 `else` 分支单次 `popleft()` → 回到少配
- 改动 5-8 回滚：各自恢复即可

环境变量灰度（可选）：

```bash
OT_RUNTIME_EXEC_TIMEOUT_SECONDS=0       # 设为 0 关闭默认超时
OT_ONCHAINOS_CLI_TIMEOUT_SECONDS=0      # 设为 0 关闭 CLI 超时
```

