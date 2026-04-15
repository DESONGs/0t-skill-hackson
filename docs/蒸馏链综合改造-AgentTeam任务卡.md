# 蒸馏链综合改造 AgentTeam 任务卡

## 1. 文档目标

本文件把以下四份方案收口成一套可以直接派发给 `AgentTeam` 的任务卡：

- `蒸馏链综合改造-实施总路线.md`
- `蒸馏链综合改造-耦合与验收方案.md`
- `蒸馏链综合改造-Archetype模块落地方案.md`
- `蒸馏链综合改造-Reflection模块落地方案.md`

目标不是重复方案，而是补齐三件事：

1. 具体任务卡
2. 子代理写入边界
3. 不同环境下的异步执行顺序与阻塞条件

## 2. AgentTeam 执行原则

### 2.1 总原则

- 先稳 `Reflection runtime`，再补 `Archetype` 中间层，最后升级 `Reflection schema/prompt`。
- `Archetype` 和 `Reflection` 可以并行开工，但只能并行推进不互相阻塞的部分。
- `style_distillation/service.py` 是全链路热文件，只允许 `MainAgent / Integrator` 最终收口。
- 任何涉及以下四个耦合点的变更，都必须走联合评审：
  - `trade_pairing/statistics` 输出字段
  - `compact_input.derived_stats`
  - reflection output schema
  - `review.status / strategy_quality`

### 2.2 冻结接口

正式并行前先冻结以下接口，不冻结就不要开多个子代理：

- `failure taxonomy`
  - `runtime_abort`
  - `runtime_timeout`
  - `provider_unavailable`
  - `empty_output`
  - `json_parse_failed`
  - `schema_rejected`
  - `generic_rejected`
- `review.status`
  - `strategy_ready`
  - `generate`
  - `generate_with_low_confidence`
  - `insufficient_signal`
  - `no_pattern_detected`
  - `runtime_failed`
  - `needs_manual_review`
- `derived_stats` archetype 字段
  - `primary_archetype`
  - `secondary_archetypes`
  - `behavioral_patterns`
  - `archetype_confidence`
  - `archetype_evidence_summary`
- memory / QA / compiler 对这些状态和字段的解释口径

## 3. 子代理与环境拆分

| 子代理 | 主要职责 | 推荐环境 | 允许写入 | 禁止写入 | 启动条件 |
| --- | --- | --- | --- | --- | --- |
| `MainAgent / Integrator` | 接口冻结、热文件收口、全链路联调 | 全量集成环境 | `style_distillation/service.py`、`docs/`、必要的 glue code | 大面积重写 `vendor/pi_runtime`、`trade_pairing.py`、`skills_compiler/*` | 立即启动 |
| `C-REF-TS` | R1/R2 的 TS runtime 稳定性 | `vendor/pi_runtime` 可构建环境 | `vendor/pi_runtime/upstream/coding_agent/src/ot_reflection_mode.ts`、`ot_runtime_entry.ts` | `style_distillation/service.py` | `failure taxonomy` 冻结 |
| `C-REF-PY` | R1/R3/R4 的 Python reflection 契约 | Python + `.venv` 环境 | `reflection/service.py`、`reflection/models.py` | `trade_pairing.py`、`skills_compiler/*` | `failure taxonomy` 冻结 |
| `C-ARC-CORE` | A1/A2/A3 的特征与 archetype 分类器 | Python + AVE mock 环境 | `style_distillation/trade_pairing.py`、新 `style_distillation/archetype.py`、`style_distillation/models.py` | `vendor/pi_runtime` | `derived_stats` 字段草案冻结 |
| `C-ARC-FALLBACK` | A4/A5 的 compact/fallback 接入 | Python + `.venv` 环境 | `style_distillation/extractors.py`，以及对 `service.py` 的改动提案 | 直接提交 `service.py` 主干改动 | A3 完成 |
| `C-COMP` | A6 与编译产物透传 | Python 编译环境 | `skills_compiler/compiler.py`、`skills_compiler/wallet_style_runtime.py` | `reflection/*`、`vendor/pi_runtime` | archetype 输出字段冻结 |
| `C-MEM-QA` | R5 的 memory / QA 收口 | Python + 可选 PG/Redis 环境 | `qa/*`、`tests/*`、`style_distillation/context.py` | `vendor/pi_runtime` | `review.status` 冻结 |
| `D-VERIFY` | 验收样本、回归脚本、门禁验证 | 全量验证环境 | `tests/*`、`scripts/verify.sh` | 业务主逻辑文件 | 至少有一个联调分支可运行 |

### 3.1 环境说明

#### Env-A: Archetype Core

- 根目录：`0t-skill_hackson_v2ing/`
- 依赖：`.venv`
- 主要验证：
  - `python -m py_compile`
  - `python -m unittest`
  - AVE mock 数据下 `style distill`

#### Env-B: Reflection TS Runtime

- 根目录：`0t-skill_hackson_v2ing/vendor/pi_runtime/`
- 依赖：`node_modules`
- 主要验证：
  - `npm run build:ot-runtime`
  - `node --check dist/pi-runtime.mjs`

#### Env-C: Reflection Python / Memory / QA

- 根目录：`0t-skill_hackson_v2ing/`
- 依赖：`.venv`
- 可选依赖：
  - `OT_DB_DSN`
  - `OT_REDIS_URL`
- 主要验证：
  - `python -m unittest discover -s tests -p 'test_*.py'`
  - 局部 smoke

#### Env-D: Compiler / Runtime Packaging

- 根目录：`0t-skill_hackson_v2ing/`
- 依赖：`.venv`
- 主要验证：
  - `candidate compile`
  - `candidate validate`
  - `candidate promote`

#### Env-I: Integrator

- 根目录：`0t-skill_hackson_v2ing/`
- 依赖：`.venv` + 可选 `vendor/pi_runtime/node_modules`
- 主要验证：
  - `scripts/verify.sh`
  - `style distill` 端到端 smoke

## 4. 异步执行波次

## Wave 0：接口冻结

- `MainAgent` 输出冻结文档
- 冻结 `failure taxonomy`
- 冻结 `review.status`
- 冻结 archetype 字段名

输出物：

- 本文档生效
- 联评清单生效

## Wave 1：并行起步

可并行启动：

- `C-REF-TS` 执行 `R1 + R2`
- `C-REF-PY` 执行 `R3` 的状态机骨架
- `C-ARC-CORE` 执行 `A1 + A2`

此波次禁止：

- 改 `Reflection schema/prompt`
- 改 compiler 映射
- 大改 `style_distillation/service.py`

## Wave 2：Archetype 分类层成形

启动条件：

- A1/A2 完成，底层特征稳定

执行内容：

- `C-ARC-CORE` 执行 `A3`
- `C-ARC-FALLBACK` 准备 `A4/A5` 的接入方案
- `C-REF-PY` 继续补 `R3`

## Wave 3：内容契约升级

启动条件：

- A3 完成
- `primary_archetype` 等字段冻结

执行内容：

- `C-ARC-FALLBACK` 执行 `A4 + A5`
- `C-REF-PY` 执行 `R4`
- `C-COMP` 执行 `A6`

## Wave 4：收口与验收

启动条件：

- `review.status`
- `strategy_quality`
- `archetype fields`
- `compiler field mapping`

执行内容：

- `C-MEM-QA` 执行 `R5`
- `D-VERIFY` 补回归样本和验收脚本
- `MainAgent` 集成 `style_distillation/service.py`

## 5. 任务卡总表

| 卡号 | 轨道 | Owner | 环境 | 依赖 | 关键输出 |
| --- | --- | --- | --- | --- | --- |
| `C0` | Contract | `MainAgent` | Env-I | 无 | 状态词表、字段名、联合评审口径 |
| `R1` | Reflection | `C-REF-TS` + `C-REF-PY` | Env-B / Env-C | `C0` | failure taxonomy + artifact 扩展 |
| `R2` | Reflection | `C-REF-TS` | Env-B | `R1` | timeout / model / provider fallback |
| `R3` | Reflection | `C-REF-PY` | Env-C | `R1` | validator 分级接受骨架 |
| `R4` | Reflection | `C-REF-PY` | Env-C | `A4` | archetype 驱动 schema/prompt |
| `R5` | Reflection | `C-MEM-QA` | Env-C | `R3` + `R4` | memory / QA / quality gate 收口 |
| `A1` | Archetype | `C-ARC-CORE` | Env-A | `C0` | 交易级字段扩展 |
| `A2` | Archetype | `C-ARC-CORE` | Env-A | `A1` | 统计级字段扩展 |
| `A3` | Archetype | `C-ARC-CORE` | Env-A | `A2` | `archetype.py` 分类器 |
| `A4` | Archetype | `MainAgent` + `C-ARC-FALLBACK` | Env-I / Env-A | `A3` | service 注入 archetype |
| `A5` | Archetype | `C-ARC-FALLBACK` | Env-A | `A4` | extractor 消费 archetype |
| `A6` | Downstream | `C-COMP` | Env-D | `A4` + `A5` | compiler/runtime 字段透传 |

## 6. 任务卡详情

## C0：接口冻结与联合评审清单

- 目标：在并行开发前冻结状态词表、Archetype 字段、失败分类和联评边界。
- 主要文件：
  - `docs/`
  - 必要时 `style_distillation/models.py`
  - 必要时 `reflection/models.py`
- 输入：
  - 四份方案文档
  - 当前代码中的 `review.status`、`strategy_quality`、`fallback_used`
- 输出：
  - 冻结字段清单
  - 联合评审清单
  - 子代理写入边界
- 阻塞条件：无
- 并行关系：后续所有卡都依赖它
- 验收标准：
  - 所有子代理用同一套状态词表
  - `style_distillation/service.py` 热文件归属明确

## R1：Reflection 失败分类与 artifact 扩展

- 目标：把 runtime failure 从粗粒度错误改成可追踪失败分类。
- Owner：`C-REF-TS` 主做，`C-REF-PY` 对齐 Python 侧消费语义。
- 主要文件：
  - `src/ot_skill_enterprise/reflection/service.py`
  - `src/ot_skill_enterprise/runtime/executor.py`
  - `vendor/pi_runtime/upstream/coding_agent/src/ot_reflection_mode.ts`
  - `vendor/pi_runtime/upstream/coding_agent/src/ot_runtime_entry.ts`
- 输入：
  - 当前 runtime transcript
  - 当前 raw/normalized artifact 结构
- 输出：
  - 失败分类落盘
  - raw_text 保留
  - provider/model/request 信息保留
- 阻塞条件：`C0`
- 可并行：`A1`、`A2`
- 验收标准：
  - `runtime_abort`、`runtime_timeout`、`json_parse_failed` 可区分
  - artifact 不再只剩一句 `Request was aborted`
- 验收命令：
  - `find src/ot_skill_enterprise -type f -name '*.py' -print0 | xargs -0 .venv/bin/python -m py_compile`
  - `(cd vendor/pi_runtime && npm run build:ot-runtime && node --check dist/pi-runtime.mjs)`

## R2：timeout / model / provider fallback

- 目标：从单模型单超时升级成分层超时和主备回退链。
- Owner：`C-REF-TS`
- 主要文件：
  - `src/ot_skill_enterprise/runtime/executor.py`
  - `src/ot_skill_enterprise/reflection/service.py`
  - `vendor/pi_runtime/upstream/coding_agent/src/ot_reflection_mode.ts`
- 输入：
  - `R1` 的 failure taxonomy
- 输出：
  - transport timeout
  - request timeout
  - overall reflection timeout
  - model/provider fallback 策略
- 阻塞条件：`R1`
- 可并行：`A1`、`A2`、`A3`
- 验收标准：
  - 主模型失败可切备用模型
  - provider 不可用可回退
  - timeout 参数口径统一
- 验收命令：
  - `(cd vendor/pi_runtime && npm run build:ot-runtime && node --check dist/pi-runtime.mjs)`

## R3：validator 分级接受骨架

- 目标：区分“系统失败”和“信号不足”，不再让 generic 结果一票否决。
- Owner：`C-REF-PY`
- 主要文件：
  - `src/ot_skill_enterprise/reflection/service.py`
  - `src/ot_skill_enterprise/style_distillation/service.py`
  - 必要时 `src/ot_skill_enterprise/style_distillation/models.py`
- 输入：
  - `R1` 的失败语义
  - `C0` 的状态词表
- 输出：
  - 分级接受 validator
  - `review.status` 状态分流
  - generic gate 降级策略
- 阻塞条件：`R1`
- 可并行：`A3`
- 验收标准：
  - `insufficient_signal` 不再算 runtime failure
  - `generic label / summary` 进入 warning 或 low confidence，而不是直接 fallback
- 验收命令：
  - `.venv/bin/python -m unittest discover -s tests -p 'test_*.py'`

## R4：Reflection schema/prompt 对接 Archetype

- 目标：让 Reflection 不再自己发明 taxonomy，而是直接消费 archetype 证据。
- Owner：`C-REF-PY`
- 主要文件：
  - `src/ot_skill_enterprise/reflection/service.py`
  - `src/ot_skill_enterprise/style_distillation/service.py`
  - 必要时 `src/ot_skill_enterprise/style_distillation/context.py`
  - 必要时 `src/ot_skill_enterprise/qa/*`
- 输入：
  - `A4` 交付的 archetype 字段
- 输出：
  - 升级后的 output schema
  - 升级后的 extraction prompt
  - archetype 驱动的 review reasoning
- 阻塞条件：
  - `A4`
  - `derived_stats.primary_archetype`
  - `behavioral_patterns`
  - `archetype_evidence_summary`
- 可并行：`A6`
- 验收标准：
  - 高信号地址输出明确 archetype
  - 低信号地址允许 `no_pattern_detected` / `insufficient_signal`
- 验收命令：
  - `.venv/bin/python -m unittest discover -s tests -p 'test_*.py'`
  - `AVE_DATA_PROVIDER=mock OT_PI_REFLECTION_MOCK=1 PYTHONPATH="${PYTHONPATH:-$PWD/src}" .venv/bin/python -m ot_skill_enterprise.control_plane.cli style distill --workspace-dir .ot-workspace --wallet 0xverifywallet0001 --chain solana`

## R5：memory / QA / quality gate 收口

- 目标：把新状态机接到记忆层、QA 报告和下游质量门禁。
- Owner：`C-MEM-QA`
- 主要文件：
  - `src/ot_skill_enterprise/style_distillation/context.py`
  - `src/ot_skill_enterprise/qa/*`
  - `tests/*`
  - 由 `MainAgent` 集成进 `style_distillation/service.py`
- 输入：
  - `R3` 的状态机
  - `R4` 的 schema 语义
- 输出：
  - memory 过滤规则
  - QA 状态映射
  - 新验收样本
- 阻塞条件：`R3` + `R4`
- 可并行：无
- 验收标准：
  - `strategy_ready` 正常记忆
  - `insufficient_signal` 低权重记忆
  - `runtime_failed` 不记忆
- 验收命令：
  - `.venv/bin/python -m unittest discover -s tests -p 'test_*.py'`
  - `OT_DB_DSN=... OT_REDIS_URL=... ./scripts/verify.sh`

## A1：交易级字段扩展

- 目标：为 archetype classifier 提供足够的交易级信号。
- Owner：`C-ARC-CORE`
- 主要文件：
  - `src/ot_skill_enterprise/style_distillation/trade_pairing.py`
  - 必要时 `src/ot_skill_enterprise/style_distillation/service.py` 的输入组装提案
- 输入：
  - 当前 FIFO 配对逻辑
  - 当前 market/token 口径
- 输出字段建议：
  - `buy_mcap_usd`
  - `buy_amount_vs_avg_ratio`
  - `is_first_buy_for_token`
  - `was_in_profit_when_added`
  - `buy_price_usd`
  - `sell_price_usd`
- 阻塞条件：`C0`
- 可并行：`R1`、`R2`
- 验收标准：
  - `trade_pairing` artifact 可见新增字段
  - 配对覆盖率不明显退化
- 验收命令：
  - `.venv/bin/python -m unittest discover -s tests -p 'test_*.py'`

## A2：统计级字段扩展

- 目标：把统计结果升级成 archetype 判定输入，而不是只做展示。
- Owner：`C-ARC-CORE`
- 主要文件：
  - `src/ot_skill_enterprise/style_distillation/trade_pairing.py`
  - 必要时 `src/ot_skill_enterprise/style_distillation/models.py`
- 输入：
  - `A1` 的交易级字段
- 输出字段建议：
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
- 阻塞条件：`A1`
- 可并行：`R1`、`R2`
- 验收标准：
  - `TradeStatistics` 与 `derived_stats` 同步出现新增字段
- 验收命令：
  - `.venv/bin/python -m unittest discover -s tests -p 'test_*.py'`

## A3：新增 `archetype.py`

- 目标：新增中间语义层，而不是继续靠 LLM 或阈值拼接风格标签。
- Owner：`C-ARC-CORE`
- 主要文件：
  - 新 `src/ot_skill_enterprise/style_distillation/archetype.py`
- 输入：
  - `A2` 的统计字段
- 输出：
  - `derive_behavioral_patterns(...)`
  - `score_archetypes(...)`
  - `select_primary_and_secondary(...)`
  - `no_stable_archetype`
- 阻塞条件：`A2`
- 可并行：`R3`
- 验收标准：
  - 至少输出：
    - `primary_label`
    - `secondary_labels`
    - `behavioral_patterns`
    - `confidence`
    - `evidence`
- 验收命令：
  - `.venv/bin/python -m unittest discover -s tests -p 'test_*.py'`

## A4：service 注入 archetype

- 目标：把 archetype 结果写入 `preprocessed`、`derived_stats` 和 `trade_pairing artifact`。
- Owner：`MainAgent` 收口，`C-ARC-FALLBACK` 提供 patch。
- 主要文件：
  - `src/ot_skill_enterprise/style_distillation/service.py`
- 输入：
  - `A3` 的输出结构
- 输出：
  - `preprocessed.archetype`
  - `preprocessed.behavioral_patterns`
  - `derived_stats.primary_archetype`
  - `derived_stats.secondary_archetypes`
  - `derived_stats.behavioral_patterns`
  - `derived_stats.archetype_confidence`
  - `derived_stats.archetype_evidence_summary`
- 阻塞条件：`A3`
- 可并行：`R3`
- 验收标准：
  - `compact_input` 可以直接看到 archetype 摘要
  - Reflection/fallback 都能消费这些字段
- 验收命令：
  - `AVE_DATA_PROVIDER=mock OT_PI_REFLECTION_MOCK=1 PYTHONPATH="${PYTHONPATH:-$PWD/src}" .venv/bin/python -m ot_skill_enterprise.control_plane.cli style distill --workspace-dir .ot-workspace --wallet 0xverifywallet0001 --chain solana`

## A5：extractor 消费 archetype

- 目标：fallback extractor 不再“创造风格”，而是消费 archetype。
- Owner：`C-ARC-FALLBACK`
- 主要文件：
  - `src/ot_skill_enterprise/style_distillation/extractors.py`
  - 必要时 `src/ot_skill_enterprise/style_distillation/models.py`
- 输入：
  - `A4` 注入后的 `preprocessed`
- 输出：
  - archetype 驱动的 `style_label`
  - archetype 驱动的 `review.reasoning`
- 阻塞条件：`A4`
- 可并行：`R4`
- 验收标准：
  - `style_label` 不再主要是 `risk_appetite + execution_tempo`
  - 低信号地址允许 `no_stable_archetype`
- 验收命令：
  - `.venv/bin/python -m unittest discover -s tests -p 'test_*.py'`

## A6：compiler/runtime 字段透传

- 目标：让 skill 包、运行时 payload 和产物引用认识 archetype 字段。
- Owner：`C-COMP`
- 主要文件：
  - `src/ot_skill_enterprise/skills_compiler/compiler.py`
  - `src/ot_skill_enterprise/skills_compiler/wallet_style_runtime.py`
- 输入：
  - `A4` + `A5` 的 archetype 输出
- 输出：
  - `references/style_profile.json`
  - `strategy_spec.json`
  - runtime payload
  - 编译产物中的 archetype 文案/字段
- 阻塞条件：`A4` + `A5`
- 可并行：`R4`
- 验收标准：
  - candidate compile/validate/promote 继续通过
  - runtime payload 可读取 archetype 字段
- 验收命令：
  - `PYTHONPATH="${PYTHONPATH:-$PWD/src}" .venv/bin/python -m ot_skill_enterprise.control_plane.cli candidate compile --workspace-dir .ot-workspace --payload-file <payload.json>`
  - `PYTHONPATH="${PYTHONPATH:-$PWD/src}" .venv/bin/python -m ot_skill_enterprise.control_plane.cli candidate validate --workspace-dir .ot-workspace --candidate-id <candidate_id>`

## 7. 联调与验收

### 7.1 回归样本

至少维持四类地址样本：

- 高频 meme scalper
- microcap day trader
- hold / diamond hands
- 低信号 / 无稳定模式地址

每组都要保留以下产物：

- reflection 原始结果
- salvage 后结果
- fallback 结果
- final skill 输出
- QA 报告

### 7.2 联调检查点

#### Checkpoint 1：distill 输出契约

- `trade_pairing`
- `trade_statistics`
- `derived_stats`
- `context_sources`
- `compact_input`

#### Checkpoint 2：reflection 三条路径

- 正常 structured output
- salvage from raw_text
- fallback extractor

#### Checkpoint 3：build/qa 收口

- `profile`
- `strategy`
- `execution_intent`
- `review`
- `backtest`
- `qa`

#### Checkpoint 4：compiler/runtime 兼容

- `SKILL.md`
- `manifest.json`
- `actions.yaml`
- `agents/interface.yaml`

### 7.3 最终门禁

在 `Integrator` 分支上至少跑一次：

```bash
cd ./0t-skill_hackson_v2ing
./scripts/verify.sh
```

如果 `vendor/pi_runtime/node_modules` 缺失，需要补一次：

```bash
cd ./0t-skill_hackson_v2ing/vendor/pi_runtime
npm install
npm run build:ot-runtime
```

## 8. 推荐启动顺序

1. `MainAgent` 先冻结 `C0`
2. 并行启动 `R1/R2` 与 `A1/A2`
3. `A3` 完成后，启动 `A4/A5`
4. `R3` 在 `R1` 后尽快落地
5. `R4` 等 `A4` 后接入
6. `A6` 与 `R4` 并行
7. `R5`、`D-VERIFY`、`MainAgent` 最后统一收口

## 9. 明确禁止

- 禁止多个子代理同时改 `style_distillation/service.py`
- 禁止在 `A4` 前启动 `R4`
- 禁止先放松 generic gate、后补 archetype taxonomy
- 禁止 compiler 先写死新字段，再倒逼上游接口
- 禁止把 `insufficient_signal` 继续当 runtime failure 处理
