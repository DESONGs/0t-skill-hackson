from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from ot_skill_enterprise.shared.clients import HttpClientError

from ..registry import ProviderRegistry


def _workspace_dir(workspace_dir: Path | None = None) -> Path:
    root = workspace_dir or Path.cwd()
    root = root.expanduser().resolve()
    (root / "data").mkdir(parents=True, exist_ok=True)
    return root


def _artifact_ref(action_name: str, request_id: str, artifact_path: Path) -> dict[str, Any]:
    return {
        "artifact_id": f"{action_name}-{request_id}",
        "kind": "json",
        "uri": str(artifact_path),
        "label": f"{action_name} artifact",
        "metadata": {"subdir": "data"},
    }


def _normalize_error(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, HttpClientError):
        details: dict[str, Any] = {}
        if exc.status_code is not None:
            details["status_code"] = exc.status_code
        if exc.body is not None:
            details["body"] = exc.body
        if exc.payload is not None:
            details["payload"] = exc.payload
        return {"code": "UPSTREAM_HTTP_ERROR", "message": str(exc), "details": details}
    if isinstance(exc, ValidationError):
        return {"code": "VALIDATION_ERROR", "message": str(exc), "details": {"errors": exc.errors()}}
    if isinstance(exc, ValueError):
        return {"code": "VALIDATION_ERROR", "message": str(exc), "details": {}}
    return {"code": "INTERNAL_ERROR", "message": str(exc), "details": {}}


@dataclass(slots=True)
class GatewayCompatRunner:
    registry: ProviderRegistry
    workspace_dir: Path | None = None

    def run(self, action_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        workspace = _workspace_dir(self.workspace_dir)
        request_id = uuid4().hex
        provider = self.registry.resolve(action_name)
        summary = f"{action_name} failed"
        request_dump: Any = payload
        response_dump: Any = None
        response_error: dict[str, Any] | None = None
        response_meta: dict[str, Any] = {}
        response_ok = False
        try:
            result = provider.run(action_name, payload, workspace_dir=workspace, request_id=request_id)
            summary = result.summary
            request_dump = result.request
            response_dump = result.response
            response_error = result.error
            response_meta = result.meta
            response_ok = result.ok
        except Exception as exc:  # pragma: no cover - defensive bridge path
            response_error = _normalize_error(exc)

        artifact_body = {
            "action": action_name,
            "request_id": request_id,
            "summary": summary,
            "request": request_dump,
            "response": response_dump,
            "error": response_error,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        artifact_path = workspace / "data" / f"{action_name}-{request_id}.json"
        artifact_path.write_text(json.dumps(artifact_body, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            "ok": bool(response_ok) if response_error is None else False,
            "action": action_name,
            "operation": action_name,
            "request_id": request_id,
            "summary": summary,
            "artifacts": [_artifact_ref(action_name, request_id, artifact_path)],
            "meta": response_meta,
            "error": response_error,
        }


def run_action(
    action_name: str,
    payload: dict[str, Any],
    *,
    registry: ProviderRegistry | None = None,
    workspace_dir: Path | str | None = None,
) -> dict[str, Any]:
    if registry is None:
        from ..ave.compat import build_ave_provider_registry

        registry = build_ave_provider_registry()
    runner = GatewayCompatRunner(
        registry=registry,
        workspace_dir=Path(workspace_dir) if workspace_dir is not None else None,
    )
    return runner.run(action_name, payload)
