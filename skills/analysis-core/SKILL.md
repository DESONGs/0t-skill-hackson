# analysis-core

`analysis-core` 是本项目的分析主脑 skill。

职责边界：

- 只负责分析计划、证据整合和报告输出
- 只通过 `ave-data-gateway` 间接使用 AVE 数据
- 只允许本 skill 进入 feedback / lab / promotion 自我迭代闭环

提供的本地动作：

- `plan_data_needs`
- `synthesize_evidence`
- `write_report`

产物约定：

- `analysis/plan.json`
- `analysis/findings.json`
- `reports/analysis-report.md`
- `reports/analysis-report.json`
