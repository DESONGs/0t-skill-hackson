---
name: ave-data-gateway
version: 1.0.0
description: |
  Stable AVE data gateway skill for 0T skill enterprise.
metadata:
  openclaw:
    requires:
      env:
        - AVE_DATA_SERVICE_URL
        - WORKSPACE_DIR
---

# ave-data-gateway

Stable data gateway skill for the enterprise analysis stack.

## Responsibilities

- Call the local project `AveDataServiceClient`.
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

## Runtime Notes

- The wrapper stdout is a single JSON object.
- Each action wrapper can accept either CLI flags or `--input-json`.
- Action artifacts are stored under `WORKSPACE_DIR/data/`.
- The wrapper uses `AVE_DATA_SERVICE_URL` and `AVE_API_KEY` when present.
