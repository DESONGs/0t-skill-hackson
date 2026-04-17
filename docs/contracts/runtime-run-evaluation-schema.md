# Runtime Run And Quality Schema

这份文档只描述当前仓库里真正依赖的最小结构，以及钱包风格蒸馏新增的 reflection lineage 契约。

## 1. RunRecord 最小结构

所有 embedded 或 external runtime 都必须归一为：

- `run_id`
- `runtime_id`
- `runtime_session_id`
- `subject_kind`
- `subject_id`
- `agent_id`
- `flow_id`
- `status`
- `ok`
- `summary`
- `input_payload`
- `output_payload`
- `skill_ids[]`
- `provider_ids[]`
- `trace_ids[]`
- `artifact_ids[]`
- `event_count`
- `trace_count`
- `artifact_count`
- `evaluation_id`
- `started_at`
- `finished_at`
- `metadata`

固定约束：

- `runtime_session_id` 必填
- 不允许通过 run 反推 session 作为正式主路径
- `flow_id` 必须可用于区分通用 run 和业务专用 run
- `metadata.disable_candidate_generation=true` 时，run 只能写 run/evaluation/artifact，不能进入 candidate 生成

## 2. RuntimeEvent 最小结构

- `event_id`
- `run_id`
- `runtime_session_id`
- `event_type`
- `trace_id`
- `status`
- `summary`
- `timestamp`
- `payload`
- `metadata`

`metadata` 可以带 runtime 专有上下文，但主模型不能依赖 Pi 私有字段命名。

## 3. ArtifactRecord 最小结构

- `artifact_id`
- `run_id`
- `runtime_session_id`
- `kind`
- `uri`
- `label`
- `source_step_id`
- `metadata`

reflection run 新增的常见 artifact：

- `reflection_job`
- `reflection_result`
- `reflection_raw_output`
- `reflection_normalized_output`

## 4. RunTrace 最小结构

- `trace_id`
- `run_id`
- `runtime_session_id`
- `events[]`
- `summary`
- `metadata`

## 5. EvaluationRecord 最小结构

当前 evaluation 不只是看状态，而是看结果质量。

- `evaluation_id`
- `run_id`
- `runtime_session_id`
- `subject_type`
- `subject_id`
- `runtime_pass`
- `contract_pass`
- `task_match_score`
- `overall_grade`
- `grade`
- `summary`
- `failure_reason`
- `suggested_action`
- `trace_ids[]`
- `event_ids[]`
- `event_types[]`
- `artifact_ids[]`
- `checks[]`
- `findings[]`
- `evidence_refs[]`
- `metadata`

reflection run 的特殊约束：

- `flow_id=wallet_style_reflection_review`
- 允许生成 `EvaluationRecord`
- 不允许因为 evaluation 结果而自动生成 candidate

## 6. Candidate 最小结构

- `candidate_id`
- `source_run_id`
- `source_evaluation_id`
- `candidate_type`
- `target_skill_name`
- `target_skill_kind`
- `change_summary`
- `generation_spec`
- `manifest_preview`
- `status`
- `validation_status`
- `package_path`
- `bundle_sha256`
- `runtime_session_id`
- `metadata`

钱包风格 skill 的 `generation_spec` 和 `metadata` 现在允许带入以下 lineage 字段：

- `review_backend`
- `reflection_flow_id`
- `reflection_run_id`
- `reflection_session_id`
- `reflection_status`
- `fallback_used`

这些字段只用于追踪 review 来源，不改变 candidate compile/validate/promote 的标准流程。

## 7. PromotionRecord 最小结构

- `promotion_id`
- `candidate_id`
- `source_run_id`
- `source_evaluation_id`
- `candidate_type`
- `candidate_slug`
- `target_skill_name`
- `target_skill_kind`
- `package_path`
- `validation_status`
- `bundle_sha256`
- `registry_status`
- `package_manifest`
- `validation_report`
- `lineage`
- `runtime_session_id`
- `metadata`

对钱包风格 skill，`lineage` 现在至少应能回答三件事：

1. 主 skill 来源于哪个 distillation run
2. 主 distillation run 依赖了哪个 reflection run
3. 这次结果是否经过 fallback

## 8. Wallet Style Reflection Job 契约

### 8.1 ReflectionJobSpec

- `subject_kind`
- `flow_id`
- `system_prompt`
- `compact_input`
- `expected_output_schema`
- `artifact_root`
- `metadata`

默认约束：

- `subject_kind=wallet_style_profile`
- `flow_id=wallet_style_reflection_review`
- `compact_input` 必须是已经预处理过的紧凑 JSON
- `expected_output_schema` 必须显式约束结构化输出

### 8.2 ReflectionJobResult

- `review_backend`
- `reflection_run_id`
- `reflection_session_id`
- `status`
- `raw_output`
- `normalized_output`
- `fallback_used`

语义约束：

- `review_backend`
  - `pi-reflection`
  - `pi-reflection-mock`
  - `wallet-style-extractor-fallback`
- `status`
  - `succeeded`
  - `failed`
  - `fallback`
- `fallback_used=true` 表示主 distillation 最终没有直接采用 reflection 的结构化结果

### 8.3 WalletStyleReviewReport

`WalletStyleReviewReport` 是对钱包风格提取结果的结构化约束，最终要能映射成：

- `WalletStyleProfile`
- `StyleReviewDecision`

至少要覆盖：

- 风格摘要
- 交易频率
- 持仓或集中度倾向
- 风险偏好
- 常见模式
- 推荐 skill 行为边界
- reviewer summary / recommendation

## 9. Wallet Style Distillation Summary 契约

`summary.json`、CLI 输出和 `/api/style-distillations` 当前至少需要暴露：

- `status`
- `wallet_address`
- `chain`
- `job_dir`
- `profile_summary`
- `review_backend`
- `reflection_flow_id`
- `reflection_run_id`
- `reflection_session_id`
- `reflection_status`
- `fallback_used`
- `candidate_id`
- `promotion_id`
- `promoted_skill_path`
- `qa`

`qa` 继续使用既有 3 条标准：

- `candidate_generated`
- `skill_auto_adopted`
- `skill_runnable`

## 10. Package 闭环约束

candidate 进入 package 以后，必须能够对应到标准 skill package 结构：

- `SKILL.md`
- `manifest.json`
- `actions.yaml`
- `agents/interface.yaml`

validate 至少要覆盖：

- package structure
- manifest / actions / interface
- runtime discovery
- dry-run
- evaluation lineage

钱包风格 skill 新增约束：

- 需要能落盘 `references/style_profile.json`
- 需要能让 smoke test 读取到风格画像和 reviewer 建议

## 11. Environment Keys

当前运行、reflection 和存储主路径依赖以下环境变量：

- `OT_DB_DSN`
- `OT_REDIS_URL`
- `OT_BLOB_BACKEND`
- `OT_BLOB_ROOT`
- `OT_BLOB_ENDPOINT`
- `OT_BLOB_BUCKET`
- `OT_BLOB_REGION`
- `OT_BLOB_PREFIX`
- `OT_INLINE_PAYLOAD_LIMIT_BYTES`
- `OT_PI_DEFAULT_MODEL`
- `OT_PI_REFLECTION_MODEL`
- `OT_PI_REFLECTION_REASONING`
- `OT_PI_REFLECTION_MOCK`

说明：

- `OT_DB_DSN` 指向 PostgreSQL 真源
- `OT_REDIS_URL` 只用于 projection 缓存
- `OT_BLOB_BACKEND` 取值 `local | s3 | minio`
- `OT_PI_REFLECTION_MODEL` 用于指定 reflection job 的模型
- `OT_PI_REFLECTION_REASONING` 用于指定 reflection job 的推理强度
- `OT_PI_REFLECTION_MOCK=1` 用于测试和离线验证
- `s3/minio` 认证通过标准 AWS SDK 环境变量提供
