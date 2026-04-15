# pi_runtime vendor snapshot

## Sources

- Upstream repo snapshot: `pi-mono_raw`
- Git commit anchor: `3b7448d156aab5af1e21fd9ab45d19e4f10865a8`
- Package versions at anchor: `0.66.1`
  - `packages/coding-agent`
  - `packages/agent`
  - `packages/ai`
  - `packages/tui`

## Scope

Only the following upstream code and runtime assets were copied into this vendor area:

- `upstream/coding_agent/src`
- `upstream/agent/src`
- `upstream/ai/src`
- `upstream/tui/src`
- `upstream/coding_agent/src/modes/interactive/assets`
- `upstream/coding_agent/src/modes/interactive/theme`
- `upstream/coding_agent/src/core/export-html`
- workspace `package.json` files required to run the vendored source via local `npm` workspaces

No build output, no lockfiles, and no upstream examples/tests/docs were imported.

## Rules

- Keep changes confined to `vendor/pi_runtime`.
- Preserve upstream file layout under each `src` tree.
- Treat the upstream commit above as the version anchor for future diffs.
- If more upstream code is needed, copy only the minimum additional files required and update this README.
- Do not overwrite unrelated local changes outside this vendor area.

## Bootstrap

Use the Python bootstrap module in `src/ot_skill_enterprise/runtime/pi/bootstrap.py` for the Stage A flow:

- `inspect` to summarize the layout and launch contract
- `install` to hydrate `vendor/pi_runtime/node_modules`
- `build` to bundle `upstream/coding_agent/src/ot_runtime_entry.ts` into `dist/pi-runtime.mjs`
- `verify` to syntax-check the built artifact with Node

Default runtime execution now targets the built artifact:

- `node vendor/pi_runtime/dist/pi-runtime.mjs`

Development fallback is explicit and dev-only:

- `tsx vendor/pi_runtime/upstream/coding_agent/src/ot_runtime_entry.ts`

## Execution Modes

The vendored `Pi` runtime now exposes two execution modes through the same built artifact:

- `stub runtime path`
  - default path for generic runtime smoke runs and local integration testing
- `reflection execution mode`
  - selected when `metadata.pi_mode=reflection` or a `reflection_job` payload is present
  - used by `wallet_style_reflection_review`
  - expected to return structured JSON review output

The reflection mode is implemented locally in:

- `upstream/coding_agent/src/ot_reflection_mode.ts`

This is a `Pi`-native extension inside the vendor snapshot. It does not shell out to, import, or depend on `Hermes`.

## Notes

- This snapshot is source-first, but the default launch path is the built artifact.
- The `tsx` fallback is only for dev mode and should not be treated as the primary runtime path.
- Some optional upstream build artifacts are still intentionally omitted.
