# Public Configuration Guide

这份公开仓已经移除了真实密钥和本地运行产物。要恢复完整功能，只需要在本地安装依赖并填写你自己的 `.env`。

## 已移除内容

- `.env` / `.env.local`
- `.venv/`
- `.ot-workspace/` 与其他本地工作区快照
- `vendor/pi_runtime/node_modules/`
- `vendor/pi_runtime/dist/`
- `vendor/onchainos_cli/upstream/cli/target/`
- `__pycache__/`、`.pytest_cache/`、构建缓存

## 最低前提

- Python `3.11+`
- Node.js `20+`
- `npm`
- Rust / Cargo
  - 需要执行 onchainos CLI 路径时必备
- Docker / Docker Compose
  - 只在你要拉起本地 Postgres / Redis / MinIO 时需要

## 推荐启动流程

```bash
cd 0t-skill_hackson_v2ing
./scripts/bootstrap.sh
cp .env.example .env
```

然后按你的使用场景补配置：

1. 只跑蒸馏与 reflection
   - 至少填 `AVE_API_KEY`、`API_PLAN`、`KIMI_API_KEY`
2. 需要 dry-run / live execution
   - 额外填 `OKX_API_KEY`、`OKX_SECRET_KEY`、`OKX_PASSPHRASE`
   - 确保本机能用 `cargo`
3. 需要本地 infra
   - 设置 `OT_START_LOCAL_STACK=1`
   - 或自己提供 `OT_DB_DSN` / `OT_REDIS_URL` / `OT_BLOB_*`

## 核心环境变量

### 必填

- `AVE_API_KEY`
- `API_PLAN`
- `KIMI_API_KEY`

### 常用默认值

- `AVE_DATA_PROVIDER=ave_rest`
- `OT_RUNTIME_DEFAULT=pi`
- `OT_PI_REFLECTION_MODEL=kimi-coding/kimi-k2-thinking`
- `OT_PI_REFLECTION_REASONING=medium`
- `OT_DEFAULT_WORKSPACE=.ot-workspace`
- `OT_FRONTEND_PORT=8090`

### 执行层

- `OKX_API_KEY`
- `OKX_SECRET_KEY`
- `OKX_PASSPHRASE`
- `ONCHAINOS_HOME`
- `OT_ONCHAINOS_CLI_BIN`
- `OT_ONCHAINOS_LIVE_CAP_USD`
- `OT_ONCHAINOS_MIN_LEG_USD`

### 本地服务与存储

- `OT_START_LOCAL_STACK`
- `OT_DB_DSN`
- `OT_REDIS_URL`
- `OT_BLOB_BACKEND`
- `OT_BLOB_ROOT`
- `OT_BLOB_ENDPOINT`
- `OT_BLOB_BUCKET`
- `OT_BLOB_REGION`

## 公开仓中的 vendored 依赖策略

- `vendor/pi_runtime`
  - 保留源码和 `package-lock.json`
  - `bootstrap.sh` 会在本地执行 `npm install` 与构建
- `vendor/onchainos_cli`
  - 保留 Rust 源码
  - 执行链路会优先使用 `OT_ONCHAINOS_CLI_BIN`，否则回退到 `cargo run`
- `vendor/ave_cloud_skill`
  - 保留 AVE REST 脚本和 requirements
  - `bootstrap.sh` 会把它的 Python 依赖安装进当前 `.venv`

## 验证

```bash
cd 0t-skill_hackson_v2ing
./scripts/verify.sh
```

如果 `vendor/pi_runtime/node_modules` 还没装，脚本会跳过相关检查；先跑一次 `./scripts/bootstrap.sh` 即可。
