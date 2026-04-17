---
name: ave-data-gateway
version: 1.0.0
description: |
  Compat AVE data gateway skill for 0T skill enterprise.
metadata:
  openclaw:
    requires:
      env:
        - AVE_DATA_SERVICE_URL
        - WORKSPACE_DIR
---

# ave-data-gateway

Compat data gateway skill for the enterprise provider stack.

## Responsibilities

- Call the provider compat bridge under `ot_skill_enterprise.providers`.
- Translate stable gateway inputs into service requests.
- Write one JSON artifact per action into `WORKSPACE_DIR/data/`.
- Return runner-readable JSON with `summary` and `artifacts`.

## Public Actions

- `discover_tokens`
- `inspect_token`
- `inspect_market`
- `inspect_wallet`
- `review_signals`

## Non-goals

- Analysis or report writing.
- Trading or wallet signing.
- WebSocket streaming.
- Evolving prompts or policies.
- Acting as the canonical provider layer.

## Runtime Notes

- The wrapper stdout is a single JSON object.
- Each action wrapper can accept either CLI flags or `--input-json`.
- Action artifacts are stored under `WORKSPACE_DIR/data/`.
- The wrapper uses `AVE_DATA_SERVICE_URL` and `AVE_API_KEY` when present.
- The skill is compat-only and should not be treated as the provider source of truth.
