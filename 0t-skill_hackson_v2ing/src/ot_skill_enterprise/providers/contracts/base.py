from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass(slots=True)
class ProviderActionRequest:
    provider: str
    action: str
    payload: dict[str, Any] = field(default_factory=dict)
    workspace_dir: Path | None = None
    request_id: str | None = None
    context: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ProviderActionResult:
    ok: bool
    provider: str
    action: str
    request_id: str
    summary: str
    request: dict[str, Any] = field(default_factory=dict)
    response: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    artifacts: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "provider": self.provider,
            "action": self.action,
            "request_id": self.request_id,
            "summary": self.summary,
            "request": self.request,
            "response": self.response,
            "error": self.error,
            "meta": self.meta,
            "artifacts": self.artifacts,
        }


class ProviderAdapter(Protocol):
    name: str
    supported_actions: tuple[str, ...]

    def run(
        self,
        action_name: str,
        payload: dict[str, Any],
        *,
        workspace_dir: Path | None = None,
        request_id: str | None = None,
    ) -> ProviderActionResult: ...
