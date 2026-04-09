from __future__ import annotations

import argparse
import os
from pathlib import Path

from _shared import bootstrap, emit_result, load_payload


bootstrap()

from ot_skill_enterprise.analysis import synthesize_evidence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="analysis-core synthesize_evidence")
    parser.add_argument("--input-json", dest="input_json", default=None)
    parser.add_argument("--request-id", dest="request_id", default=None)
    parser.add_argument("--workspace-dir", dest="workspace_dir", default=None)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    payload = load_payload(args, ["request_id"])
    workspace_dir = args.workspace_dir or os.environ.get("WORKSPACE_DIR") or str(Path.cwd())
    emit_result(synthesize_evidence(payload, workspace_dir=workspace_dir))


if __name__ == "__main__":
    main()
