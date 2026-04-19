# `0t team` / `0t-protocol` Guide

这份文档只讲一件事：什么时候该用 `0t team`。

先说结论：

- 正常运行项目，用 `0t workflow`
- 只有做长任务、多 agent 协作、handoff、审批时，才用 `0t team`

## `0t-protocol` 是什么

`0t-protocol/` 是仓库里跟代码一起维护的 protocol bundle。  
它里面放的是：

- role 文档
- workflow 定义
- module 定义
- 让 planner / optimizer / reviewer 协作时能对齐的一组规则

它不是项目启动入口，也不是第二套运行时。

## `0t team` 是什么

`0t team` 是 operator facade。

它负责：

- 创建长任务 session
- 让 Codex / Claude Code 这类 agent 接手协作任务
- 查询当前 session 状态
- 在需要时生成 handoff
- 提交 review / approve / archive 这类 operator 动作

它不负责：

- 持有真正的 workflow 状态
- 自己再做第二套 recommendation / approval 状态机

真正的状态 owner 是 `TS Pi kernel`。

## 最常用的命令

```bash
uv run 0t team doctor
uv run 0t team start autoresearch --workspace desk-alpha --skill my-skill --adapter codex --data-source-adapter ave --execution-adapter onchainos_cli
uv run 0t team status <session_id>
uv run 0t team review <session_id>
uv run 0t team approve <session_id> --variant <variant_id>
```

## `start` 之后会发生什么

`0t team start` 默认不会只创建一个空 session。  
它会直接把任务往前推进，直到进入下面几种状态之一：

- `awaiting_approval`
- `recommended`
- terminal failure

也就是说，普通情况下你不需要手工一步一步编排。

## 什么时候才需要 `handoff`

只有当 kernel 明确给出 `handoff_ready` work item 时，才用：

```bash
uv run 0t team handoff ...
uv run 0t team submit-work ...
```

如果 session 没进入 `handoff_ready`，那这两个命令就不是常规主路径。

## adapter 从哪里来

`0t team start` 不会偷偷帮你选 adapter。

它只认两种来源：

- CLI 显式参数  
  `--data-source-adapter` / `--execution-adapter`
- workspace 配置  
  `.ot-workspace/workspaces/<workspace_id>/workflow-config.json`

## 你什么时候应该回到普通文档

如果任务已经不是多 agent 协作，而是：

- 启动项目
- 跑蒸馏
- 调试服务
- 查环境变量

那就回到：

- [README.md](../../README.md)
- [START_HERE.md](../../START_HERE.md)
- [AGENTS.md](../../AGENTS.md)
