# 0T-Skill System Architecture

## High-Level Architecture

```mermaid
graph TB
    subgraph USER["User Interface"]
        CLI["CLI<br/>0t"]
        HTTP["HTTP API"]
        FE["Frontend<br/>Dashboard"]
    end

    subgraph CTRL["Control Plane"]
        CP["Command Router"]
        WS["Workspace Manager"]
    end

    subgraph DATA["Data Plane — AVE"]
        AVE_SVC["AVE Data Service"]
        AVE_ADP["AveDataProviderAdapter"]
        AVE_CLI["AveDataServiceClient"]
    end

    subgraph DISTILL["Distillation Engine"]
        SDS["WalletStyleDistillationService"]
        TP["Trade Pairing (M2)"]
        MC["Market Context (M3)"]
        SF["Signal Filters (M4)"]
        AR["Archetype Classifier (M7)"]
        BT["Backtest Engine (M6)"]
        EX["Extractors (M5)"]
        CTX["Context Assembler"]
    end

    subgraph REFLECT["Reflection Plane"]
        PRS["PiReflectionService"]
        PIR["Pi Runtime (TypeScript)"]
        KIMI["Kimi K2 Model"]
    end

    subgraph COMPILE["Compilation Plane"]
        SPC["SkillPackageCompiler"]
        WSR["WalletStyleRuntime"]
        VAL["Contract Validators"]
    end

    subgraph EXEC["Execution Plane — OKX OnchainOS"]
        OCLI["OnchainOS CLI Adapter"]
        WALL["Wallet Login/Status"]
        SECU["Security Scan"]
        SWAP["Quote/Swap/Broadcast"]
    end

    subgraph RUNTIME["Runtime Layer"]
        RRC["RuntimeRunCoordinator"]
        RES["RuntimeSessionStore"]
        RIP["RunIngestionPipeline"]
    end

    subgraph STORE["Persistence"]
        OTW[".ot-workspace"]
        REG["Evolution Registry"]
        CACHE["Stage Cache"]
    end

    USER --> CTRL
    CTRL --> SDS
    SDS --> AVE_ADP
    AVE_ADP --> AVE_CLI --> AVE_SVC
    SDS --> TP & MC & SF
    TP & MC & SF --> AR
    AR --> EX
    SDS --> CTX --> PRS --> PIR --> KIMI
    PRS --> SDS
    SDS --> BT
    SDS --> SPC --> VAL
    SPC --> WSR
    SDS --> OCLI --> WALL & SECU & SWAP
    RRC --> RES & RIP
    SDS --> RRC
    RRC --> STORE

    style DATA fill:#4ECDC4,color:#000
    style EXEC fill:#FF6B6B,color:#000
    style DISTILL fill:#45B7D1,color:#000
    style REFLECT fill:#96CEB4,color:#000
    style COMPILE fill:#FFE66D,color:#000
```

## Four-Plane Design

The system enforces strict separation between four operational planes:

```mermaid
flowchart LR
    subgraph DP["🟢 Data Plane<br/>AVE ONLY"]
        D1["Wallet profiles"]
        D2["Token details + risk"]
        D3["Market candles"]
        D4["On-chain signals"]
    end

    subgraph RP["🔵 Reflection Plane<br/>Pi/Kimi"]
        R1["Structured reasoning"]
        R2["Style profile generation"]
        R3["Strategy specification"]
    end

    subgraph CP["🟡 Compilation Plane<br/>Skill-OS"]
        C1["Package compilation"]
        C2["Contract validation"]
        C3["Registry promotion"]
    end

    subgraph XP["🔴 Execution Plane<br/>OKX OnchainOS"]
        X1["Wallet auth"]
        X2["Security scan"]
        X3["Simulate / Broadcast"]
    end

    DP -->|"compact_input ≤6KB"| RP
    RP -->|"profile+strategy"| CP
    CP -->|"execution_intent"| XP

    style DP fill:#4ECDC4,color:#000
    style RP fill:#96CEB4,color:#000
    style CP fill:#FFE66D,color:#000
    style XP fill:#FF6B6B,color:#000
```

**Isolation rules:**
- Data Plane (AVE) never executes trades
- Execution Plane (OnchainOS) never provides distillation data
- No cross-plane data backfeed — OnchainOS market/signal/PnL data is never injected into distillation

## Agent Framework

### Agent Hierarchy

```mermaid
flowchart TB
    MA["MainAgent<br/>Repository governance<br/>Cross-layer interface audit"]
    
    AA["Agent A<br/>Repository meta<br/>README + entry points"]
    AB["Agent B<br/>Execution layer<br/>onchainos CLI + config"]
    AC["Agent C<br/>Distillation core<br/>reflection + style + compiler"]
    AD["Agent D<br/>QA & Testing<br/>contract validation"]

    MA --> AA & AB & AC & AD

    style MA fill:#FFE66D,color:#000
```

### WalletStyleDistillationService — Core Orchestrator

```mermaid
sequenceDiagram
    participant User
    participant CLI
    participant SDS as DistillationService
    participant AVE as AVE Provider
    participant Pi as Pi/Kimi
    participant Compiler
    participant OKX as OnchainOS

    User->>CLI: style distill --wallet 0x... --chain bsc
    CLI->>SDS: distill_wallet_style(wallet, chain)
    
    rect rgb(78, 205, 196)
        Note over SDS,AVE: Phase 1: AVE Data Collection (parallel)
        SDS->>AVE: inspect_wallet
        par Token enrichment
            SDS->>AVE: inspect_token × N
            SDS->>AVE: inspect_market × N
            SDS->>AVE: review_signals
        end
    end

    rect rgb(69, 183, 209)
        Note over SDS: Phase 2: Feature Engineering (parallel)
        SDS->>SDS: M2: pair_trades (FIFO)
        SDS->>SDS: M3: compute_market_context
        SDS->>SDS: M4: build_risk_filters
        SDS->>SDS: M7: classify_archetype
    end

    rect rgb(150, 206, 180)
        Note over SDS,Pi: Phase 3: LLM Reflection
        SDS->>SDS: assemble compact_input (≤6KB)
        SDS->>Pi: ReflectionJobSpec
        Pi-->>SDS: profile + strategy + execution_intent
    end

    rect rgb(255, 230, 109)
        Note over SDS,Compiler: Phase 4: Build & Verify
        SDS->>SDS: run_backtest → confidence_score
        SDS->>Compiler: compile → validate → promote
    end

    rect rgb(255, 107, 107)
        Note over SDS,OKX: Phase 5: Execution
        SDS->>OKX: prepare / dry-run / live
    end

    SDS-->>CLI: Skill package + summary
    CLI-->>User: Job complete
```

### Candidate Lifecycle

```mermaid
stateDiagram-v2
    [*] --> Distilled: style distill
    Distilled --> Compiled: compile
    Compiled --> Validated: validate
    Validated --> Promoted: promote
    Promoted --> SmokeTested: smoke test
    SmokeTested --> DryRunReady: dry-run pass
    DryRunReady --> LiveReady: approval granted
    
    Compiled --> Failed: validation error
    SmokeTested --> Failed: smoke failure
    Failed --> [*]
    LiveReady --> [*]
```

## Four-Stage Pipeline Detail

### Stage 1: `distill_features`

```mermaid
flowchart LR
    W["Wallet + Chain"] --> IW["AVE: inspect_wallet"]
    IW --> FT["Pick focus tokens"]
    FT --> PAR["Parallel fetch"]
    
    PAR --> IT["inspect_token × N"]
    PAR --> IM["inspect_market × N"]
    PAR --> RS["review_signals"]
    
    IT & IM & RS --> M2["M2: Trade pairing"]
    IT & IM & RS --> M3["M3: Market context"]
    IT & IM & RS --> M4["M4: Risk filters"]
    
    M2 & M3 & M4 --> M7["M7: Archetype"]
    M7 --> CI["compact_input ≤6KB"]

    style IW fill:#4ECDC4,color:#000
    style IT fill:#4ECDC4,color:#000
    style IM fill:#4ECDC4,color:#000
    style RS fill:#4ECDC4,color:#000
    style M7 fill:#FFE66D,color:#000
```

**Output**: `stage_distill_features.json` + archetype artifact

### Stage 2: `reflection_report`

```mermaid
flowchart LR
    CI["compact_input"] --> SPEC["ReflectionJobSpec<br/>+ output_schema<br/>+ constraints"]
    SPEC --> PI["Pi Runtime<br/>(TypeScript subprocess)"]
    PI --> KIMI["Kimi K2<br/>Structured output"]
    KIMI --> PARSE["Parse & validate"]
    PARSE --> OUT["profile<br/>strategy<br/>execution_intent<br/>review"]
    
    PARSE -->|"Failed"| FB["Fallback:<br/>WalletStyleExtractor"]
    FB --> OUT

    style PI fill:#96CEB4,color:#000
    style KIMI fill:#96CEB4,color:#000
```

**Three-tier degradation**: Mock → Live Pi/Kimi → Rule-based fallback

### Stage 3: `skill_build`

**Output**: Complete Skill package directory + backtest results + confidence score

### Stage 4: `execution_outcome`

```mermaid
flowchart LR
    EI["execution_intent"] --> MODE{Mode?}
    MODE -->|prepare_only| PLAN["Generate execution plan"]
    MODE -->|dry_run| SIM["OnchainOS: simulate"]
    MODE -->|live| BC["OnchainOS: broadcast"]
    
    SIM --> |"Pass"| READY["dry_run_ready ✓"]
    BC --> |"Signed"| DONE["live_executed ✓"]

    style SIM fill:#FF6B6B,color:#000
    style BC fill:#FF6B6B,color:#000
```

## Parallel Execution Strategy

```mermaid
gantt
    title Distillation Pipeline Timeline
    dateFormat X
    axisFormat %Ls

    section M1 Data (AVE)
    inspect_wallet           :m1a, 0, 3
    inspect_token × 4        :m1b, 3, 8
    inspect_market × 4       :m1c, 3, 8
    review_signals           :m1d, 3, 6

    section M2-M4 (parallel)
    Trade pairing            :m2, 8, 9
    Market context           :m3, 8, 9
    Risk filters             :m4, 8, 9

    section M7 Archetype
    classify_archetype       :m7, 9, 10

    section M5 LLM
    Pi/Kimi reflection       :m5, 10, 18

    section M6 Build
    Backtest + compile       :m6, 18, 20
```

**Critical path**: M1(8s) → M2-M4 parallel(1s) → M7(1s) → M5 LLM(8s) → M6(2s) = **~20s total**

## Context Layer Architecture

```mermaid
flowchart TB
    subgraph STATIC["Static Instructions"]
        SI["Fixed stage/reflection prompts"]
    end

    subgraph LEDGER["Canonical Ledger"]
        JM["Job metadata"]
        SS["Stage states"]
        LN["Lineage tracking"]
    end

    subgraph ARTIFACTS["Stage Artifacts (immutable)"]
        A1["stage_distill_features.json"]
        A2["stage_reflection.json"]
        A3["stage_build.json"]
        A4["stage_execution.json"]
        A5["archetype.json"]
    end

    subgraph EPHEMERAL["Ephemeral Envelopes"]
        EE["Reflection call context<br/>(single-use injection)"]
    end

    subgraph MEMORY["Derived Memory"]
        DM["Reusable style insights"]
        RH["Review hints"]
    end

    STATIC --> LEDGER --> ARTIFACTS --> EPHEMERAL --> MEMORY

    style ARTIFACTS fill:#FFE66D,color:#000
```

## Technology Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Parallelism | `ThreadPoolExecutor` | AVE provider uses blocking subprocess; asyncio cannot accelerate |
| Market data for LLM | Pre-computed summaries | Raw K-lines too large (16KB+), would explode context |
| compact_input limit | 6KB | Pi maxTokens=3000 output; input safety zone ~4000 tokens |
| Trade matching | FIFO | On-chain txs have natural time ordering; FIFO is most intuitive |
| Entry factor analysis | Frequency statistics | Sample size <20 makes regression statistically insignificant |
| Archetype classifier | Rule-based + threshold | Transparent, auditable, no black-box ML on small samples |
| Data boundary | AVE-only | Prevents dual-path data drift between distillation and execution |
| Execution adapter | OnchainOS CLI | Mature execution capability, cleanly decoupled from data plane |
