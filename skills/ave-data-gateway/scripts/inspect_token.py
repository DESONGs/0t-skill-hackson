from __future__ import annotations

import argparse

from _shared import bootstrap, emit_result, load_payload


bootstrap()

from ot_skill_enterprise.gateway import run_action


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ave-data-gateway inspect_token")
    parser.add_argument("--input-json", dest="input_json", default=None)
    parser.add_argument("--token", default=None)
    parser.add_argument("--address", default=None)
    parser.add_argument("--chain", default=None)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    payload = load_payload(args, ["token", "address", "chain"])
    emit_result(run_action("inspect_token", payload))


if __name__ == "__main__":
    main()
