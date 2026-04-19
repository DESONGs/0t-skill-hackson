from __future__ import annotations

import argparse
import json
from pathlib import Path

from ot_skill_enterprise.enterprise_bridge import EnterpriseBridge, runtime_entrypoint


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="0t bridge", description="Single-root 0T bridge")
    parser.add_argument("--root", type=Path, default=None, help="Project root to inspect")
    subparsers = parser.add_subparsers(dest="command", required=False)

    subparsers.add_parser("discover", help="List local and vendored skills")
    materialize = subparsers.add_parser("materialize", help="Materialize a local skill install")
    materialize.add_argument("skill_name", help="Local skill name")
    subparsers.add_parser("entrypoint", help="Print the runtime entrypoint payload")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    bridge = EnterpriseBridge.from_project_root(args.root)

    if args.command in {None, "entrypoint"}:
        payload = runtime_entrypoint(args.root)
    elif args.command == "discover":
        payload = {
            "project_root": str(bridge.root),
            "local_skills": [summary.to_dict() for summary in bridge.discover_local_skill_packages()],
            "vendor_ave_cloud_skills": [
                summary.to_dict() for summary in bridge.discover_ave_cloud_skill_snapshots()
            ],
        }
    elif args.command == "materialize":
        install = bridge.materialize_local_skill_install(args.skill_name)
        payload = install.to_dict() if hasattr(install, "to_dict") else dict(install)
    else:
        parser.error(f"unknown command: {args.command}")
        return 2

    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
