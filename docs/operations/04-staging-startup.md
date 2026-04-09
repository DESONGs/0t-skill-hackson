# Staging Startup

这份文档描述 `0t-skill_enterprise` 的最小 staging 启动流程。

## 1. 前置条件

- Python `3.11+`
- 已执行 `./scripts/bootstrap.sh`
- `.env` 已存在
- 如果使用真实 AVE 数据，`.env` 中已填写 `AVE_API_KEY`

推荐的 `.env` 关键项：

```env
AVE_DATA_PROVIDER=ave_rest
AVE_API_KEY=your-real-key
API_PLAN=free
AVE_DATA_SERVICE_BIND_HOST=127.0.0.1
AVE_DATA_SERVICE_PORT=8080
OT_STAGING_WORKSPACE=.staging-workspace
```

## 2. 一键启动

```bash
./scripts/run_staging_flow.sh
```

默认行为：

1. 读取 `.env`
2. 启动 `ave-data-service`
3. 等待 `/healthz`
4. 执行 `token_due_diligence`
5. 将输出写入 `.staging-workspace/`

## 3. 指定其他 preset 或输入

```bash
./scripts/run_staging_flow.sh \
  --preset wallet_profile \
  --inputs-file examples/staging/wallet_profile.json \
  --workspace-dir .staging-workspace-wallet
```

## 4. 成功判定

- 数据服务进程启动成功
- `/healthz` 返回 `ok`
- `workflow-run` 返回 `status=succeeded`
- 生成：
  - `.staging-workspace/reports/analysis-report.md`
  - `.staging-workspace/reports/analysis-report.json`

## 5. 失败排查

- 若服务无法启动，检查：
  - `.env`
  - `AVE_DATA_PROVIDER`
  - 依赖是否已安装
- 若真实 AVE 请求失败，检查：
  - `AVE_API_KEY`
  - `API_PLAN`
  - `vendor/ave_cloud_skill/scripts/requirements.txt` 依赖是否已装
- 若只想验证本地闭环，可把 `AVE_DATA_PROVIDER=mock`
