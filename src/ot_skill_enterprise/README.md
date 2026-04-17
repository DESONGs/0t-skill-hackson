# ot_skill_enterprise

这里是项目主 Python 包，不是 skill 包目录。

建议阅读顺序：

1. `control_plane/`
2. `style_distillation/`
3. `providers/` 和 `gateway/`
4. `registry/`、`storage/`、`runs/`
5. `reflection/`、`runtime/`、`execution/`

目录说明：

- `control_plane/`：CLI、API、启动接线和系统总入口
- `style_distillation/`：钱包风格蒸馏主流程
- `providers/`：外部数据和能力提供方接入
- `gateway/`：对 skill 与外部服务的边界封装
- `registry/`、`storage/`、`runs/`：运行记录、元数据和持久化
- `reflection/`、`runtime/`、`execution/`：反思、运行时和执行链路
- `skills_compiler/`：skill 产物编译和组装
- `shared/`：公共模型和工具函数

边界：

- 这里放项目业务实现，不直接放公开 skill package 文件
- 公开 skill 看仓库根目录 `skills/`
- vendored 上游实现看仓库根目录 `vendor/`
