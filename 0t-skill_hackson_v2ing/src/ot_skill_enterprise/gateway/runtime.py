from __future__ import annotations

from pathlib import Path
from typing import Any

from ot_skill_enterprise.providers.ave.adapter import ACTION_NAMES
from ot_skill_enterprise.providers.ave.compat import build_ave_gateway_runner


class GatewayActionRunner:
    """Legacy gateway wrapper that now delegates to the provider compat layer."""

    action_name: str

    def __init__(
        self,
        action_name: str,
        *,
        client: Any | None = None,
        workspace_dir: Path | None = None,
    ) -> None:
        self.action_name = action_name
        self._runner = build_ave_gateway_runner(client=client, workspace_dir=workspace_dir)

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.action_name not in ACTION_NAMES:
            raise ValueError(f"unsupported action: {self.action_name}")
        return self._runner.run(self.action_name, payload)


def run_action(
    action_name: str,
    payload: dict[str, Any],
    *,
    client: Any | None = None,
    workspace_dir: Path | str | None = None,
) -> dict[str, Any]:
    runner = build_ave_gateway_runner(
        client=client,
        workspace_dir=Path(workspace_dir) if workspace_dir is not None else None,
    )
    if action_name not in ACTION_NAMES:
        raise ValueError(f"unsupported action: {action_name}")
    return runner.run(action_name, payload)
