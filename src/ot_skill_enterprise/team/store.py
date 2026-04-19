from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class TeamStateStore:
    workspace_root: Path

    def __post_init__(self) -> None:
        self.workspace_root = Path(self.workspace_root).expanduser().resolve()
        self.workspace_root.mkdir(parents=True, exist_ok=True)

    @property
    def sessions_root(self) -> Path:
        path = self.workspace_root / "runtime-sessions"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def session_workspace_dir(self, session_id: str) -> Path:
        path = self.sessions_root / session_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def session_dir(self, session_id: str) -> Path:
        path = self.session_workspace_dir(session_id) / "workflow-kernel"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def session_path(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "session.json"

    def team_dir(self, session_id: str) -> Path:
        path = self.session_dir(session_id) / "team"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def handoffs_dir(self, session_id: str) -> Path:
        path = self.team_dir(session_id) / "handoffs"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def handoff_path(self, session_id: str, name: str) -> Path:
        return self.handoffs_dir(session_id) / name

    def write_handoff(self, session_id: str, name: str, content: str) -> str:
        path = self.handoff_path(session_id, name)
        path.write_text(content, encoding="utf-8")
        return str(path)
