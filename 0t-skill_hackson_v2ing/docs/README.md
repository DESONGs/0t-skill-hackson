# Docs Index

这里只保留和当前代码直接对应的文档。

## 建议阅读顺序

1. [../README.md](../README.md)
2. [architecture/01-system-overview.md](./architecture/01-system-overview.md)
3. [architecture/02-wallet-style-agent-reflection.md](./architecture/02-wallet-style-agent-reflection.md)
4. [product/01-plain-language-platform-guide.md](./product/01-plain-language-platform-guide.md)
5. [product/02-hackathon-roadshow-wallet-style-skill.md](./product/02-hackathon-roadshow-wallet-style-skill.md)
6. [contracts/03-runtime-run-and-evaluation-schema.md](./contracts/03-runtime-run-and-evaluation-schema.md)
7. [contracts/04-workspace-discovery-api.md](./contracts/04-workspace-discovery-api.md)

## 当前文档分工

- `architecture/`
  - 面向工程实现、模块边界、调用链和演进约束
- `product/`
  - 面向理解和路演，强调用户路径和 demo 讲法
- `contracts/`
  - 面向字段、最小结构、workspace / runtime / lineage 约束

## 关键更新

- `Pi` 现在同时支持 `stub runtime path` 和 `reflection execution mode`
- wallet style distillation 主链已经升级为 `数据预处理 -> Pi reflection -> candidate -> compile -> validate -> promote`
- `Hermes` 是机制参考，不是项目运行依赖
- 所有旧仓库绝对路径已经替换为当前仓库内相对链接
