from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml

from ot_skill_enterprise.enterprise_bridge.paths import ave_cloud_skill_root, ensure_bridge_import_paths, project_root, vendor_root


def _load_vendor_parse_skill_md():
    ensure_bridge_import_paths()
    from skill_contract.parsers.skill_md import parse_skill_md

    return parse_skill_md


@dataclass(frozen=True)
class SkillPackageSummary:
    skill_name: str
    root: Path
    source: str
    description: str
    version: str | None
    owner: str | None
    skill_type: str
    default_action: str | None
    actions: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_name": self.skill_name,
            "root": str(self.root),
            "source": self.source,
            "description": self.description,
            "version": self.version,
            "owner": self.owner,
            "skill_type": self.skill_type,
            "default_action": self.default_action,
            "actions": list(self.actions),
        }


@dataclass(frozen=True)
class LocalSkillInstallResult:
    install_id: str
    skill_name: str
    skill_root: Path
    install_root: Path
    files: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "install_id": self.install_id,
            "skill_name": self.skill_name,
            "skill_root": str(self.skill_root),
            "install_root": str(self.install_root),
            "files": list(self.files),
        }


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _summarize_local_package(package_root: Path, *, source: str) -> SkillPackageSummary:
    manifest = _read_json(package_root / "manifest.json")
    actions = _read_yaml(package_root / "actions.yaml")
    skill_md = package_root / "SKILL.md"
    description = str(manifest.get("description", "")).strip()
    if not description and skill_md.exists():
        lines = [line.strip() for line in skill_md.read_text(encoding="utf-8").splitlines() if line.strip()]
        description = lines[1] if len(lines) > 1 and lines[0].startswith("#") else (lines[0] if lines else "")
    action_ids = tuple(
        str(item.get("id", "")).strip()
        for item in actions.get("actions", [])
        if isinstance(item, dict) and str(item.get("id", "")).strip()
    )
    default_action = str(actions.get("default_action") or action_ids[0]) if action_ids else None
    skill_name = str(manifest.get("name") or package_root.name)
    version = str(manifest.get("version")) if manifest.get("version") is not None else None
    owner = str(manifest.get("owner")) if manifest.get("owner") is not None else None
    skill_type = str(manifest.get("kind") or "skill")
    return SkillPackageSummary(
        skill_name=skill_name,
        root=Path(package_root),
        source=source,
        description=description,
        version=version,
        owner=owner,
        skill_type=skill_type,
        default_action=default_action,
        actions=action_ids,
    )


def _discover_package_roots(base_dir: Path, *, require_actions: bool = True) -> list[Path]:
    if not base_dir.exists():
        return []
    roots: list[Path] = []
    for candidate in sorted(base_dir.iterdir()):
        if not candidate.is_dir():
            continue
        has_skill_md = (candidate / "SKILL.md").exists()
        has_actions_yaml = (candidate / "actions.yaml").exists()
        if has_skill_md and (has_actions_yaml or not require_actions):
            roots.append(candidate)
    return roots


def discover_local_skill_packages(root: Path | None = None) -> list[SkillPackageSummary]:
    project = Path(root) if root is not None else project_root()
    skill_root = project / "skills"
    packages: list[SkillPackageSummary] = []
    for package_root in _discover_package_roots(skill_root, require_actions=True):
        packages.append(_summarize_local_package(package_root, source="local"))
    return packages


def discover_ave_cloud_skill_snapshots(root: Path | None = None) -> list[SkillPackageSummary]:
    parse_skill_md = _load_vendor_parse_skill_md()
    project = Path(root) if root is not None else project_root()
    skill_root = project / "vendor" / "skill_enterprise" / "upstream" / "ave-cloud-skill" / "skills"
    summaries: list[SkillPackageSummary] = []
    for package_root in _discover_package_roots(skill_root, require_actions=False):
        skill_md_path = package_root / "SKILL.md"
        try:
            md = parse_skill_md(skill_md_path)
            name = md.frontmatter.name
            description = md.frontmatter.description
            owner = md.frontmatter.owner
        except Exception:
            name = package_root.name
            description = ""
            owner = None
        summaries.append(
            SkillPackageSummary(
                skill_name=str(name),
                root=package_root,
                source="vendor:ave-cloud-skill",
                description=str(description),
                version=None,
                owner=str(owner) if owner is not None else None,
                skill_type="script",
                default_action=None,
                actions=(),
            )
        )
    return summaries


class EnterpriseBridge:
    """Single-root bridge for the 0t skill enterprise workspace."""

    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root is not None else project_root()

    @classmethod
    def from_project_root(cls, root: Path | None = None) -> "EnterpriseBridge":
        return cls(root=root)

    @property
    def vendor_root(self) -> Path:
        return vendor_root()

    @property
    def runtime_src(self) -> Path:
        return self.vendor_root / "src"

    @property
    def local_skill_root(self) -> Path:
        return self.root / "skills"

    def discover_local_skill_packages(self) -> list[SkillPackageSummary]:
        return discover_local_skill_packages(self.root)

    def discover_ave_cloud_skill_snapshots(self) -> list[SkillPackageSummary]:
        return discover_ave_cloud_skill_snapshots(self.root)

    def runtime_entrypoint(self) -> dict[str, Any]:
        return {
            "project_root": str(self.root),
            "vendor_root": str(self.vendor_root),
            "runtime_src": str(self.runtime_src),
            "local_skills": [summary.to_dict() for summary in self.discover_local_skill_packages()],
            "vendor_ave_cloud_skills": [
                summary.to_dict() for summary in self.discover_ave_cloud_skill_snapshots()
            ],
        }

    def load_local_skill_package(self, skill_name: str) -> Any:
        for summary in self.discover_local_skill_packages():
            if summary.skill_name == skill_name:
                return _summarize_local_package(summary.root, source=summary.source)
        raise KeyError(f"Unknown local skill: {skill_name}")

    def materialize_local_skill_install(
        self,
        skill_name: str,
        *,
        install_root: Path | None = None,
    ) -> Any:
        matched_summary = None
        for candidate in self.discover_local_skill_packages():
            if candidate.skill_name == skill_name:
                matched_summary = candidate
                break
        if matched_summary is None:
            raise KeyError(f"Unknown local skill: {skill_name}")

        root = install_root or (self.root / ".enterprise-installs")
        install_dir = root / f"{matched_summary.skill_name}-{matched_summary.version or 'local'}"
        if install_dir.exists():
            shutil.rmtree(install_dir)
        shutil.copytree(matched_summary.root, install_dir)
        return LocalSkillInstallResult(
            install_id=install_dir.name,
            skill_name=matched_summary.skill_name,
            skill_root=matched_summary.root,
            install_root=install_dir,
            files=tuple(str(path.relative_to(install_dir)) for path in sorted(install_dir.rglob("*")) if path.is_file()),
        )

    def list_skill_names(self) -> list[str]:
        return [summary.skill_name for summary in self.discover_local_skill_packages()]


def runtime_entrypoint(root: Path | None = None) -> dict[str, Any]:
    return EnterpriseBridge.from_project_root(root).runtime_entrypoint()
