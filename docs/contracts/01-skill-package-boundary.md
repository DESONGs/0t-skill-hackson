# Skill Package Boundary

## 1. 目标

锁定 `skills/`、`workflows/`、`src/` 三者的职责边界，避免后续把实现散到错误目录。

## 2. `skills/`

这里只放真正可发布的 skill package。

每个 skill package 至少包含：

- `SKILL.md`
- `manifest.json`
- `actions.yaml`
- `agents/interface.yaml`

可选目录：

- `references/`
- `evals/`
- `scripts/`

禁止把业务实现主逻辑全部塞在 `skills/` 里。

## 3. `workflows/`

这里只放：

- preset
- service task 配置
- DAG 定义
- 输入输出 schema

禁止放：

- AVE client
- 业务逻辑实现
- lab 内核代码

## 4. `src/ot_skill_enterprise/`

这里放项目级业务实现：

- gateway adapter
- analysis logic
- report builder
- workflow glue
- lab glue

## 5. 固定约束

- `skills/` 面向发布
- `workflows/` 面向编排
- `src/ot_skill_enterprise/` 面向实现
- `vendor/` 面向复刻的上游运行依赖和参考能力
