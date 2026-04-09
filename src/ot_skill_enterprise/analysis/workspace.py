from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from ot_skill_enterprise.shared.contracts import ArtifactRef


def _workspace_root(workspace_dir: Path | str | None = None) -> Path:
    root = Path(workspace_dir or Path.cwd()).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


@dataclass(frozen=True)
class AnalysisWorkspace:
    root: Path

    @classmethod
    def from_path(cls, workspace_dir: Path | str | None = None) -> "AnalysisWorkspace":
        return cls(root=_workspace_root(workspace_dir))

    @property
    def analysis_dir(self) -> Path:
        return self.root / "analysis"

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def reports_dir(self) -> Path:
        return self.root / "reports"

    def ensure(self) -> "AnalysisWorkspace":
        self.analysis_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        return self

    def plan_path(self) -> Path:
        return self.analysis_dir / "plan.json"

    def findings_path(self) -> Path:
        return self.analysis_dir / "findings.json"

    def report_json_path(self) -> Path:
        return self.reports_dir / "analysis-report.json"

    def report_md_path(self) -> Path:
        return self.reports_dir / "analysis-report.md"

    def data_artifacts(self) -> list[Path]:
        if not self.data_dir.exists():
            return []
        return sorted(path for path in self.data_dir.glob("*.json") if path.is_file())

    def artifact_ref(self, *, artifact_id: str, kind: str, uri: Path, label: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        return ArtifactRef(
            artifact_id=artifact_id,
            kind=kind,
            uri=str(uri),
            label=label,
            metadata=metadata or {},
        ).model_dump(mode="json")

    def read_json(self, path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    def write_json(self, path: Path, payload: Any) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path


def iter_workspace_json(workspace: AnalysisWorkspace, paths: Iterable[Path] | None = None) -> list[tuple[Path, dict[str, Any]]]:
    selected = list(paths) if paths is not None else workspace.data_artifacts()
    loaded: list[tuple[Path, dict[str, Any]]] = []
    for path in selected:
        try:
            loaded.append((path, workspace.read_json(path)))
        except Exception:
            continue
    return loaded
