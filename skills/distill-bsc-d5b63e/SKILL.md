---
name: distill-bsc-d5b63e
description: Ultra-active BSC scalper trading microcap tokens in sub-15-minute bursts
  using WBNB. Deploys tiny clip sizes (~0.05% of NAV) across pyramid-style entries,
  tolerates extreme drawdowns, and maintains zero stablecoin buffer.
version: 1.0.0
owner: mainagent
status: experimental
tags:
- generated
- candidate
- script
- wallet_style
metadata:
  candidate_id: candidate-aed7431af7d6
  runtime_session_id: style-session-style-job-5c83be0b24
  source_run_id: style-distill-run-style-job-5c83be0b24
  source_evaluation_id: eval-066c1ffc27f1
  target_skill_name: Wallet Distill BSC d5b63e
  target_skill_kind: wallet_style
  candidate_type: script
---
# Wallet Distill BSC d5b63e

Ultra-active BSC scalper trading microcap tokens in sub-15-minute bursts using WBNB. Deploys tiny clip sizes (~0.05% of NAV) across pyramid-style entries, tolerates extreme drawdowns, and maintains zero stablecoin buffer.

## Wallet Style Signature

- Wallet: 0xd5b63edd7cdf4c23718cc8a6a83e312dc8ae3fe1
- Chain: bsc
- Style label: Micro-Position Memecoin Scalper
- Execution tempo: Ultra-high (same-minute bursts, ~14 min average hold)
- Risk appetite: Aggressive micro-sizing with macro drawdown tolerance
- Conviction profile: Low per-trade conviction, high volume, pyramiding
- Stablecoin bias: None (0% stablecoin allocation, WBNB-native)

## Execution Rules

- Enter in same-minute bursts using WBNB quote
- Pyramid into positions across 3+ clips
- Hold time target under 15 minutes
- Recycle all proceeds back into WBNB; no stablecoin parking
- Accept drawdowns >80% before exit

## Anti Patterns

- No stop losses (diamond-hands through -87% drawdowns)
- Zero stablecoin risk buffer
- Oversized trade frequency relative to position edge

## Runtime Notes

- This package is generated for the hackathon wallet-style distillation flow.
- Promotion copies the package into local skills and makes it discoverable immediately.
