from __future__ import annotations

import argparse
import json
from pathlib import Path

from ot_skill_enterprise.enterprise_bridge.cli import main as bridge_main
from ot_skill_enterprise.root_runtime import _load_inputs, run_preset_workflow


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ot-enterprise", description="0T single-root runtime entrypoint")
    subparsers = parser.add_subparsers(dest="command", required=True)

    bridge = subparsers.add_parser("bridge", help="Delegate to the enterprise bridge CLI")
    bridge.add_argument("bridge_args", nargs=argparse.REMAINDER)

    workflow = subparsers.add_parser("workflow-run", help="Run a local workflow preset from the 0t root")
    workflow.add_argument("--preset", required=True)
    workflow.add_argument("--workspace-dir", default=".ot-workspace")
    workflow.add_argument("--inputs", default=None, help="Inline JSON inputs")
    workflow.add_argument("--inputs-file", default=None, help="Path to a JSON file containing inputs")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "bridge":
        return bridge_main(args.bridge_args)
    if args.command == "workflow-run":
        payload = _load_inputs(args.inputs, args.inputs_file)
        result = run_preset_workflow(
            args.preset,
            payload,
            workspace_dir=Path(args.workspace_dir).expanduser().resolve(),
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
