# Skills

这个目录只保留公开仓需要看的 skill。

默认命名规则已经收敛：

- 有明确风格结果时：`风格标签-链-钱包后缀`
- 还没有稳定风格结果时：`distill-链-钱包后缀`

优先关注：

- `ave-data-gateway/`
- `distill-bsc-d5b63e/`
- `meme-hunter-bsc-567a89/`
- `meme-hunter-bsc-c0bc2d/`

边界说明：

- `ave-data-gateway/` 是运行时依赖的基础 gateway skill
- `distill-*` 和 `风格标签-*` 是公开 fixture 或已提升的 skill 包，用于测试、示例和上下文校验
- 其他本地生成 skill 目录默认被 `.gitignore` 忽略，不属于公开仓契约
- agent 首次阅读时不要把 `skills/` 当成主业务代码入口，先看根目录文档、`scripts/`、`src/ot_skill_enterprise/`

如果本地出现类似 `smoke-candidate/` 的目录：

- 把它视为本机运行产物或临时 smoke skill
- 除非任务明确要求分析生成结果，否则忽略它
