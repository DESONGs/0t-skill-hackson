# Wallet Style Agent Reflection

这份文档只讨论一个问题：  
`wallet style distillation` 在升级到 `Pi` 后台 agent 自省器以后，系统现在到底怎么跑。

## 1. 系统上下文图

```mermaid
flowchart LR
    U["用户 / CLI / Dashboard"] --> CP["Control Plane"]
    CP --> SDS["WalletStyleDistillationService"]
    SDS --> AVE["AVE Data Provider"]
    SDS --> REF["PiReflectionService"]
    REF --> PI["Pi Runtime"]
    SDS --> PIPE["RunIngestionPipeline"]
    PIPE --> QA["QAEvaluator"]
    QA --> CAND["Candidate"]
    CAND --> COMP["SkillPackageCompiler"]
    COMP --> PROM["Promotion"]
    PROM --> SKILLS["skills/"]
```

结论：

- `WalletStyleDistillationService` 是应用化总入口
- `PiReflectionService` 只负责风格提取和 review
- skill candidate 仍然由主 distillation run 生成

## 2. 模块关系图

```mermaid
flowchart TD
    SDS["style_distillation/service.py"] --> PRE["wallet preprocess"]
    SDS --> SPEC["ReflectionJobSpec"]
    SDS --> REF["reflection/service.py"]
    REF --> RS["runtime/service.py"]
    RS --> COORD["runtime/coordinator.py"]
    COORD --> EXEC["runtime/executor.py"]
    EXEC --> PIR["vendor/pi_runtime/dist/pi-runtime.mjs"]
    PIR --> MODE["reflection execution mode"]
    MODE --> OUT["normalized review json"]
    SDS --> FALLBACK["WalletStyleExtractor fallback"]
    SDS --> PIPE["runs/pipeline.py"]
    PIPE --> COMP["control_plane/candidates.py + skills_compiler"]
```

模块边界：

- `reflection/`
  - 定义 job/result/report 的最小稳定接口
- `runtime/`
  - 负责标准化 session、run、trace、artifact
- `style_distillation/`
  - 负责编排、fallback、candidate、QA 和 summary

## 3. Reflection Sequence

```mermaid
sequenceDiagram
    participant S as WalletStyleDistillationService
    participant R as PiReflectionService
    participant RS as RuntimeService
    participant PI as "Pi runtime (reflection mode)"
    participant REG as evolution-registry

    S->>R: run(ReflectionJobSpec)
    R->>RS: runtime.run(metadata.pi_mode=reflection)
    RS->>PI: built artifact + reflection job
    PI-->>RS: transcript(output.normalized_output)
    RS->>REG: record run/evaluation/artifacts
    RS-->>R: reflection run result
    R-->>S: ReflectionJobResult
    S->>S: parse normalized output
    alt 结构合法
        S->>S: 生成 WalletStyleProfile + StyleReviewDecision
    else 结构非法 / 运行失败
        S->>S: fallback 到 WalletStyleExtractor
    end
```

关键约束：

- reflection run 的 `flow_id` 固定为 `wallet_style_reflection_review`
- reflection run 的 `disable_candidate_generation=true`
- reflection lineage 必须回写到 distillation `summary.json`

## 4. End-to-End Distillation Sequence

```mermaid
sequenceDiagram
    participant U as User
    participant FE as CLI / Frontend
    participant S as WalletStyleDistillationService
    participant AVE as AVE Provider
    participant PI as Pi Reflection
    participant PIPE as RunIngestionPipeline
    participant CS as CandidateSurfaceService
    participant EB as EnterpriseBridge

    U->>FE: 输入钱包地址
    FE->>S: distill_wallet_style(wallet, chain)
    S->>AVE: inspect_wallet / inspect_token / review_signals
    S->>S: preprocess -> compact json
    S->>PI: reflection job
    PI-->>S: profile + review 或失败
    S->>S: fallback if needed
    S->>PIPE: record main wallet_style_distillation run
    PIPE-->>S: evaluation + candidate lifecycle
    S->>CS: compile -> validate -> promote
    S->>EB: discover promoted skill
    S->>S: smoke test generated primary.py
    S-->>FE: summary + reflection lineage + QA
```

## 5. 关键文件

- `src/ot_skill_enterprise/reflection/models.py`
- `src/ot_skill_enterprise/reflection/service.py`
- `src/ot_skill_enterprise/style_distillation/service.py`
- `src/ot_skill_enterprise/runtime/coordinator.py`
- `src/ot_skill_enterprise/runs/pipeline.py`
- `vendor/pi_runtime/upstream/coding_agent/src/ot_runtime_entry.ts`
- `vendor/pi_runtime/upstream/coding_agent/src/ot_reflection_mode.ts`

## 6. 数据与产物

一次成功的 wallet style distillation 至少会留下三段 lineage：

1. `wallet_style_reflection_review`
   - `reflection_run_id`
   - `reflection_session_id`
   - `reflection_status`
2. `wallet_style_distillation`
   - 主 candidate 生成 run
3. `promotion`
   - 晋升到 `skills/` 的最终包

job 目录下的关键 artifacts：

- `wallet_profile.preprocessed.json`
- `reflection_job.json`
- `reflection_result.json`
- `reflection_normalized_output.json`
- `style_profile.json`
- `style_review.json`
- `summary.json`

## 7. 当前默认值

- 默认优先走 `Pi` reflection
- `OT_PI_REFLECTION_MOCK=1` 时走 mock reflection，用于测试和离线验证
- reflection 失败时回退到 `WalletStyleExtractor`
- 当前仍是单任务同步闭环，不处理并发
