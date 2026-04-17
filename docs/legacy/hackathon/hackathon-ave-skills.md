# Project Documentation — Description of AVE Skills Used

## Project Overview

**0T-Skill** is an on-chain wallet trading style distillation and autonomous execution system. Given any wallet address, it automatically analyzes historical trading behavior, distills it into a structured trading strategy, and packages it as an executable **Skill** that can run real trades through OKX OnchainOS.

**AVE is the exclusive data intelligence layer** powering the entire distillation pipeline. Without AVE, there is no data — and without data, there is no distillation.

```mermaid
flowchart LR
    W["🏦 Wallet Address"]
    
    subgraph AVE["📊 AVE Skills"]
        IW["inspect_wallet"]
        IT["inspect_token"]
        IM["inspect_market"]
        RS["review_signals"]
        DT["discover_tokens"]
    end

    subgraph ENGINE["🧠 Distillation Engine"]
        D["Style Analysis"]
        A["Archetype Classification"]
        R["LLM Reflection"]
    end

    PKG["📦 Executable Skill"]
    EXEC["⚡ Live Trades"]

    W --> AVE --> ENGINE --> PKG --> EXEC

    style AVE fill:#4ECDC4,color:#000
    style PKG fill:#FFE66D,color:#000
```

---

## AVE Skills Used

### 1. `inspect_wallet`

**Purpose**: The entry point for every distillation job. Retrieves the complete wallet profile including holdings, balance, and full transaction history.

```mermaid
flowchart LR
    INPUT["Wallet: 0x...<br/>Chain: bsc"]
    IW["AVE: inspect_wallet"]
    
    subgraph OUTPUT["Returned Data"]
        H["Holdings<br/>(tokens + balances)"]
        B["Wallet Balance<br/>(native + USD)"]
        TX["Transaction History<br/>(up to 5 pages)"]
        META["Wallet Metadata<br/>(age, activity level)"]
    end

    INPUT --> IW --> OUTPUT

    style IW fill:#4ECDC4,color:#000
```

**How 0T-Skill uses it:**

| Data Field | Usage in Pipeline | Module |
|---|---|---|
| `holdings` | Extract Top-5 holdings by value; determine stablecoin bias | M1 |
| `activity` | Raw transaction history for FIFO trade pairing | M1 → M2 |
| `balance` | Calculate position sizing relative to total wallet value | M2 |
| `wallet_metadata` | Determine wallet age, activity frequency, session windows | M1 → M7 |

**Example call:**
```python
ave_provider.run("inspect_wallet", {
    "wallet": "0xbac453b9b7f53b35ac906b641925b2f5f2567a89",
    "chain": "bsc",
    "include_holdings": True,
    "include_activity": True
})
```

**Paginated history**: The system fetches up to 5 pages of transaction history to build a comprehensive behavioral profile while respecting AVE API rate limits.

---

### 2. `inspect_token`

**Purpose**: Enriches each focus token with detailed metadata, holder distribution, and smart contract risk assessment.

```mermaid
flowchart LR
    INPUT["Token: PP<br/>Chain: bsc"]
    IT["AVE: inspect_token"]

    subgraph OUTPUT["Returned Data"]
        TD["Token Details<br/>(name, symbol, decimals)"]
        HS["Holder Snapshot<br/>(top holders, concentration %)"]
        RS["Risk Snapshot<br/>(contract vulnerabilities)"]
        PR["Price Data<br/>(current price, market cap)"]
    end

    INPUT --> IT --> OUTPUT

    style IT fill:#4ECDC4,color:#000
```

**How 0T-Skill uses it:**

| Data Field | Usage in Pipeline | Module |
|---|---|---|
| `holder_snapshot` | Detect holder concentration (e.g., "top holders control 95% of supply") | M4 Risk Filter |
| `risk_snapshot` | Identify contract risks (owner transfer control, slippage manipulation) | M4 Risk Filter |
| `price_data` | Enrich trade pairing with USD values; calculate PnL | M2 |
| `market_cap` | Classify small-cap bias for archetype detection | M7 Archetype |

**Parallel execution**: For each wallet, 0T-Skill selects the top focus tokens (typically 4) and calls `inspect_token` in parallel using `ThreadPoolExecutor`:

```mermaid
flowchart TB
    FT["Focus Tokens<br/>(Top 4 by trade frequency)"]
    
    FT --> T1["inspect_token(PP)"]
    FT --> T2["inspect_token(PIZZA)"]
    FT --> T3["inspect_token(XMONEY)"]
    FT --> T4["inspect_token(GENIUS)"]

    T1 & T2 & T3 & T4 --> AGG["Aggregated Token Profiles"]

    style T1 fill:#4ECDC4,color:#000
    style T2 fill:#4ECDC4,color:#000
    style T3 fill:#4ECDC4,color:#000
    style T4 fill:#4ECDC4,color:#000
```

**Risk filter generation from AVE data:**

Real example from distilled Skill (`meme-hunter-bsc-567a89`):
- `BLOCK: PP can restrict transfers or freeze holders` — from `inspect_token` risk scan
- `WARN: PIZZA top holders control 95.47% of supply` — from `inspect_token` holder snapshot
- `WARN: 哔哔大队 has owner-controlled transfer rules` — from `inspect_token` AI report

---

### 3. `inspect_market`

**Purpose**: Provides market microstructure data including K-line candles, liquidity depth, and trading volume.

```mermaid
flowchart LR
    INPUT["Token: PP<br/>Interval: 1h<br/>Window: 24h"]
    IM["AVE: inspect_market"]

    subgraph OUTPUT["Returned Data"]
        KL["K-line Candles<br/>(OHLCV)"]
        LQ["Liquidity Depth<br/>(USD)"]
        VOL["Volume Profile<br/>(24h)"]
        PR["Price Changes<br/>(1h, 24h %)"]
    end

    INPUT --> IM --> OUTPUT

    style IM fill:#4ECDC4,color:#000
```

**How 0T-Skill uses it:**

| Data Field | Usage in Pipeline | Module |
|---|---|---|
| Price change % (1h, 24h) | Calculate momentum labels (bullish/bearish/neutral) | M3 Market Context |
| Volume data | Compute volume-to-liquidity ratio for entry factor analysis | M4 Entry Factors |
| Liquidity depth | Verify sufficient liquidity before trade execution | M4 → Execution |
| K-line data | Pre-compute volatility regime (low/medium/high/extreme) | M3 Market Context |

**Two-layer processing of market data:**

```mermaid
flowchart TB
    RAW["Raw AVE market data<br/>(16KB+ per token)"]
    
    subgraph FULL["Full Layer — Python computation"]
        F1["OHLCV candle analysis"]
        F2["Volatility regime calculation"]
        F3["Momentum scoring"]
        F4["Volume/liquidity ratios"]
    end
    
    subgraph COMPACT["Compact Layer — LLM input"]
        C1["momentum_label: bullish"]
        C2["volatility_regime: medium"]
        C3["price_change_24h: +5.2%"]
        C4["volume_to_liquidity: 2.3"]
    end

    RAW --> FULL -->|"Compress to ≤0.9KB"| COMPACT

    style RAW fill:#4ECDC4,color:#000
    style COMPACT fill:#FFE66D,color:#000
```

---

### 4. `review_signals`

**Purpose**: Detects on-chain anomaly signals including whale movements, unusual trading activity, and market alerts.

```mermaid
flowchart LR
    INPUT["Chain: bsc<br/>Limit: 20"]
    RS["AVE: review_signals"]

    subgraph OUTPUT["Returned Data"]
        SIG["Signal List<br/>(type, severity, timestamp)"]
        WH["Whale Movements<br/>(large transfers)"]
        AN["Anomalies<br/>(unusual volume, price spikes)"]
    end

    INPUT --> RS --> OUTPUT

    style RS fill:#4ECDC4,color:#000
```

**How 0T-Skill uses it:**

| Data Field | Usage in Pipeline | Module |
|---|---|---|
| Signal list | Count active signals; filter by severity | M4 Signal Filter |
| High-severity signals | Generate anti-pattern warnings in Skill output | M4 → Skill Package |
| Signal context | Inject into compact_input for LLM awareness | M5 LLM Input |

---

### 5. `discover_tokens`

**Purpose**: Token search and discovery utility used for resolving token references during enrichment.

**How 0T-Skill uses it:**
- Auxiliary role: resolves ambiguous token identifiers during the enrichment phase
- Helps map raw transaction addresses to named tokens with metadata

---

## AVE Data Flow Through the Pipeline

```mermaid
flowchart TB
    subgraph COLLECT["M1: AVE Data Collection"]
        direction LR
        IW["inspect_wallet"] --> RAW["Raw wallet data"]
        IT["inspect_token × N"] --> RAW
        IM["inspect_market × N"] --> RAW
        RS["review_signals"] --> RAW
    end

    subgraph PROCESS["M2-M4: Feature Engineering"]
        direction LR
        TP["M2: Trade Pairing<br/>FIFO matching<br/>→ win_rate, pnl_ratio"]
        MC["M3: Market Context<br/>Momentum, volatility<br/>→ regime labels"]
        SF["M4: Risk Filters<br/>Contract risks<br/>→ hard blocks, warnings"]
    end

    subgraph ARCH["M7: Archetype Classification"]
        AC["classify_archetype()<br/>→ meme_hunter<br/>→ degen_sniper<br/>→ scalper<br/>→ swing_trader"]
    end

    subgraph DISTILL["M5: LLM Distillation"]
        CI["compact_input ≤6KB<br/>(All AVE-derived)"]
        LLM["Pi/Kimi Reflection"]
        OUT["profile + strategy<br/>+ execution_intent"]
    end

    subgraph VERIFY["M6: Backtest & Confidence"]
        BT["Signal replay<br/>against AVE historical data"]
        CS["Confidence score"]
    end

    COLLECT --> PROCESS
    PROCESS --> ARCH
    ARCH --> DISTILL
    DISTILL --> VERIFY

    style COLLECT fill:#4ECDC4,color:#000
    style ARCH fill:#FFE66D,color:#000
    style DISTILL fill:#96CEB4,color:#000
```

### AVE Data Budget for LLM

All data sent to the LLM originates from AVE. The system compresses raw AVE responses into a compact format:

| Section | Source AVE Endpoint | Budget |
|---|---|---|
| `wallet_summary` | `inspect_wallet` | 0.3 KB |
| `holdings` (Top 5) | `inspect_wallet` | 0.5 KB |
| `recent_activity` (Top 8) | `inspect_wallet` | 1.0 KB |
| `derived_stats` (M2) | `inspect_wallet` (tx history) | 0.5 KB |
| `market_context` | `inspect_market` | 0.9 KB |
| `signal_context` | `review_signals` + `inspect_token` | 0.5 KB |
| `archetype_context` | All endpoints (M7 aggregation) | 0.3 KB |
| `token_snapshots` (Top 4) | `inspect_token` | 0.6 KB |
| **Total** | | **≤6 KB** |

---

## Real Distilled Skill Examples Using AVE Data

### Example 1: Meme Hunter — `meme-hunter-bsc-567a89`

**What AVE data revealed:**

```mermaid
flowchart TB
    subgraph AVE_DATA["AVE Data Retrieved"]
        D1["inspect_wallet:<br/>High-frequency BSC activity<br/>27 trades/day"]
        D2["inspect_token:<br/>PP, PIZZA, CX — all micro-caps<br/>Multiple risk flags detected"]
        D3["inspect_market:<br/>Volume spikes on meme tokens<br/>Low liquidity pools"]
        D4["review_signals:<br/>Anomalous trading patterns"]
    end

    subgraph DISTILLED["Distillation Result"]
        R1["Archetype: meme_hunter<br/>+ degen_sniper secondary"]
        R2["Confidence: 0.95"]
        R3["Key pattern: small_cap_bias (1.00)<br/>avg first-buy mcap $752K"]
        R4["Risk: PP can restrict transfers<br/>PIZZA holders 95.47% concentrated"]
    end

    AVE_DATA --> DISTILLED

    style AVE_DATA fill:#4ECDC4,color:#000
    style DISTILLED fill:#FFE66D,color:#000
```

**AVE risk detection in action:**
- `inspect_token(PP)` → detected `owner_can_change_transfer_mode_after_initialization` → generated `BLOCK` level warning
- `inspect_token(PIZZA)` → detected top holder concentration at 95.47% → generated `WARN` level alert
- `inspect_token(哔哔大队)` → detected owner-controlled transfer rules → generated `WARN`

### Example 2: Exploratory Profile — `meme-hunter-bsc-c0bc2d`

**What AVE data revealed:**

- `inspect_wallet`: Lower frequency (3.44 trades/day), distributed across multiple tokens
- `inspect_token`: GENIUS, DiamondBalls, ASTER — mixed risk profiles
- Result: `no_stable_archetype` with 0.39 confidence — **the system honestly reports when AVE data doesn't support a confident classification**

This demonstrates that AVE data quality directly drives distillation confidence. Rich, consistent trading history yields high-confidence archetypes; sparse or contradictory data yields honest uncertainty.

---

## AVE as the Single Source of Truth

```mermaid
flowchart TB
    subgraph ALLOWED["✅ AVE Data Consumption"]
        A1["Distillation features"]
        A2["Trade pairing statistics"]
        A3["Market context computation"]
        A4["Risk filter generation"]
        A5["Backtest validation"]
        A6["Archetype classification"]
        A7["LLM compact_input assembly"]
    end

    subgraph BLOCKED["❌ Explicitly Prohibited"]
        B1["Reading market/signal data from OnchainOS"]
        B2["Using OKX PnL data for distillation"]
        B3["Backfilling OKX portfolio data into AVE pipeline"]
        B4["Any data source other than AVE for analysis"]
    end

    AVE["AVE API"] --> ALLOWED
    OKX["OKX OnchainOS"] -.->|"BLOCKED"| ALLOWED

    style ALLOWED fill:#4ECDC4,color:#000
    style BLOCKED fill:#FF6B6B,color:#000
```

**Why AVE-only?**
1. **Data consistency**: Single source prevents metric drift between distillation and validation
2. **Auditability**: Every data point traces back to one provider with full artifact logging
3. **Reproducibility**: Same wallet + same AVE state = same distillation output
4. **Clean separation**: Data plane (AVE) and execution plane (OKX) have zero coupling

---

## Technical Implementation of AVE Integration

### AVE Data Provider Adapter

The `AveDataProviderAdapter` is the unified interface between 0T-Skill and AVE:

```mermaid
flowchart LR
    subgraph ADAPTER["AveDataProviderAdapter"]
        RUN["run(action, payload)"]
        VAL["Pydantic Request Validation"]
        CALL["HTTP Client Call"]
        NORM["Response Normalization"]
        ART["Artifact Persistence"]
    end

    RUN --> VAL --> CALL --> NORM --> ART
    ART --> RESULT["ProviderActionResult"]

    style ADAPTER fill:#4ECDC4,color:#000
```

**Key design properties:**
- **Request validation**: Every AVE call goes through Pydantic model validation (`InspectWalletRequest`, `InspectTokenRequest`, etc.)
- **Response normalization**: All AVE responses are wrapped in a unified `ProviderActionResult` envelope with `ok`, `summary`, `response`, `error`, `artifacts`
- **Artifact persistence**: Every AVE call's request and response is saved as `{action}-{request_id}.json` for full traceability
- **Error classification**: HTTP errors, validation errors, and internal errors are categorized into standard error codes

### AVE Data Service

The AVE Data Service is a local HTTP server that proxies and normalizes AVE API calls:

| Endpoint | Maps to AVE Skill |
|---|---|
| `/v1/discover_tokens` | `discover_tokens` |
| `/v1/inspect_token` | `inspect_token` |
| `/v1/inspect_market` | `inspect_market` |
| `/v1/inspect_wallet` | `inspect_wallet` |
| `/v1/review_signals` | `review_signals` |

### AVE Cloud Skill Reference

The project vendors the official AVE Cloud Skill package (`vendor/ave_cloud_skill/`) which includes:
- `ave_data_rest.py` — REST API client implementation
- Reference documentation (data-api-doc, response-contract, token-conventions)
- Docker deployment configuration

---

## Summary

| AVE Skill | What It Provides | How 0T-Skill Uses It |
|---|---|---|
| `inspect_wallet` | Wallet profile, holdings, tx history | Entry point; trade pairing source; session window analysis |
| `inspect_token` | Token details, holder data, risk scan | Risk filtering; archetype small-cap detection; PnL enrichment |
| `inspect_market` | K-lines, liquidity, volume | Momentum/volatility context; entry factor volume analysis |
| `review_signals` | On-chain anomalies | Signal context for LLM; anti-pattern generation |
| `discover_tokens` | Token search/resolution | Auxiliary token identifier resolution |

AVE Skills are not just data endpoints — they are the **foundation of the entire distillation intelligence**. Every statistical feature, every archetype classification, every LLM insight, and every confidence score ultimately traces back to AVE data.
