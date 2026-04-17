from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def bootstrap() -> Path:
    root = Path(__file__).resolve().parents[3]
    src = root / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    return root


def load_payload(args: Any, keys: list[str]) -> dict[str, Any]:
    if getattr(args, "input_json", None):
        return json.loads(args.input_json)
    payload: dict[str, Any] = {}
    for key in keys:
        value = getattr(args, key, None)
        if value is not None:
            payload[key] = value
    return payload


def emit_result(result: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(result, ensure_ascii=False))
    sys.stdout.write("\n")
