"""Project-local import helpers shared by the main agent integration layer."""

from pathlib import Path


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]

