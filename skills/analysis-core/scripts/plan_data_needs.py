from __future__ import annotations

import argparse
import os
from pathlib import Path

from _shared import bootstrap, emit_result, load_payload


bootstrap()

from ot_skill_enterprise.analysis import plan_data_needs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="analysis-core plan_data_needs")
    parser.add_argument("--input-json", dest="input_json", default=None)
    parser.add_argument("--topic", default=None)
    parser.add_argument("--objective", default=None)
    parser.add_argument("--scope", default=None)
    parser.add_argument("--request-id", dest="request_id", default=None)
    parser.add_argument("--workspace-hint", dest="workspace_hint", default=None)
    parser.add_argument("--question", action="append", default=None)
    parser.add_argument("--focus-domain", dest="focus_domain", action="append", default=None)
    parser.add_argument("--workspace-dir", dest="workspace_dir", default=None)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    payload = load_payload(args, ["topic", "objective", "scope", "request_id", "workspace_hint"])
    if args.question:
        payload["questions"] = args.question
    if args.focus_domain:
        payload["focus_domains"] = args.focus_domain
    workspace_dir = args.workspace_dir or os.environ.get("WORKSPACE_DIR") or str(Path.cwd())
    emit_result(plan_data_needs(payload, workspace_dir=workspace_dir))


if __name__ == "__main__":
    main()
