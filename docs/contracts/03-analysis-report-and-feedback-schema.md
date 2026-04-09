# Analysis Report And Feedback Schema

## 1. 报告最小结构

`analysis-core` 输出的最终报告必须同时提供：

- `report.md`
- `report.json`

`report.json` 必须至少包含：

- `task_summary`
- `scope`
- `key_findings[]`
- `risk_flags[]`
- `unknowns[]`
- `data_sources[]`
- `artifacts[]`
- `generated_at`

## 2. feedback 最小结构

进入反馈链路的记录至少包含：

- `run_id`
- `skill_id`
- `action_id`
- `status`
- `summary`
- `artifacts[]`
- `error_code`
- `metadata`

## 3. case 最小结构

- `case_id`
- `source`
- `pattern`
- `evidence`
- `severity`
- `metadata`

## 4. proposal 最小结构

- `proposal_id`
- `case_id`
- `target_skill_name`
- `decision_mode`
- `change_summary`
- `target_layer`
- `metadata`

## 5. 约束

- gateway 相关失败只能生成运行反馈，不能进入自动演化
- 只有 `analysis-core` 失败或质量不达标时，才能继续生成 case / proposal
