from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .pi.bootstrap import prepare_pi_runtime_bootstrap


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _in_container() -> bool:
    return Path("/.dockerenv").exists() or _truthy(os.environ.get("OT_IN_DOCKER_COMPOSE"))


def _ave_dockerfile(root: Path) -> Path:
    return root / "docker" / "Dockerfile.ave"


def _docker_image_present(image: str) -> bool:
    completed = subprocess.run(
        ["docker", "image", "inspect", image],
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode == 0


def prepare_runtime_environment(
    *,
    workspace_dir: Path | None = None,
    verify_pi: bool = True,
) -> dict[str, Any]:
    root = _project_root()
    resolved_workspace = Path(workspace_dir).expanduser().resolve() if workspace_dir is not None else (root / ".ot-workspace").resolve()
    pi_payload = prepare_pi_runtime_bootstrap(root=root, workspace_dir=resolved_workspace, verify=verify_pi)

    ave_payload: dict[str, Any] = {
        "enabled": _truthy(os.environ.get("AVE_USE_DOCKER")),
        "image": "ave-cloud",
        "performed": False,
        "skipped_reason": None,
    }
    if not ave_payload["enabled"]:
        ave_payload["skipped_reason"] = "AVE_USE_DOCKER disabled"
    elif _in_container():
        ave_payload["skipped_reason"] = "container runtime skips host Docker image preparation"
    else:
        dockerfile = _ave_dockerfile(root)
        ave_payload["dockerfile"] = str(dockerfile)
        if not shutil.which("docker"):
            raise RuntimeError("docker is required when AVE_USE_DOCKER=true")
        if _docker_image_present("ave-cloud"):
            ave_payload["skipped_reason"] = "ave-cloud image already present"
        else:
            subprocess.run(
                ["docker", "build", "-f", str(dockerfile), "-t", "ave-cloud", str(root)],
                check=True,
            )
            ave_payload["performed"] = True
        ave_payload["image_present"] = _docker_image_present("ave-cloud")

    return {
        "status": "completed",
        "workspace_dir": str(resolved_workspace),
        "pi_runtime": pi_payload,
        "ave_docker": ave_payload,
    }
