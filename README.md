# 0t-skill-hackson

当前仓库是公开发布用的独立副本，对外交付以本仓库根目录为准。

## 仓库结构

- `agent.md`
  - 根级唯一权威规则
- `distill-modules/`
  - 开发前冻结的蒸馏与执行设计文档
- `0t-skill_hackson_v2ing/`
  - 主 Python 工程

## 开发入口

统一从仓库根目录进入，再进入内层主工程执行代码、测试和脚本。

```bash
git clone https://github.com/DESONGs/0t-skill-hackson.git
cd 0t-skill-hackson
cd 0t-skill_hackson_v2ing
```

## 当前设计口径

- 数据平面：`AVE-only`
- 执行平面：`onchainos CLI-only`
- 目标链路：`AVE 蒸馏 -> Pi reflection -> StrategySpec + ExecutionIntent -> skill compile -> execute action -> dry-run/live-ready QA`

详细设计见：

- [agent.md](./agent.md)
- [distill-modules/00-总结指引.md](./distill-modules/00-总结指引.md)
- [distill-modules/M5-LLM蒸馏与Skill生成.md](./distill-modules/M5-LLM蒸馏与Skill生成.md)
- [distill-modules/M6-回测与置信度评估.md](./distill-modules/M6-回测与置信度评估.md)
