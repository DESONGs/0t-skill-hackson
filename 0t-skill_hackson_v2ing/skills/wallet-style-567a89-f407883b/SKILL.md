---
name: wallet-style-567a89-f407883b
description: This BSC wallet behaves like a meme hunter trader with degen sniper secondary
  traits. Its execution tempo is high frequency rotation, its risk posture is conservative,
  and its conviction profile is distributed basket. It rotates most often through
  PP and is most active during us session. The archetype signal is rated at 0.95 confidence.
version: 1.0.0
owner: mainagent
status: experimental
tags:
- generated
- candidate
- script
- wallet_style
- archetype:meme_hunter
metadata:
  candidate_id: candidate-c264d6d5a6fc
  runtime_session_id: style-session-style-job-06e02dce03
  source_run_id: style-distill-run-style-job-06e02dce03
  source_evaluation_id: eval-79d3aec7be05
  target_skill_name: wallet-style-567a89
  target_skill_kind: wallet_style
  candidate_type: script
  trading_archetype:
    primary_archetype: meme_hunter
    secondary_archetypes:
    - degen_sniper
    behavioral_patterns:
    - 'small_cap_bias (1.00): first_buy_avg_mcap_usd=752390.34; small_cap_trade_ratio=1.00'
    behavioral_pattern_labels:
    - 'small_cap_bias (1.00): first_buy_avg_mcap_usd=752390.34; small_cap_trade_ratio=1.00'
    archetype_confidence: 0.95
    archetype_evidence_summary: '[''trades_per_day=27.00'']'
    archetype_token_preference:
    - PP
    summary: 'meme hunter trader; secondary patterns: degen sniper; behavioral patterns:
      small cap bias (1.00): first buy avg mcap usd=752390.34; small cap trade ratio=1.00;
      token preference: PP; confidence 0.95; evidence: [''trades_per_day=27.00'']'
  archetype_primary: meme_hunter
  archetype_summary: 'meme hunter trader; secondary patterns: degen sniper; behavioral
    patterns: small cap bias (1.00): first buy avg mcap usd=752390.34; small cap trade
    ratio=1.00; token preference: PP; confidence 0.95; evidence: [''trades_per_day=27.00'']'
---
# wallet-style-567a89

This BSC wallet behaves like a meme hunter trader with degen sniper secondary traits. Its execution tempo is high frequency rotation, its risk posture is conservative, and its conviction profile is distributed basket. It rotates most often through PP and is most active during us session. The archetype signal is rated at 0.95 confidence.

## Wallet Style Signature

- Wallet: 0xbac453b9b7f53b35ac906b641925b2f5f2567a89
- Chain: bsc
- Style label: meme_hunter
- Execution tempo: high-frequency rotation
- Risk appetite: conservative
- Conviction profile: distributed basket
- Stablecoin bias: fully deployed

## Trading Archetype

- Trader class: meme hunter.
- Persona: meme hunter trader; secondary patterns: degen sniper; behavioral patterns: small cap bias (1.00): first buy avg mcap usd=752390.34; small cap trade ratio=1.00; token preference: PP; confidence 0.95; evidence: ['trades_per_day=27.00']
- Secondary archetypes: degen sniper
- Behavioral patterns: small cap bias (1.00): first buy avg mcap usd=752390.34; small cap trade ratio=1.00
- Archetype confidence: 0.95
- Evidence summary: ['trades_per_day=27.00']
- Token preference: PP

## Execution Rules

- Honor archetype signal: meme_hunter.
- Secondary archetypes observed: degen_sniper.
- Behavioral patterns observed: small_cap_bias (1.00): first_buy_avg_mcap_usd=752390.34;...
- Evidence signals: trades_per_day=27.00.
- Bias decisions toward sell setups instead of all-market participation.

## Anti Patterns

- WARN: PIZZA top holders control 95.47% of supply
- WARN: 哔哔大队 has owner-controlled transfer rules
- WARN: 哔哔大队 top holders control 93.23% of supply
- BLOCK: PP can restrict transfers or freeze holders
- WARN: PP has owner-controlled transfer rules
- BLOCK: CX can restrict transfers or freeze holders
- Additional risk notes are preserved in references/style_profile.json.

## Runtime Notes

- This package is generated for the hackathon wallet-style distillation flow.
- Promotion copies the package into local skills and makes it discoverable immediately.
