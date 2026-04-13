from __future__ import annotations

import argparse
import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..registry import RuntimeRegistry, build_default_runtime_registry
from .adapter import PiRuntimeAdapter, build_pi_runtime_adapter


def _project_root_from_file() -> Path:
    return Path(__file__).resolve().parents[4]


def _run_command(command: list[str], *, cwd: Path) -> None:
    subprocess.run(command, cwd=str(cwd), check=True)


def _runtime_entrypoint(runtime_root: Path) -> Path:
    return runtime_root / "upstream" / "coding_agent" / "src" / "ot_runtime_entry.ts"


def _built_artifact(runtime_root: Path) -> Path:
    return runtime_root / "dist" / "pi-runtime.mjs"


def _node_modules_dir(runtime_root: Path) -> Path:
    return runtime_root / "node_modules"


def _has_built_artifact(runtime_root: Path) -> bool:
    return _built_artifact(runtime_root).is_file()


def _has_dev_entrypoint(runtime_root: Path) -> bool:
    return _runtime_entrypoint(runtime_root).is_file()


def _default_runtime_launcher(runtime_root: Path) -> list[str]:
    return [os.getenv("OT_PI_NODE", "node"), str(_built_artifact(runtime_root))]


def _dev_runtime_launcher(runtime_root: Path) -> list[str]:
    return [os.getenv("OT_PI_NODE", "node"), "--import", "tsx", str(_runtime_entrypoint(runtime_root))]


def _build_bundle(runtime_root: Path) -> Path:
    artifact = _built_artifact(runtime_root)
    artifact.parent.mkdir(parents=True, exist_ok=True)
    _run_command([os.getenv("OT_PI_NPM", "npm"), "run", "build:ot-runtime"], cwd=runtime_root)
    return artifact


@dataclass(frozen=True)
class PiRuntimeBootstrap:
    project_root: Path
    src_root: Path
    workspace_root: Path
    runtime_root: Path
    upstream_roots: dict[str, Path]
    descriptor: dict[str, Any]
    runtime_registry: RuntimeRegistry
    adapter: PiRuntimeAdapter
    compatibility_notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def runtime_entrypoint(self) -> Path:
        return _runtime_entrypoint(self.runtime_root)

    @property
    def built_artifact(self) -> Path:
        return _built_artifact(self.runtime_root)

    def default_run_command(self) -> list[str]:
        return _default_runtime_launcher(self.runtime_root)

    def dev_run_command(self) -> list[str]:
        return _dev_runtime_launcher(self.runtime_root)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_root": str(self.project_root),
            "src_root": str(self.src_root),
            "workspace_root": str(self.workspace_root),
            "runtime_root": str(self.runtime_root),
            "upstream_roots": {key: str(value) for key, value in self.upstream_roots.items()},
            "descriptor": dict(self.descriptor),
            "runtime_registry": self.runtime_registry.describe(),
            "runtime_entrypoint": str(self.runtime_entrypoint),
            "built_artifact": str(self.built_artifact),
            "run_instructions": {
                "default": {
                    "mode": "built",
                    "command": self.default_run_command(),
                    "entrypoint": str(self.built_artifact),
                    "note": "This is the default runtime path.",
                },
                "dev": {
                    "mode": "dev",
                    "command": self.dev_run_command(),
                    "entrypoint": str(self.runtime_entrypoint),
                    "note": "tsx fallback is allowed only in dev mode.",
                },
            },
            "compatibility_notes": list(self.compatibility_notes),
        }


def build_pi_runtime_bootstrap(
    *,
    root: Path | None = None,
    workspace_dir: Path | None = None,
    runtime_root: Path | None = None,
) -> PiRuntimeBootstrap:
    project_root = Path(root).expanduser().resolve() if root is not None else _project_root_from_file()
    src_root = project_root / "src"
    resolved_workspace = Path(workspace_dir).expanduser().resolve() if workspace_dir is not None else project_root / ".ot-workspace"
    resolved_runtime_root = Path(runtime_root).expanduser().resolve() if runtime_root is not None else project_root / "vendor" / "pi_runtime"
    upstream_root = resolved_runtime_root / "upstream"
    upstream_roots = {
        "coding_agent": upstream_root / "coding_agent" / "src",
        "agent": upstream_root / "agent" / "src",
        "ai": upstream_root / "ai" / "src",
        "tui": upstream_root / "tui" / "src",
    }
    adapter = build_pi_runtime_adapter(runtime_root=resolved_runtime_root, workspace_dir=resolved_workspace)
    registry = build_default_runtime_registry(adapter=adapter)
    return PiRuntimeBootstrap(
        project_root=project_root,
        src_root=src_root,
        workspace_root=resolved_workspace,
        runtime_root=resolved_runtime_root,
        upstream_roots=upstream_roots,
        descriptor=adapter.descriptor.model_dump(mode="json"),
        runtime_registry=registry,
        adapter=adapter,
        compatibility_notes=(
            "Pi is an embedded in-repo runtime, not a product identity.",
            "default runtime execution uses the bundled built artifact.",
            "tsx is allowed only as a dev-mode fallback.",
        ),
    )


def inspect_pi_runtime_bootstrap(
    *,
    root: Path | None = None,
    workspace_dir: Path | None = None,
    runtime_root: Path | None = None,
) -> dict[str, Any]:
    bootstrap = build_pi_runtime_bootstrap(root=root, workspace_dir=workspace_dir, runtime_root=runtime_root)
    runtime_root_path = bootstrap.runtime_root
    payload = bootstrap.to_dict()
    payload.update(
        {
            "stage": "inspect",
            "node_modules_present": _node_modules_dir(runtime_root_path).exists(),
            "entrypoint_present": _has_dev_entrypoint(runtime_root_path),
            "built_artifact_present": _has_built_artifact(runtime_root_path),
            "stages": {
                "inspect": "summarize runtime layout and launch contract",
                "install": "hydrate local dependencies into vendor/pi_runtime/node_modules",
                "build": "bundle ot_runtime_entry.ts into dist/pi-runtime.mjs",
                "verify": "check the built artifact and launch contract",
            },
        }
    )
    return payload


def install_pi_runtime_bootstrap(
    *,
    root: Path | None = None,
    workspace_dir: Path | None = None,
    runtime_root: Path | None = None,
) -> dict[str, Any]:
    bootstrap = build_pi_runtime_bootstrap(root=root, workspace_dir=workspace_dir, runtime_root=runtime_root)
    runtime_root_path = bootstrap.runtime_root
    _run_command([os.getenv("OT_PI_NPM", "npm"), "install", "--no-fund", "--no-audit"], cwd=runtime_root_path)
    payload = bootstrap.to_dict()
    payload.update(
        {
            "stage": "install",
            "status": "completed",
            "node_modules_present": _node_modules_dir(runtime_root_path).exists(),
            "built_artifact_present": _has_built_artifact(runtime_root_path),
        }
    )
    return payload


def build_pi_runtime_bootstrap_artifact(
    *,
    root: Path | None = None,
    workspace_dir: Path | None = None,
    runtime_root: Path | None = None,
) -> dict[str, Any]:
    bootstrap = build_pi_runtime_bootstrap(root=root, workspace_dir=workspace_dir, runtime_root=runtime_root)
    runtime_root_path = bootstrap.runtime_root
    if not _node_modules_dir(runtime_root_path).exists():
        raise FileNotFoundError("vendor/pi_runtime/node_modules is missing; run install first")
    artifact = _build_bundle(runtime_root_path)
    payload = bootstrap.to_dict()
    payload.update(
        {
            "stage": "build",
            "status": "completed",
            "built_artifact_present": artifact.exists(),
            "built_artifact_size": artifact.stat().st_size if artifact.exists() else 0,
        }
    )
    return payload


def verify_pi_runtime_bootstrap(
    *,
    root: Path | None = None,
    workspace_dir: Path | None = None,
    runtime_root: Path | None = None,
) -> dict[str, Any]:
    bootstrap = build_pi_runtime_bootstrap(root=root, workspace_dir=workspace_dir, runtime_root=runtime_root)
    runtime_root_path = bootstrap.runtime_root
    artifact = _built_artifact(runtime_root_path)
    if not artifact.is_file():
        raise FileNotFoundError(f"built artifact missing: {artifact}")
    _run_command([os.getenv("OT_PI_NODE", "node"), "--check", str(artifact)], cwd=runtime_root_path)
    payload = bootstrap.to_dict()
    payload.update(
        {
            "stage": "verify",
            "status": "completed",
            "built_artifact_present": True,
            "built_artifact_size": artifact.stat().st_size,
            "launch_contract": {
                "default": bootstrap.default_run_command(),
                "dev": bootstrap.dev_run_command(),
            },
        }
    )
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ot-runtime-bootstrap", description="Inspect, install, build, and verify the embedded Pi runtime")
    parser.add_argument("--workspace-dir", default=None)
    parser.add_argument("--runtime-root", default=None)
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("inspect")
    subparsers.add_parser("install")
    subparsers.add_parser("build")
    subparsers.add_parser("verify")
    args = parser.parse_args(argv)

    workspace_dir = Path(args.workspace_dir).expanduser().resolve() if args.workspace_dir else None
    runtime_root = Path(args.runtime_root).expanduser().resolve() if args.runtime_root else None

    if args.command in (None, "inspect"):
        payload = inspect_pi_runtime_bootstrap(workspace_dir=workspace_dir, runtime_root=runtime_root)
    elif args.command == "install":
        payload = install_pi_runtime_bootstrap(workspace_dir=workspace_dir, runtime_root=runtime_root)
    elif args.command == "build":
        payload = build_pi_runtime_bootstrap_artifact(workspace_dir=workspace_dir, runtime_root=runtime_root)
    elif args.command == "verify":
        payload = verify_pi_runtime_bootstrap(workspace_dir=workspace_dir, runtime_root=runtime_root)
    else:
        raise ValueError(f"unknown command: {args.command}")

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
