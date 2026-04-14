# 0t-skill-v2

`0t-skill-v2/` 是当前唯一对外交付仓。

## 仓库结构

- `agent.md`
  - 根级规则和边界说明
- `distill-modules/`
  - 钱包风格蒸馏、执行适配、回测和 QA 的开发基线
- `0t-skill_hackson_v2ing/`
  - 主工程，包含运行时代码、前后端、测试、vendored 依赖

## 当前项目情况

项目当前是一条完整的 `wallet-style skill` 链路：

`AVE 数据采集 -> Distill 特征提取 -> Pi/Kimi reflection -> skill build -> dry-run/live execution QA`

固定边界：

- 数据平面：`AVE-only`
- 执行平面：`onchainos CLI-only`
- 对外入口：单入口 `style distill`，内部按阶段推进

## 从哪里启动

```bash
cd /Users/chenge/Desktop/hackson/0t-skill-v2/0t-skill_hackson_v2ing
```

主命令：

```bash
ot-enterprise style list --workspace-dir .ot-workspace
ot-enterprise style get --workspace-dir .ot-workspace --job-id <job_id>
ot-enterprise style distill --workspace-dir .ot-workspace --wallet 0x... --chain bsc
ot-enterprise style resume --workspace-dir .ot-workspace --job-id <job_id>
ot-frontend
```

## 配置原则

配置全部通过环境变量注入，不依赖硬编码密钥。

- 命令行入口和前端服务都会自动读取主工程目录下的 `.env`
- 公开仓只保留 `.env.example`
- 运行产物、工作区、技能包和本地虚拟环境不进入公开版

重点配置分组：

- AVE：`AVE_API_KEY`、`API_PLAN`、`AVE_DATA_PROVIDER`
- Pi/Kimi：`KIMI_API_KEY`、`OT_PI_REFLECTION_MODEL`、`OT_PI_REFLECTION_REASONING`
- onchainos：`OKX_API_KEY`、`OKX_SECRET_KEY`、`OKX_PASSPHRASE`、`ONCHAINOS_HOME`
- 执行默认值：`OT_ONCHAINOS_LIVE_CAP_USD`、`OT_ONCHAINOS_MIN_LEG_USD`

完整样例见：

- [0t-skill_hackson_v2ing/.env.example](./0t-skill_hackson_v2ing/.env.example)

## 文档入口

- [agent.md](./agent.md)
- [distill-modules/00-总结指引.md](./distill-modules/00-总结指引.md)
- [0t-skill_hackson_v2ing/README.md](./0t-skill_hackson_v2ing/README.md)
- [0t-skill_hackson_v2ing/docs/README.md](./0t-skill_hackson_v2ing/docs/README.md)
