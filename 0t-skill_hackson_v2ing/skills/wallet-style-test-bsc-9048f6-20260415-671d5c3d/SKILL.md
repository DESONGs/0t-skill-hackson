---
name: wallet-style-test-bsc-9048f6-20260415-671d5c3d
description: High-frequency BSC scalper specializing in micro-cap memecoins via WBNB
  quote pairs. Exhibits same-minute burst execution, 21-second average holds, pyramid
  averaging into positions, and...
version: 1.0.0
owner: mainagent
status: experimental
tags:
- generated
- candidate
- script
- wallet_style
metadata:
  candidate_id: candidate-67b61320848c
  runtime_session_id: style-session-style-job-f584d6e375
  source_run_id: style-distill-run-style-job-f584d6e375
  source_evaluation_id: eval-96e4b89b96bd
  target_skill_name: wallet-style-test-bsc-9048f6-20260415
  target_skill_kind: wallet_style
  candidate_type: script
---
# wallet-style-test-bsc-9048f6-20260415

High-frequency BSC scalper specializing in micro-cap memecoins via WBNB quote pairs. Exhibits same-minute burst execution, 21-second average holds, pyramid averaging into positions, and...

## Wallet Style Signature

- Wallet: 0x9048f6c683abb0eba156797fd699fe662b4dbfef
- Chain: bsc
- Style label: BSC Memecoin Scalper
- Execution tempo: Ultra-short scalp (same-minute burst)
- Risk appetite: Aggressive memecoin scalper
- Conviction profile: Momentum-driven tape reader with diamond-hands drawdown tolerance
- Stablecoin bias: Zero stablecoin allocation; fully risk-on

## Execution Rules

- Enter in same-minute bursts during europe-overlap windows
- Use WBNB as primary quote token for all swaps
- Scale into winners via pyramid averaging pattern
- Hold average 21 seconds; classify as scalping
- Accept drawdowns up to -61% without forced exit (diamond-hands tolerance)

## Anti Patterns

- Holding zero stablecoin reserves for drawdown defense
- Trading tokens with owner transfer control and slippage manipulation flags
- Average 21-second holds increase MEV and front-run exposure
- WARN: PPAI has owner-controlled transfer rules
- WARN: PPAI top holders control 100.0% of supply
- WARN: TОKЕNМAХXІNG has owner-controlled transfer rules
- Additional risk notes are preserved in references/style_profile.json.

## Runtime Notes

- This package is generated for the hackathon wallet-style distillation flow.
- Promotion copies the package into local skills and makes it discoverable immediately.
