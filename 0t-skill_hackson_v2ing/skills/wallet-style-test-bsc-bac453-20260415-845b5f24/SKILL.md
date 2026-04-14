---
name: wallet-style-test-bsc-bac453-20260415-845b5f24
description: High-velocity BSC day-trader deploying same-minute bursts into microcaps
  like XMONEY and PP, pyramid-averaging into positions with zero stablecoin cushion
  and holding through -85% drawdowns.
version: 1.0.0
owner: mainagent
status: experimental
tags:
- generated
- candidate
- script
- wallet_style
metadata:
  candidate_id: candidate-3fb55dcc6e6c
  runtime_session_id: style-session-style-job-5781fa7f86
  source_run_id: style-distill-run-style-job-5781fa7f86
  source_evaluation_id: eval-2c2cccc0f8e1
  target_skill_name: wallet-style-test-bsc-bac453-20260415
  target_skill_kind: wallet_style
  candidate_type: script
---
# wallet-style-test-bsc-bac453-20260415

High-velocity BSC day-trader deploying same-minute bursts into microcaps like XMONEY and PP, pyramid-averaging into positions with zero stablecoin cushion and holding through -85% drawdowns.

## Wallet Style Signature

- Wallet: 0xbac453b9b7f53b35ac906b641925b2f5f2567a89
- Chain: bsc
- Style label: BSC Microcap Day-Scalper
- Execution tempo: same-minute-burst day-trading (~3.8h average hold)
- Risk appetite: extreme microcap degen
- Conviction profile: diamond-hands pyramid accumulator
- Stablecoin bias: zero-stablecoin fully deployed

## Execution Rules

- Enter in same-minute bursts during US session
- Use WBNB as primary quote pair
- Pyramid-average into existing positions (avg 2.95 splits)
- Hold through >80% drawdowns
- Maintain zero stablecoin allocation

## Anti Patterns

- No stablecoin risk-off buffer
- Accumulating tokens with freeze/transfer restrictions (PP)
- Holding through extreme drawdowns rather than cutting losses
- BLOCK: PP can restrict transfers or freeze holders
- WARN: PP has owner-controlled transfer rules
- WARN: 哔哔大队 has owner-controlled transfer rules
- Additional risk notes are preserved in references/style_profile.json.

## Runtime Notes

- This package is generated for the hackathon wallet-style distillation flow.
- Promotion copies the package into local skills and makes it discoverable immediately.
