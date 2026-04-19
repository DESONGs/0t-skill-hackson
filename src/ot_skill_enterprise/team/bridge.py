from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .adapters import AdapterLaunchEnvelope, build_builtin_adapters
from .protocol import TeamProtocolBundle
from .store import TeamStateStore


@dataclass(slots=True)
class AgentTeamBridge:
    store: TeamStateStore
    protocol: TeamProtocolBundle
    adapters: dict[str, Any] = field(default_factory=build_builtin_adapters)

    def report_capabilities(self) -> dict[str, Any]:
        return {
            "supported_adapters": [adapter.model_dump(mode="json") for adapter in self.adapters.values()],
            "protocol_root": str(self.protocol.root),
            "entrypoint": str(self.protocol.entrypoint_path),
        }

    def render_handoff_markdown(
        self,
        *,
        session_id: str,
        adapter_id: str,
        role_id: str,
        workflow_id: str,
        module_id: str,
        workspace_id: str,
        target_skill: str,
        work_item_id: str,
        title: str,
        kind: str,
        dependencies: list[str],
        input_refs: list[str],
        result_path: str,
    ) -> str:
        role_doc = self.protocol.role_docs.get(role_id, "").strip()
        role_title = role_id.replace("-", " ").title()
        return "\n".join(
            [
                f"# {role_title} Handoff",
                "",
                f"- adapter: `{adapter_id}`",
                f"- session_id: `{session_id}`",
                f"- work_item_id: `{work_item_id}`",
                f"- workspace_id: `{workspace_id}`",
                f"- workflow: `{workflow_id}`",
                f"- module: `{module_id}`",
                f"- target_skill: `{target_skill}`",
                "",
                "## Read First",
                f"- [`AGENTS.md`]({self.protocol.root.parent / 'AGENTS.md'})",
                f"- [`ENTRYPOINT.md`]({self.protocol.entrypoint_path})",
                "",
                "## Role Contract",
                role_doc,
                "",
                "## Work Item",
                f"- title: {title}",
                f"- kind: {kind}",
                f"- dependencies: {', '.join(dependencies) if dependencies else 'none'}",
                f"- input_refs: {', '.join(input_refs) if input_refs else 'none'}",
                "",
                "## Submit Result",
                "Submit the worker-bridge response contract through:",
                f"`0t team submit-work --session-id {session_id} --work-item-id {work_item_id} --payload-file <result.json>`",
                "",
                f"Expected result path: `{result_path}`",
            ]
        ).strip() + "\n"

    def build_launch_envelope(self, *, adapter_id: str, handoff_markdown: str) -> dict[str, str]:
        if adapter_id not in self.adapters:
            raise ValueError(f"unsupported adapter: {adapter_id}")
        return AdapterLaunchEnvelope(
            adapter_id=adapter_id,
            display_name=self.adapters[adapter_id].display_name,
            handoff_markdown=handoff_markdown,
        ).as_dict()

    def write_handoff(self, session_id: str, name: str, content: str) -> str:
        return self.store.write_handoff(session_id, name, content)
