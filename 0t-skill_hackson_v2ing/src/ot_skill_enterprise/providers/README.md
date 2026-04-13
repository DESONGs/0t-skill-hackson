# Providers

This package hosts the thin provider integration layer for 0T Skill Enterprise.

## Layers

- `contracts/`: shared provider request/result contracts
- `registry/`: action-to-provider routing
- `ave/`: AVE adapter and AVE-specific compat helpers
- `compat/`: legacy gateway bridge used by `ot_skill_enterprise.gateway`

## Design Goal

Keep external agents and higher-level workflows provider-agnostic while AVE remains a replaceable data adapter.
