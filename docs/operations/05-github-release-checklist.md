# GitHub Release Checklist

推送到 GitHub 前，建议按下面顺序检查。

## 1. 环境与密钥

- 仓库中没有真实 `.env`
- `.env.example` 与当前代码需要的变量一致
- 没有提交任何 AVE 密钥、代理地址或私有配置

## 2. 运行与测试

- 执行过 `./scripts/verify.sh`
- 至少本地跑通过一次：
  - `./scripts/start_ave_data_service.sh`
  - `PYTHONPATH=src python -m ot_skill_enterprise.root_cli bridge discover`
  - `PYTHONPATH=src python -m ot_skill_enterprise.root_cli workflow-run ...`

## 3. 文档

- `README.md` 与实际目录、命令、运行方式一致
- `agent.md` 与当前协作方式一致
- `docs/README.md` 不再引用已删除文档

## 4. 目录清理

- 已清理：
  - `.ot-workspace/`
  - `.staging-workspace/`
  - `.enterprise-installs/`
  - `__pycache__/`
  - `.pytest_cache/`
- 没有误提交临时日志和本地测试产物

## 5. 发布边界

- 未引入 trade
- 未引入 WSS
- `analysis-core` 没有直接访问公网
- `ave-data-gateway` 仍然是稳定只读数据层
