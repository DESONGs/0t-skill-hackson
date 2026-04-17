from __future__ import annotations

from typing import Any, Mapping


def normalize_feedback_target(payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
    body = dict(payload or {})
    return {
        "run_id": body.get("run_id"),
        "agent_id": body.get("agent_id"),
        "skill_id": body.get("skill_id"),
        "flow_id": body.get("flow_id"),
        "provider_id": body.get("provider_id"),
        "metadata": dict(body.get("metadata") or {}),
    }

