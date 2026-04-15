# 0T-Skill Collaboration Guide

## Repository Scope

This is the public release repository for the 0T-Skill wallet-style distillation system.

### Directory Layout

```
0t-skill-hackson-public/
├── README.md                        # Project overview & AVE integration highlights
├── CONFIGURATION.md                 # Environment setup & dependency guide
├── agent.md                         # This file — collaboration rules
├── docs/                            # Public documentation
│   ├── PROJECT_INTRODUCTION.md      # Project introduction & example skills
│   ├── ARCHITECTURE.md              # System architecture & agent framework
│   ├── INTEGRATION.md               # AVE + OKX + Skill-OS integration
│   └── AVE_SKILLS.md                # AVE Skills documentation (hackathon)
└── 0t-skill_hackson_v2ing/          # Main project
    ├── src/ot_skill_enterprise/     # Core business logic
    ├── services/                    # AVE data service
    ├── vendor/                      # Vendored dependencies
    ├── skills/                      # Distilled skill packages
    ├── tests/                       # Test suite
    ├── scripts/                     # Bootstrap & startup
    └── frontend/                    # Dashboard
```

## Boundary Rules

### Data Boundary — AVE Only

- All distillation, backtest, market context, and feature extraction uses **AVE exclusively**
- OnchainOS must **never** serve as a data source for distillation, backtest, signal analysis, or PnL calculation
- No code in `style_distillation/`, `reflection/`, `backtesting/`, or `market_context/` may read from OnchainOS data paths

### Execution Boundary — OKX OnchainOS Only

- Wallet login, signing, security scan, dry-run, and broadcast use **OnchainOS CLI exclusively**
- Execution is only triggered through the `execute` action of a compiled Skill package
- Direct chain execution from `primary`, `style_distillation`, or `reflection` is prohibited

### Skill Contract

| Script | Network | Responsibility |
|--------|---------|---------------|
| `primary` | `allow_network: false` | Recommendation + trade plan generation |
| `execute` | `allow_network: true` | Consumes trade plan + execution_intent via OnchainOS CLI |

## Security

- No hardcoded secrets, API keys, or credentials in the repository
- All sensitive configuration via environment variables (see `CONFIGURATION.md`)
- `.env`, `.venv`, `.ot-workspace` are gitignored
- Live execution requires explicit human approval (`requires_explicit_approval: true`)

## Documentation

| Document | Purpose |
|----------|---------|
| `README.md` | Project overview, quick start, AVE integration |
| `docs/AVE_SKILLS.md` | Detailed AVE Skills usage documentation |
| `docs/PROJECT_INTRODUCTION.md` | Background, flow, example skills |
| `docs/ARCHITECTURE.md` | Agent framework, pipeline architecture |
| `docs/INTEGRATION.md` | Three-component integration guide |
| `CONFIGURATION.md` | Environment variables & setup |
