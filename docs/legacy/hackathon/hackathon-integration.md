# AVE, OKX OnchainOS & Skill-OS Framework Integration

## Three-Component Relationship

```mermaid
flowchart TB
    subgraph SKILLOS["🧠 Skill-OS Framework (Self-developed)"]
        direction TB
        ORCH["Orchestration Engine"]
        DIST["Distillation Pipeline"]
        REFL["LLM Reflection"]
        COMP["Skill Compiler"]
        ARCH["Archetype Classifier"]
    end

    subgraph AVE_BLOCK["📊 AVE (Data Intelligence)"]
        direction TB
        AVE_W["inspect_wallet"]
        AVE_T["inspect_token"]
        AVE_M["inspect_market"]
        AVE_S["review_signals"]
    end

    subgraph OKX_BLOCK["⚡ OKX OnchainOS (Execution)"]
        direction TB
        OKX_L["wallet login"]
        OKX_SEC["security scan"]
        OKX_Q["quote/swap"]
        OKX_B["broadcast"]
    end

    AVE_BLOCK -->|"Raw data"| SKILLOS
    SKILLOS -->|"Skill package<br/>execution_intent"| OKX_BLOCK

    SKILLOS -->|"compact_input"| LLM["Pi/Kimi<br/>LLM Reflection"]
    LLM -->|"profile+strategy"| SKILLOS

    style AVE_BLOCK fill:#4ECDC4,color:#000
    style OKX_BLOCK fill:#FF6B6B,color:#000
    style SKILLOS fill:#45B7D1,color:#FFF
```

**Metaphor:**
- **AVE** = the system's **eyes** — sees the on-chain world
- **Skill-OS** = the system's **brain** — analyzes, reasons, compiles
- **OKX OnchainOS** = the system's **hands** — executes real trades

## Data Flow Through All Three Components

```mermaid
sequenceDiagram
    participant AVE as 📊 AVE
    participant OS as 🧠 Skill-OS
    participant LLM as 🔮 Pi/Kimi
    participant OKX as ⚡ OKX OnchainOS

    rect rgb(78, 205, 196)
        Note over AVE,OS: Data Collection (AVE → Skill-OS)
        OS->>AVE: inspect_wallet(0x..., bsc)
        AVE-->>OS: wallet profile + holdings + tx history
        par Parallel enrichment
            OS->>AVE: inspect_token × N
            AVE-->>OS: token details + risk scans
            OS->>AVE: inspect_market × N
            AVE-->>OS: K-lines + liquidity
            OS->>AVE: review_signals
            AVE-->>OS: on-chain anomalies
        end
    end

    rect rgb(69, 183, 209)
        Note over OS: Feature Engineering (Skill-OS internal)
        OS->>OS: M2: Trade pairing (FIFO)
        OS->>OS: M3: Market context
        OS->>OS: M4: Risk filters
        OS->>OS: M7: Archetype classification
        OS->>OS: Assemble compact_input (≤6KB)
    end

    rect rgb(150, 206, 180)
        Note over OS,LLM: LLM Reflection (Skill-OS → Pi/Kimi)
        OS->>LLM: compact_input + output_schema
        LLM-->>OS: profile + strategy + execution_intent
    end

    rect rgb(255, 230, 109)
        Note over OS: Compilation (Skill-OS internal)
        OS->>OS: Backtest validation
        OS->>OS: Compile Skill package
        OS->>OS: Validate + promote
    end

    rect rgb(255, 107, 107)
        Note over OS,OKX: Execution (Skill-OS → OKX OnchainOS)
        OS->>OKX: wallet login/status
        OKX-->>OS: authenticated
        OS->>OKX: security scan(target_token)
        OKX-->>OS: risk assessment
        OS->>OKX: quote(swap params)
        OKX-->>OS: route + price
        OS->>OKX: simulate (dry-run)
        OKX-->>OS: simulation result
        OS->>OKX: broadcast (live, if approved)
        OKX-->>OS: tx hash
    end
```

## AVE Integration Detail

### Adapter Architecture

```mermaid
flowchart TB
    subgraph SKILLOS["Skill-OS"]
        SDS["DistillationService"]
        ADA["AveDataProviderAdapter"]
        CLI["AveDataServiceClient"]
        PAR["ProviderActionResult"]
    end

    subgraph SERVICE["AVE Data Service"]
        SVC["HTTP Server (localhost:8080)"]
        PROV["AveRestProvider"]
    end

    subgraph UPSTREAM["AVE Cloud"]
        API["AVE REST API"]
    end

    SDS -->|"run(action, payload)"| ADA
    ADA -->|"Pydantic validation"| CLI
    CLI -->|"HTTP request"| SVC
    SVC -->|"Provider routing"| PROV
    PROV -->|"REST call"| API
    API -->|"JSON response"| PROV
    PROV -->|"Normalized envelope"| SVC
    SVC -->|"JSON"| CLI
    CLI -->|"ProviderActionResult"| ADA
    ADA -->|"artifact saved to disk"| SDS

    style UPSTREAM fill:#4ECDC4,color:#000
    style SERVICE fill:#88D8B0,color:#000
```

### Two-Layer Data Architecture

```mermaid
flowchart TB
    subgraph FULL["Full Data Layer (Python-side)"]
        F1["full_activity_history (100+ txs)"]
        F2["Complete token_profiles (holder snapshots)"]
        F3["Complete market_data (OHLCV candles)"]
        F4["Complete completed_trades (paired results)"]
    end

    subgraph COMPACT["Compact Data Layer (LLM-side, ≤6KB)"]
        C1["wallet_summary — 0.3KB"]
        C2["holdings Top 5 — 0.5KB"]
        C3["recent_activity Top 8 — 1.0KB"]
        C4["derived_stats + M2 — 0.5KB"]
        C5["market_context — 0.9KB"]
        C6["signal_context — 0.5KB"]
        C7["archetype_context — 0.3KB"]
        C8["token_snapshots — 0.6KB"]
    end

    FULL -->|"Compression + truncation"| COMPACT
    FULL -->|"Used by"| USE1["M2 statistics<br/>M4 entry factors<br/>M6 backtest"]
    COMPACT -->|"Sent to"| USE2["Pi/Kimi<br/>LLM reflection"]

    style FULL fill:#4ECDC4,color:#000
    style COMPACT fill:#FFE66D,color:#000
```

## OKX OnchainOS Integration Detail

### Execution Chain

```mermaid
flowchart LR
    EI["execution_intent<br/>from Skill package"]

    subgraph PREFLIGHT["Preflight"]
        L["wallet login/status"]
        A["wallet addresses"]
        B["wallet balance"]
    end

    subgraph SECURITY["Security"]
        S["security scan<br/>on target token"]
    end

    subgraph TRADE["Trade"]
        Q["quote<br/>(DEX routing)"]
        AP["approval<br/>(token allowance)"]
    end

    subgraph EXEC["Execute"]
        SIM["simulate<br/>(dry-run)"]
        BC["broadcast<br/>(live)"]
    end

    EI --> PREFLIGHT --> SECURITY --> TRADE --> EXEC

    style PREFLIGHT fill:#FFB6C1,color:#000
    style SECURITY fill:#FFA07A,color:#000
    style TRADE fill:#FF6B6B,color:#000
    style EXEC fill:#DC143C,color:#FFF
```

### Execution Modes

```mermaid
stateDiagram-v2
    [*] --> PrepareOnly: mode=prepare_only
    [*] --> DryRun: mode=dry_run
    [*] --> Live: mode=live

    PrepareOnly --> PlanGenerated: Generate execution plan
    DryRun --> Simulated: OnchainOS simulate
    Live --> Broadcast: OnchainOS broadcast (requires approval)

    PlanGenerated --> [*]
    Simulated --> [*]
    Broadcast --> [*]
```

### Security Constraints

| Constraint | Value | Source |
|---|---|---|
| Live execution approval | `requires_explicit_approval: true` | Always enforced |
| Single trade USD cap | `OT_ONCHAINOS_LIVE_CAP_USD` (default $10) | Environment variable |
| Minimum trade leg | `OT_ONCHAINOS_MIN_LEG_USD` (default $5) | Environment variable |
| Security scan | Mandatory preflight step | Hardcoded in execution chain |
| OKX credentials | `OKX_API_KEY`, `OKX_SECRET_KEY`, `OKX_PASSPHRASE` | Environment variables |

### Supported Chains

| Chain | Chain ID | Status |
|---|---|---|
| BSC | 56 | Verified |
| Ethereum | 1 | Supported |
| Base | 8453 | Supported |
| Polygon | 137 | Supported |
| Arbitrum | 42161 | Supported |
| Optimism | 10 | Supported |

## Skill-OS Framework — The Orchestration Brain

### How Skill-OS Bridges AVE and OKX

```mermaid
flowchart TB
    subgraph AVE_SIDE["AVE (Data Input)"]
        A1["inspect_wallet"]
        A2["inspect_token"]
        A3["inspect_market"]
        A4["review_signals"]
    end

    subgraph SKILLOS["Skill-OS Processing"]
        S1["M1: Data Collection"]
        S2["M2-M4: Feature Engineering"]
        S3["M7: Archetype Classification"]
        S4["M5: LLM Distillation"]
        S5["M6: Backtest & Confidence"]
        S6["Skill Package Compilation"]
    end

    subgraph OKX_SIDE["OKX OnchainOS (Execution Output)"]
        O1["wallet login"]
        O2["security scan"]
        O3["quote / simulate"]
        O4["broadcast"]
    end

    AVE_SIDE --> S1
    S1 --> S2 --> S3 --> S4 --> S5 --> S6
    S6 --> OKX_SIDE

    style AVE_SIDE fill:#4ECDC4,color:#000
    style SKILLOS fill:#45B7D1,color:#FFF
    style OKX_SIDE fill:#FF6B6B,color:#000
```

### Skill-OS Module Map

```mermaid
flowchart TB
    subgraph CTRL["Control Plane"]
        CLI["CLI: 0t"]
        API["HTTP API"]
        FE["Frontend server"]
    end

    subgraph DISTILL["style_distillation/"]
        SVC["service.py — Main orchestrator"]
        TP["trade_pairing.py — M2"]
        MC["market_context.py — M3"]
        SF["signal_filters.py — M4"]
        AR["archetype.py — M7"]
        EX["extractors.py — M5"]
        BT["backtesting.py — M6"]
        CTX["context.py — Context assembler"]
        RB["reflection_builders.py"]
        MOD["models.py"]
    end

    subgraph REFLECT["reflection/"]
        PRS["service.py — PiReflectionService"]
        PRM["models.py — Job specs"]
    end

    subgraph COMPILE["skills_compiler/"]
        CMP["compiler.py — SkillPackageCompiler"]
        WSR["wallet_style_runtime.py"]
    end

    subgraph EXECUTE["execution/"]
        ONC["onchainos_cli.py — OKX adapter"]
    end

    subgraph PROVIDE["providers/ave/"]
        ADP["adapter.py — AveDataProviderAdapter"]
        ACL["client.py"]
    end

    subgraph RUNTIME["runtime/"]
        RRC["coordinator.py"]
        REX["executor.py"]
        PIA["pi/adapter.py"]
    end

    CTRL --> DISTILL
    DISTILL --> REFLECT & COMPILE & EXECUTE & PROVIDE
    DISTILL --> RUNTIME

    style DISTILL fill:#45B7D1,color:#FFF
    style PROVIDE fill:#4ECDC4,color:#000
    style EXECUTE fill:#FF6B6B,color:#000
```

### Skill Package: Where AVE Data Meets OKX Execution

```mermaid
flowchart TB
    PKG["📦 Skill Package"]

    subgraph FROM_AVE["Derived from AVE Data"]
        SP["style_profile.json<br/>Trading style portrait"]
        SS["strategy_spec.json<br/>Entry/exit conditions"]
        TC["token_catalog.json<br/>Focus token metadata"]
        AT["archetype.json<br/>Behavioral classification"]
    end

    subgraph FROM_LLM["Derived from LLM Reflection"]
        SK["SKILL.md<br/>Human-readable description"]
        MF["manifest.json<br/>Complete metadata"]
    end

    subgraph FOR_EXEC["For OKX Execution"]
        EI["execution_intent.json<br/>Adapter + mode + workflow"]
        PP["primary.py<br/>Strategy decisions (reads AVE)"]
        EP["execute.py<br/>Trade execution (calls OKX)"]
    end

    PKG --> FROM_AVE & FROM_LLM & FOR_EXEC

    style FROM_AVE fill:#4ECDC4,color:#000
    style FROM_LLM fill:#96CEB4,color:#000
    style FOR_EXEC fill:#FF6B6B,color:#000
```

### Script Responsibility Split

| Script | Network Access | Data Source | Responsibility |
|---|---|---|---|
| `primary.py` | AVE only | AVE market data | Read real-time data, evaluate entry conditions, output trade plan |
| `execute.py` | OnchainOS only | OnchainOS CLI | Receive trade plan, execute security → quote → approve → swap |

This separation ensures **data judgment** and **trade execution** are decoupled — either side can iterate independently.

## Environment Variables Summary

### AVE Data Plane

| Variable | Purpose |
|---|---|
| `AVE_API_KEY` | AVE API authentication |
| `API_PLAN` | AVE API plan tier |
| `AVE_DATA_PROVIDER` | Provider identifier (default: `ave_rest`) |
| `AVE_DATA_SERVICE_URL` | AVE data service URL (default: localhost:8080) |

### OKX Execution Plane

| Variable | Purpose |
|---|---|
| `OKX_API_KEY` | OKX API key (required for live) |
| `OKX_SECRET_KEY` | OKX secret (required for live) |
| `OKX_PASSPHRASE` | OKX passphrase (required for live) |
| `OT_ONCHAINOS_LIVE_CAP_USD` | Max USD per trade (default $10) |

### LLM Reflection

| Variable | Purpose |
|---|---|
| `KIMI_API_KEY` | Kimi K2 model key |
| `OT_PI_REFLECTION_MODEL` | Model selection |
| `OT_PI_REFLECTION_MOCK` | Enable mock mode |
