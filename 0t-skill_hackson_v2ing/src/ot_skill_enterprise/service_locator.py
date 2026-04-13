"""Project-local import helpers shared by control plane and service layers."""

from __future__ import annotations

import os
from pathlib import Path


def project_root(root: Path | None = None) -> Path:
    return Path(root) if root is not None else Path(__file__).resolve().parents[2]


def src_root(root: Path | None = None) -> Path:
    return project_root(root) / "src"


def control_plane_root(root: Path | None = None) -> Path:
    return src_root(root) / "ot_skill_enterprise" / "control_plane"


def bin_root(root: Path | None = None) -> Path:
    return project_root(root) / "bin"


def workspace_root(default: str = ".ot-workspace", root: Path | None = None) -> Path:
    workspace = os.getenv("OT_WORKSPACE_DIR") or os.getenv("OT_DEFAULT_WORKSPACE") or default
    return (project_root(root) / workspace).resolve()
