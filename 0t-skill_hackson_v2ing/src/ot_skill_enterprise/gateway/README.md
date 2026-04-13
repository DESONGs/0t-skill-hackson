# Gateway Module

This module is a legacy compat bridge.

It now delegates to the provider layer under `ot_skill_enterprise.providers` and exists to keep the old `ave-data-gateway` entrypoints working while the provider architecture evolves.

## What belongs here

- Legacy action runner wrappers
- Backward-compatible bridge code
- Minimal glue for old skill entrypoints

## What does not belong here

- Provider contracts
- Registry logic
- Provider-specific adapters
- Analysis or workflow logic
