# 0T Skill Hackson Public

这是 `0t-skill-v2` 最新主线的公开脱敏仓。它保留完整的 `wallet-style skill` 运行链路源码、测试、脚本和 vendored source，只移除了真实密钥、本地工作区、虚拟环境、`node_modules`、Rust `target` 和其他运行产物。

完整链路仍然是：

`AVE 数据采集 -> Distill 特征提取 -> Pi/Kimi reflection -> skill build -> dry-run/live execution QA`

## 公开版保证

- 可以复现完整工程能力，不是只展示架构的“样例仓”
- 所有敏感配置都改成环境变量模板，真实 key 不在仓库中
- vendored 依赖保留源码快照，安装阶段会在本地重新构建
- 运行产物默认忽略，不会再次污染公开仓

## 环境前提

- Python `3.11+`
- Node.js `20+` 与 `npm`
- Rust / Cargo
  - 仅在你要跑 onchainos 执行链路、dry-run 或 live execution 时需要
- Docker / Docker Compose
  - 仅在你想拉起本地 Postgres、Redis、MinIO 开发栈时需要

## 快速开始

```bash
cd 0t-skill_hackson_v2ing
./scripts/bootstrap.sh
cp .env.example .env
```

把 `.env` 里的占位值替换成你自己的配置后，常用启动方式如下：

```bash
./scripts/start_ave_data_service.sh
./scripts/start_frontend.sh
./scripts/start_pi_runtime.sh
```

主命令：

```bash
ot-enterprise style list --workspace-dir .ot-workspace
ot-enterprise style get --workspace-dir .ot-workspace --job-id <job_id>
ot-enterprise style distill --workspace-dir .ot-workspace --wallet 0x... --chain bsc
ot-enterprise style resume --workspace-dir .ot-workspace --job-id <job_id>
ot-frontend
```

## 仓库结构

- `0t-skill_hackson_v2ing/`
  - 主工程：源码、服务、脚本、测试、vendored source
- `distill-modules/`
  - 蒸馏链设计与模块拆分文档
- `docs/`
  - 改造方案、验收材料、问题分析
- `agent.md`
  - 根级协作边界说明

## 说明文档

- [CONFIGURATION.md](./CONFIGURATION.md)
- [0t-skill_hackson_v2ing/README.md](./0t-skill_hackson_v2ing/README.md)
- [0t-skill_hackson_v2ing/docs/README.md](./0t-skill_hackson_v2ing/docs/README.md)
- [distill-modules/00-总结指引.md](./distill-modules/00-总结指引.md)
