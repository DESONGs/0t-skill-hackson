# Phase 2 剩余修复（2 项）

> 前置：P0 上下文序列化、P3 coordinator/executor 已通过 review  
> 本文档仅涉及 2 个文件、共 6 行代码改动

---

## 修复 1：Trade Pairing PnL 计算（4 行）

**文件**：`src/ot_skill_enterprise/style_distillation/trade_pairing.py`

**问题**：`else` 分支（无 `token_amount` 的 sell）循环结构正确，但 PnL 计算用了 `amount_usd`（原始总额）乘以 `sell_fraction`（相对剩余额的比例），基数不匹配。后续迭代 sell_amount 被放大，3 笔 $100 buy 配 $300 sell 会算出总 sell = $550。

**改法**：第 245、247、248、249 行，把 `amount_usd` 替换为 `sell_remaining_usd`（共 4 处）。

```python
# 第 245 行
sell_amount_usd=sell_remaining_usd * sell_fraction,

# 第 247 行
pnl_usd=sell_remaining_usd * sell_fraction - matched_usd,

# 第 248 行
pnl_pct=(sell_remaining_usd * sell_fraction - matched_usd) / matched_usd * 100.0 if matched_usd > 0 else 0.0,

# 第 249 行
is_profitable=(sell_remaining_usd * sell_fraction - matched_usd) > 0,
```

**验证**：`sell_remaining_usd * sell_fraction` 数学上等于 `matched_usd`，USD-only 匹配下 PnL 恒为 0（无 token 数量无法判定盈亏，符合预期）。

---

## 修复 2：f-string 兼容性（2 行）

**文件**：`src/ot_skill_enterprise/skills_compiler/models.py`

**问题**：第 89-95 行在 f-string 内用了同类引号的 dict 字面量，Python 3.12+ 才合法（PEP 701），3.11 编译报错，阻断全部测试。

**改法**：把 dict 提到 f-string 外面。

```python
# 第 89-95 行，替换为：
_hash_input = {
    "target_skill_name": target_skill_name,
    "candidate_type": candidate_type,
    "change_summary": change_summary,
    "source_run_id": source_run_id,
    "source_evaluation_id": source_evaluation_id,
}
candidate_id = f"candidate-{_short_hash(_hash_input)}"
```

---

## 改完后验证

```bash
cd 0t-skill_hackson_v2ing

python3 -c "import py_compile; py_compile.compile('src/ot_skill_enterprise/skills_compiler/models.py', doraise=True)"

python3 -m pytest tests/test_wallet_style_reflection.py -v
python3 -m pytest tests/test_wallet_style_context_layering.py -v
```
