from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterable

from ot_skill_enterprise.service_locator import project_root


def vendor_root() -> Path:
    return project_root() / "vendor" / "skill_enterprise"


def vendor_src_root() -> Path:
    return vendor_root() / "src"


def ave_cloud_skill_root() -> Path:
    return vendor_root() / "upstream" / "ave-cloud-skill"


def ensure_bridge_import_paths() -> list[str]:
    """Add the vendored upstream runtime and local project src to sys.path."""

    paths = [vendor_src_root(), project_root() / "src"]
    resolved: list[str] = []
    for path in paths:
        if path.exists():
            path_str = str(path)
            if path_str not in sys.path:
                sys.path.insert(0, path_str)
            resolved.append(path_str)
    return resolved


def ensure_directories(paths: Iterable[Path]) -> None:
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)
