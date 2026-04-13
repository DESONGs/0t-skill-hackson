# 0T Skill Hackson

当前唯一仓库根目录是外层 `0t-skill-v2/`。
本目录是主 Python 工程，根级规则见 [../agent.md](../agent.md)。

`0t-skill_hackson` 是一个 runtime-first 的 SkillOps 控制面。  
主链保持不变：

`run -> evaluation -> candidate -> package -> validate -> promote`

当前 hackathon 应用化链路已经升级为：

`wallet address -> AVE data -> compact json -> Pi reflection agent -> candidate -> compile -> validate -> promote -> smoke QA`

## 当前能力

- 启动并记录 `Pi` runtime session
- 记录外部 agent 的 run / trace / artifact
- 自动做 evaluation、candidate 生成、package 编译、validate、promotion
- 把晋升后的 skill 安装到本地 `skills/`
- 通过前端 dashboard 查看 runtime、candidate、promotion、wallet style distillation
- 输入一个钱包地址，生成一个可自动发现、可运行、可 smoke test 的 `wallet style skill`

## Pi 执行模式

当前 `Pi` 在项目里有两条执行路径：

- `stub runtime path`
  - 默认通用 smoke / runtime run
  - 入口仍是 `vendor/pi_runtime/upstream/coding_agent/src/ot_runtime_entry.ts`
- `reflection execution mode`
  - 专门给 wallet style reflection 用
  - 仍通过同一个 built artifact 启动，但会根据 `pi_mode=reflection` 切换到真实的结构化 review 路径
  - 失败时会回退到本地 `WalletStyleExtractor`

`Hermes` 现在只保留为设计参考，不会被 import、shell 调用，或作为运行时依赖接入项目。

## 核心命令

```bash
ot-enterprise runtime list
ot-enterprise runtime overview --workspace-dir .ot-workspace
ot-enterprise runtime start --runtime pi
ot-enterprise runtime run --runtime pi --prompt "inspect repository"
ot-enterprise runtime record-run --workspace-dir .ot-workspace --payload-file /tmp/run.json

ot-enterprise candidate list --workspace-dir .ot-workspace
ot-enterprise candidate compile --workspace-dir .ot-workspace --candidate-id <candidate-id>
ot-enterprise candidate validate --workspace-dir .ot-workspace --candidate-id <candidate-id>
ot-enterprise candidate promote --workspace-dir .ot-workspace --candidate-id <candidate-id>

ot-enterprise style list --workspace-dir .ot-workspace
ot-enterprise style distill --workspace-dir .ot-workspace --wallet 0xabc --chain solana
```

## 本地启动

```bash
cd 0t-skill_hackson_v2ing
python3 -m venv .venv
source .venv/bin/activate
cp .env.example .env
./scripts/start_stack.sh
./scripts/bootstrap.sh
./scripts/start_frontend.sh
```

常用脚本：

- `./scripts/bootstrap.sh`
- `./scripts/start_frontend.sh`
- `./scripts/start_pi_runtime.sh`
- `./scripts/start_ave_data_service.sh`
- `./scripts/verify.sh`

## 关键环境变量

- `OT_DEFAULT_WORKSPACE`
- `OT_PI_RUNTIME_ROOT`
- `OT_PI_DEFAULT_MODEL`
- `OT_PI_REFLECTION_MODEL`
- `OT_PI_REFLECTION_REASONING`
- `OT_PI_REFLECTION_MOCK`
- `AVE_DATA_PROVIDER`
- `OT_DB_DSN`
- `OT_REDIS_URL`

完整样例见 [.env.example](./.env.example)。

## 文档入口

- [docs/README.md](./docs/README.md)
- [docs/architecture/01-system-overview.md](./docs/architecture/01-system-overview.md)
- [docs/architecture/02-wallet-style-agent-reflection.md](./docs/architecture/02-wallet-style-agent-reflection.md)
- [docs/product/01-plain-language-platform-guide.md](./docs/product/01-plain-language-platform-guide.md)
- [docs/product/02-hackathon-roadshow-wallet-style-skill.md](./docs/product/02-hackathon-roadshow-wallet-style-skill.md)
- [docs/contracts/03-runtime-run-and-evaluation-schema.md](./docs/contracts/03-runtime-run-and-evaluation-schema.md)
