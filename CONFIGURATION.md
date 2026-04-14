# Public Copy Configuration Notes

这份文档说明公开副本里哪些内容被保留，哪些内容被抽离，以及如果要恢复完整运行链路需要补什么。

## 公开副本保留内容

- 核心源码：
  - `0t-skill_hackson_v2ing/src/ot_skill_enterprise/`
- 前端静态资源：
  - `0t-skill_hackson_v2ing/frontend/`
- 两个 example skills：
  - `0t-skill_hackson_v2ing/skills/wallet-style-test-bsc-9048f6-20260415-671d5c3d/`
  - `0t-skill_hackson_v2ing/skills/wallet-style-test-bsc-bac453-20260415-845b5f24/`
- 公开说明文档：
  - 根级 `README.md`
  - 根级 `CONFIGURATION.md`
  - 根级 `agent.md`
  - 内层 `README.md`
  - 内层 `agent.md`

## 已抽离内容

### 本地与敏感数据

- `.env`
- `.env.local`
- `.venv/`
- `.ot-workspace/`
- `.pytest_cache/`
- `__pycache__/`

### 私有或外置依赖

- `vendor/`
- `services/`
- `scripts/`
- `docker-compose.yml`
- `pyproject.toml`
- `bin/ot-enterprise`

### 开发与修复材料

- `tests/`
- `docs/`
- `distill-modules/`
- fix/debug/QA 类过程文档

## 为什么要抽离

- 避免泄露本地绝对路径、账号配置和工作区结构
- 避免公开 vendored 上游仓或私有运行时拼装细节
- 让 GitHub 公开仓只表达“架构和核心代码”，不绑定本地执行环境
- 避免把开发阶段文档、修复记录和一次性 QA 产物带进公开交付

## 恢复完整运行链路时需要补回的能力

### 数据平面

- AVE 数据提供层
- 钱包、市场、信号与代币信息查询能力
- WSS 价格流能力

### 反射平面

- Pi / Kimi 或等价 reflection backend
- 对应模型配置和鉴权

### 执行平面

- onchain 执行 CLI 或等价执行适配器
- 钱包登录与安全扫描能力
- dry-run / broadcast 能力

### 安装与编排

- Python 3.11 运行环境
- 项目依赖安装清单
- 本地服务启动脚本或容器编排

## 环境变量清单

公开副本没有附带 `.env.example`。如果你要恢复完整工程，至少需要准备以下变量：

### 数据与反射

- `AVE_API_KEY`
- `API_PLAN`
- `AVE_DATA_PROVIDER`
- `KIMI_API_KEY`
- `OT_PI_REFLECTION_MODEL`
- `OT_PI_REFLECTION_REASONING`
- `OT_PI_REFLECTION_MOCK`

### 执行层

- `OKX_API_KEY`
- `OKX_SECRET_KEY`
- `OKX_PASSPHRASE`
- `ONCHAINOS_HOME`
- `OT_ONCHAINOS_CLI_BIN`
- `OT_ONCHAINOS_LIVE_CAP_USD`
- `OT_ONCHAINOS_MIN_LEG_USD`
- `OT_ONCHAINOS_APPROVAL_WAIT_RETRIES`
- `OT_ONCHAINOS_APPROVAL_WAIT_SECONDS`

### 前端与工作区

- `OT_DEFAULT_WORKSPACE`
- `OT_FRONTEND_BIND_HOST`
- `OT_FRONTEND_PORT`
- `AVE_USE_DOCKER`

## Example skill 说明

公开副本保留了两个地址蒸馏得到的 skill 包作为样例：

- `0x9048f6c683abb0eba156797fd699fe662b4dbfef`
- `0xbac453b9b7f53b35ac906b641925b2f5f2567a89`

处理规则：

- skill 包结构完整保留
- 示例中的本地绝对路径和工作区路径已脱敏
- 这些 example skills 用于展示输出形态，不代表公开副本已经具备完整执行依赖

## 推荐上传口径

- 把这个公开副本当作“脱敏源码快照”
- 不承诺仓库开箱即跑
- 重点展示：
  - 蒸馏链路设计
  - skill 编译产物结构
  - 执行接口设计
  - example skill 输出形态
