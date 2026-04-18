from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from ot_skill_enterprise.shared.contracts.common import utc_now

from .adapters import AdapterLaunchEnvelope, build_builtin_adapters
from .models import OptimizationSession, TeamAdapterSession, WorkItem
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

    def _format_handoff(self, session: OptimizationSession, work_item: WorkItem, adapter_id: str) -> str:
        role_doc = self.protocol.role_docs.get(work_item.role_id, "")
        brief_path = self.store.brief_path(session.session_id)
        result_path = self.store.work_items_dir(session.session_id) / f"{work_item.work_item_id}.result.json"
        role_title = work_item.role_id.replace("-", " ").title()
        return "\n".join(
            [
                f"# {role_title} Handoff",
                "",
                f"- adapter: `{adapter_id}`",
                f"- session_id: `{session.session_id}`",
                f"- work_item_id: `{work_item.work_item_id}`",
                f"- workspace_id: `{session.workspace_id}`",
                f"- workflow: `{session.workflow_id}`",
                f"- module: `{session.module_id}`",
                f"- target_skill: `{session.subject_id}`",
                "",
                "## Read First",
                f"- [`AGENTS.md`]({self.protocol.root.parent / 'AGENTS.md'})",
                f"- [`ENTRYPOINT.md`]({self.protocol.entrypoint_path})",
                f"- session brief: `{brief_path}`",
                "",
                "## Role Contract",
                role_doc.strip(),
                "",
                "## Work Item",
                f"- title: {work_item.title}",
                f"- kind: {work_item.kind}",
                f"- dependencies: {', '.join(work_item.depends_on) if work_item.depends_on else 'none'}",
                f"- input_refs: {', '.join(work_item.input_refs) if work_item.input_refs else 'none'}",
                "",
                "## Submit Result",
                "Write a JSON payload and hand it back through:",
                f"`ot-team submit-work --session-id {session.session_id} --work-item-id {work_item.work_item_id} --payload-file <result.json> --agent-id {adapter_id}`",
                "",
                f"Expected result path: `{result_path}`",
            ]
        ).strip() + "\n"

    def start_agent_session(self, session: OptimizationSession, work_item: WorkItem, *, adapter_id: str | None = None) -> dict[str, Any]:
        selected = adapter_id or session.adapter_family
        if selected not in self.adapters:
            raise ValueError(f"unsupported adapter: {selected}")
        if work_item.role_id not in set(self.adapters[selected].supported_roles):
            raise ValueError(f"adapter {selected} does not support role {work_item.role_id}")
        handoff = self._format_handoff(session, work_item, selected)
        handoff_name = f"{work_item.role_id}-{work_item.work_item_id}.md"
        handoff_path = self.store.write_handoff(session.session_id, handoff_name, handoff)
        payload = TeamAdapterSession(
            agent_session_id=f"agent-{uuid4().hex[:12]}",
            session_id=session.session_id,
            work_item_id=work_item.work_item_id,
            adapter_id=selected,
            role_id=work_item.role_id,
            status="ready",
            handoff_path=handoff_path,
            metadata={"created_at": utc_now().isoformat()},
        )
        self.store.save_agent_session(payload)
        work_item.status = "in_progress"
        work_item.instructions_path = handoff_path
        work_item.updated_at = utc_now()
        self.store.save_work_item(work_item)
        return {
            "agent_session": payload.model_dump(mode="json"),
            "launch": AdapterLaunchEnvelope(
                adapter_id=selected,
                display_name=self.adapters[selected].display_name,
                handoff_markdown=handoff,
            ).as_dict(),
        }

    def poll_status(self, session_id: str) -> dict[str, Any]:
        session = self.store.get_session(session_id)
        if session is None:
            raise ValueError(f"unknown session: {session_id}")
        work_items = self.store.list_work_items(session_id)
        counts: dict[str, int] = {}
        for item in work_items:
            counts[item.status] = counts.get(item.status, 0) + 1
        return {
            "session_id": session_id,
            "status": session.status,
            "work_item_counts": counts,
            "agent_sessions": [item.model_dump(mode="json") for item in self.store.list_agent_sessions(session_id)],
        }

    def fetch_result(self, session_id: str, work_item_id: str) -> dict[str, Any] | None:
        return self.store.load_work_item_result(session_id, work_item_id)

    def stop_session(self, session_id: str) -> dict[str, Any]:
        session = self.store.get_session(session_id)
        if session is None:
            raise ValueError(f"unknown session: {session_id}")
        session.status = "paused"
        session.updated_at = utc_now()
        self.store.save_session(session)
        return {"session_id": session_id, "status": session.status}
