# Docs Index

这些文档只描述当前仓库里的实际实现，不记录迭代历史。

## 建议阅读顺序

1. [../README.md](../README.md)
2. [architecture/01-system-overview.md](./architecture/01-system-overview.md)
3. [architecture/02-wallet-style-agent-reflection.md](./architecture/02-wallet-style-agent-reflection.md)
4. [product/01-plain-language-platform-guide.md](./product/01-plain-language-platform-guide.md)
5. [contracts/03-runtime-run-and-evaluation-schema.md](./contracts/03-runtime-run-and-evaluation-schema.md)
6. [contracts/04-workspace-discovery-api.md](./contracts/04-workspace-discovery-api.md)

## 文档分工

- `architecture/`
  - 模块边界、阶段链路、上下文和运行面
- `product/`
  - 对非实现者解释项目如何使用
- `contracts/`
  - API、workspace 和 runtime 字段约束

## 当前口径

- 单入口：`style distill`
- 内部分阶段：`distill_features -> reflection_report -> skill_build -> execution_outcome`
- 数据平面：AVE-only
- 执行平面：onchainos CLI-only
- 配置来源：环境变量，CLI 和前端服务都会自动加载主工程目录 `.env`
