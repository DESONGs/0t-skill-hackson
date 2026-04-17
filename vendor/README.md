# Vendor Boundary

`vendor/` 保存 vendored 上游代码，不是仓库的默认阅读入口。

正常情况下：

- 先看仓库根目录文档
- 再看 `scripts/`
- 再看 `src/ot_skill_enterprise/`
- 只有任务明确落在 vendored 运行时或上游差异上时，才进入这里

目录说明：

- `ave_cloud_skill/`：AVE REST bridge 和相关脚本
- `pi_runtime/`：PI runtime 的 vendored Node 源码与构建产物
- `onchainos_cli/`：OKX OnchainOS CLI 的 vendored Rust 源码
- `skill_enterprise/`：上游 enterprise skill 参考实现

agent 使用建议：

- 不要把 `vendor/` 当成主业务代码入口
- 不要优先在这里 grep 整个项目行为
- 只有当任务涉及 runtime 构建、OnchainOS、AVE bridge 或上游对比时再读这里
