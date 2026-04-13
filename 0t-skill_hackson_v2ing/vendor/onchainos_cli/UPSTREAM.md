# Vendored onchainos CLI

- Source repository: `https://github.com/okx/onchainos-skills.git`
- Upstream commit: `04153f634ac837afdbe01ae04fab75f511ee673a`
- Vendored subtree: `cli/`
- Local destination: `vendor/onchainos_cli/upstream/cli/`

## Usage Boundary

This vendored subtree is only used as the execution plane for:

- `wallet`
- `security`
- `swap`
- `gateway`

The following capability groups are intentionally not wired into 0T data paths:

- `market`
- `signal`
- `portfolio`
- `tracker`
- `defi`
- any PnL or leaderboard endpoints

## Provenance

This copy is intentionally preserved as a full CLI subtree so execution behavior can be audited and upgraded without introducing a second on-chain data path into the distillation pipeline.
