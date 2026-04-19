from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ot_skill_enterprise.nextgen.kernel_bridge import WorkflowKernelBridge, build_nextgen_kernel_bridge

from .adapters import AdapterLaunchEnvelope, build_builtin_adapters
from .models import WorkflowDefinition
from .protocol import TeamProtocolBundle, load_team_protocol_bundle
from .store import TeamStateStore


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "item"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    return [dict(item) for item in value] if isinstance(value, list) else []


def _string(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


@dataclass(slots=True)
class AgentTeamService:
    project_root: Path
    workspace_root: Path
    protocol: TeamProtocolBundle
    store: TeamStateStore
    kernel_bridge: WorkflowKernelBridge
    adapters: dict[str, Any]

    def _workflow(self, workflow_id: str) -> WorkflowDefinition:
        try:
            return self.protocol.resolve_workflow(workflow_id)
        except KeyError as exc:
            raise ValueError(f"unsupported 0t team workflow: {workflow_id}") from exc

    def _workflow_requires_execution(self, workflow_id: str) -> bool:
        return workflow_id in {"autonomous_research", "approval_convergence"}

    def _workspace_config_path(self, workspace_id: str) -> Path:
        return self.workspace_root / "workspaces" / workspace_id / "workflow-config.json"

    def _workspace_adapter_config(self, workspace_id: str) -> dict[str, str]:
        path = self._workspace_config_path(workspace_id)
        payload = _read_json(path)
        candidates = [
            payload,
            _mapping(payload.get("adapters")),
            _mapping(payload.get("adapter_ids")),
            _mapping(payload.get("workflow")),
            _mapping(_mapping(payload.get("workflow")).get("adapter_ids")),
            _mapping(_mapping(payload.get("workflow")).get("adapters")),
            _mapping(payload.get("nextgen")),
            _mapping(_mapping(payload.get("nextgen")).get("adapter_ids")),
            _mapping(_mapping(payload.get("nextgen")).get("adapters")),
        ]
        resolved: dict[str, str] = {}
        for candidate in candidates:
            if not candidate:
                continue
            data_source = _string(candidate.get("data_source") or candidate.get("data_source_adapter_id"))
            execution = _string(candidate.get("execution") or candidate.get("execution_adapter_id"))
            if data_source and "data_source" not in resolved:
                resolved["data_source"] = data_source
            if execution and "execution" not in resolved:
                resolved["execution"] = execution
        return resolved

    def _resolve_kernel_adapters(
        self,
        *,
        workflow_id: str,
        workspace_id: str,
        data_source_adapter_id: str | None = None,
        execution_adapter_id: str | None = None,
    ) -> dict[str, str]:
        workspace_adapters = self._workspace_adapter_config(workspace_id)
        resolved_data_source = _string(data_source_adapter_id) or workspace_adapters.get("data_source")
        resolved_execution = _string(execution_adapter_id) or workspace_adapters.get("execution")
        if not resolved_data_source:
            raise ValueError(
                "0t team start requires an explicit --data-source-adapter or a workspace workflow-config.json with data_source/data_source_adapter_id"
            )
        if self._workflow_requires_execution(workflow_id) and not resolved_execution:
            raise ValueError(
                "0t team start requires an explicit --execution-adapter or a workspace workflow-config.json with execution/execution_adapter_id"
            )
        return {
            "data_source": resolved_data_source,
            "execution": resolved_execution or "",
        }

    def _resolve_skill_path(self, skill_ref: str) -> Path:
        raw = Path(skill_ref).expanduser()
        candidates = [
            raw,
            self.project_root / "skills" / skill_ref,
        ]
        for candidate in candidates:
            manifest = candidate / "manifest.json" if candidate.is_dir() else candidate
            if manifest.is_file():
                return manifest.resolve()
        raise ValueError(
            "0t team start currently requires a wallet-style skill package path or slug with a manifest.json"
        )

    def _resolve_skill_context(self, skill_ref: str) -> dict[str, Any]:
        manifest_path = self._resolve_skill_path(skill_ref)
        manifest = _read_json(manifest_path)
        metadata = _mapping(manifest.get("metadata"))
        profile = _mapping(metadata.get("wallet_style_profile"))
        wallet = str(profile.get("wallet") or "").strip()
        chain = str(profile.get("chain") or "").strip().lower()
        if not wallet or not chain:
            raise ValueError(
                "0t team start currently supports wallet-style skills only; metadata.wallet_style_profile.wallet and chain are required"
            )
        skill_name = str(manifest.get("name") or manifest_path.parent.name).strip() or manifest_path.parent.name
        return {
            "skill_name": skill_name,
            "skill_slug": manifest_path.parent.name,
            "skill_path": str(manifest_path.parent.resolve()),
            "wallet": wallet,
            "chain": chain,
            "manifest_path": str(manifest_path),
        }

    def _session_paths(self, session_id: str) -> dict[str, Path]:
        kernel_root = self.store.session_dir(session_id)
        return {
            "kernel_root": kernel_root,
            "session": self.store.session_path(session_id),
            "team": kernel_root / "team.json",
            "recommendation": kernel_root / "recommendation.json",
            "approval": kernel_root / "approval.json",
            "approval_convergence": kernel_root / "approval-convergence.json",
            "result": kernel_root / "result.json",
            "work_items": kernel_root / "work-items.json",
            "journal": kernel_root / "journal.jsonl",
        }

    def _kernel_docs(self, session_id: str) -> dict[str, dict[str, Any]]:
        paths = self._session_paths(session_id)
        result_payload = _read_json(paths["result"])
        approval_convergence = _read_json(paths["approval_convergence"])
        approval_summary = _read_json(paths["approval"])
        return {
            "session": _read_json(paths["session"]),
            "team": _read_json(paths["team"]),
            "recommendation": _read_json(paths["recommendation"]),
            "approval": approval_convergence or approval_summary,
            "approval_summary": approval_summary,
            "work_items": _read_json(paths["work_items"]),
            "result": _mapping(result_payload.get("final_result")) if result_payload else {},
        }

    def _project_session(
        self,
        session_doc: dict[str, Any],
        final_result: dict[str, Any],
        *,
        team_doc: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        request = _mapping(session_doc.get("request"))
        team = _mapping(team_doc)
        request_metadata = _mapping(request.get("metadata"))
        active_workflow_id = session_doc.get("workflow_id")
        entry_workflow_id = (
            request_metadata.get("entry_workflow_id")
            or request_metadata.get("requested_workflow_id")
            or active_workflow_id
        )
        session_payload = {
            "session_id": session_doc.get("session_id"),
            "workspace_id": request.get("workspace_id") or request.get("workspace"),
            "workflow_id": entry_workflow_id,
            "active_workflow_id": active_workflow_id,
            "status": session_doc.get("status"),
            "action": session_doc.get("action"),
            "runtime_id": session_doc.get("runtime_id"),
            "run_id": session_doc.get("run_id"),
            "project_root": session_doc.get("project_root"),
            "workspace_dir": session_doc.get("workspace_dir"),
            "session_workspace": session_doc.get("session_workspace"),
            "kernel_root": session_doc.get("kernel_root"),
            "started_at": session_doc.get("started_at"),
            "updated_at": session_doc.get("updated_at"),
            "objective": request.get("objective"),
            "request": request,
            "supplemental_inputs": _mapping(session_doc.get("supplemental_inputs")),
            "metadata": {
                **_mapping(session_doc.get("metadata")),
                "workflow_title": _mapping(session_doc.get("metadata")).get("workflow_title"),
            },
        }
        if team:
            adapter_family = _string(team.get("adapter_family"))
            if adapter_family:
                session_payload["adapter_family"] = adapter_family
        return session_payload

    def _project_work_items(
        self,
        team_doc: dict[str, Any],
        *,
        session_id: str | None = None,
        work_items_doc: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        kernel_items = {
            item.get("work_item_id"): dict(item)
            for item in _list_of_dicts(_mapping(work_items_doc).get("work_items"))
            if item.get("work_item_id")
        }
        payload: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for item in _list_of_dicts(team_doc.get("work_items")):
            work_item_id = item.get("id")
            kernel_item = kernel_items.get(work_item_id, {})
            seen_ids.add(str(work_item_id))
            payload.append(
                {
                    "work_item_id": work_item_id,
                    "session_id": session_id,
                    "role_id": item.get("role"),
                    "title": kernel_item.get("title") or item.get("title"),
                    "kind": item.get("kind"),
                    "status": kernel_item.get("status") or item.get("status"),
                    "adapter_id": team_doc.get("adapter_family"),
                    "depends_on": list(kernel_item.get("depends_on") or item.get("depends_on") or []),
                    "input_refs": list(item.get("input_refs") or []),
                    "instructions_path": item.get("instructions_path"),
                    "result_path": kernel_item.get("response_path") or item.get("result_path"),
                    "metadata": {
                        **dict(item.get("metadata") or {}),
                        **{key: value for key, value in kernel_item.items() if key not in {"title", "status", "depends_on", "response_path"}},
                    },
                    "created_at": kernel_item.get("created_at") or item.get("created_at"),
                    "updated_at": kernel_item.get("updated_at") or item.get("updated_at"),
                }
            )
        for work_item_id, kernel_item in kernel_items.items():
            if str(work_item_id) in seen_ids:
                continue
            payload.append(
                {
                    "work_item_id": work_item_id,
                    "session_id": session_id,
                    "role_id": None,
                    "title": kernel_item.get("title"),
                    "kind": kernel_item.get("plugin_id"),
                    "status": kernel_item.get("status"),
                    "adapter_id": team_doc.get("adapter_family"),
                    "depends_on": list(kernel_item.get("depends_on") or []),
                    "input_refs": [],
                    "instructions_path": kernel_item.get("request_path"),
                    "result_path": kernel_item.get("response_path"),
                    "metadata": dict(kernel_item),
                    "created_at": kernel_item.get("created_at"),
                    "updated_at": kernel_item.get("updated_at"),
                }
            )
        return payload

    def _project_agent_sessions(self, team_doc: dict[str, Any], *, session_id: str | None = None) -> list[dict[str, Any]]:
        payload: list[dict[str, Any]] = []
        for item in _list_of_dicts(team_doc.get("agent_sessions")):
            payload.append(
                {
                    "agent_session_id": item.get("agent_session_id"),
                    "session_id": session_id,
                    "work_item_id": item.get("work_item_id"),
                    "adapter_id": item.get("adapter_family"),
                    "role_id": item.get("role"),
                    "status": item.get("status"),
                    "handoff_path": item.get("instructions_path"),
                    "created_at": item.get("opened_at"),
                    "updated_at": item.get("updated_at"),
                    "metadata": dict(item.get("metadata") or {}),
                }
            )
        return payload

    def _project_activation(self, session_id: str, approval_doc: dict[str, Any], *, variant_id: str) -> dict[str, Any] | None:
        if not approval_doc:
            return None
        approval_payload = _mapping(approval_doc.get("approval"))
        if not approval_payload:
            return None
        return {
            "session_id": session_id,
            "variant_id": approval_payload.get("variant_id") or approval_doc.get("recommended_variant_id") or variant_id,
            "status": approval_payload.get("status") or approval_doc.get("status"),
            "approval_required": approval_payload.get("approval_required"),
            "approval_granted": approval_payload.get("approval_granted"),
            "activation_requested": approval_payload.get("activation_requested"),
            "activation_allowed": approval_payload.get("activation_allowed"),
            "rationale": approval_payload.get("rationale"),
            "artifacts": list(approval_payload.get("artifacts") or []),
            "metadata": dict(approval_payload.get("metadata") or {}),
        }

    def _resolve_handoff_work_item(
        self,
        session_id: str,
        work_items: list[dict[str, Any]],
        *,
        work_item_id: str | None = None,
        role_id: str | None = None,
        allowed_statuses: tuple[str, ...] = ("handoff_ready",),
    ) -> dict[str, Any]:
        if work_item_id:
            for item in work_items:
                if item.get("work_item_id") == work_item_id:
                    if item.get("status") not in allowed_statuses:
                        raise ValueError(
                            f"work item {work_item_id} is not in an allowed status {allowed_statuses}: {item.get('status')}"
                        )
                    if role_id and item.get("role_id") != role_id:
                        raise ValueError(
                            f"work item {work_item_id} belongs to role {item.get('role_id')}, not requested role {role_id}"
                        )
                    return item
            raise ValueError(f"unknown work item for session {session_id}: {work_item_id}")
        if role_id:
            for item in work_items:
                if item.get("role_id") == role_id and item.get("status") in allowed_statuses:
                    return item
            raise ValueError(
                f"no work item in status {allowed_statuses} is available for role {role_id} in session {session_id}"
            )
        for item in work_items:
            if item.get("status") in allowed_statuses:
                return item
        raise ValueError(f"no work item in status {allowed_statuses} is available for session {session_id}")

    def _validate_submission_payload(
        self,
        *,
        work_item: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if _string(payload.get("contract_version")) != "nextgen.worker.response.v1":
            raise ValueError(
                "0t team submit-work requires a nextgen.worker.response.v1 payload; the facade no longer auto-wraps arbitrary JSON"
            )
        metadata = _mapping(work_item.get("metadata"))
        if _string(payload.get("workflow_step_id")) != _string(metadata.get("step_id")):
            raise ValueError(
                f"submitted payload workflow_step_id does not match kernel handoff item {work_item.get('work_item_id')}"
            )
        return dict(payload)

    def _project_recommendation(self, final_result: dict[str, Any], recommendation_doc: dict[str, Any]) -> dict[str, Any] | None:
        source = final_result or recommendation_doc
        if not source:
            return None
        payload = dict(source)
        payload.setdefault(
            "variant_id",
            payload.get("recommended_variant_id")
            or recommendation_doc.get("recommended_variant_id")
            or final_result.get("recommended_variant_id"),
        )
        payload.setdefault("leaderboard", list(final_result.get("leaderboard") or recommendation_doc.get("leaderboard") or []))
        return payload

    def _project_approval(self, approval_doc: dict[str, Any]) -> dict[str, Any] | None:
        if not approval_doc:
            return None
        payload = dict(approval_doc)
        approval_payload = _mapping(payload.get("approval"))
        payload.setdefault(
            "variant_id",
            approval_payload.get("variant_id") or payload.get("recommended_variant_id"),
        )
        return payload

    def _require_session(self, session_id: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
        docs = self._kernel_docs(session_id)
        session_doc = docs["session"]
        if not session_doc:
            raise ValueError(f"unknown session: {session_id}")
        return docs["session"], docs["team"], docs["recommendation"], docs["approval"], docs["result"]

    def doctor(self) -> dict[str, Any]:
        sessions = sorted(self.store.sessions_root.glob("*/workflow-kernel/session.json"))
        return {
            "status": "ready",
            "project_root": str(self.project_root),
            "workspace_root": str(self.workspace_root),
            "protocol_root": str(self.protocol.root),
            "workflow_count": len(self.protocol.workflows),
            "module_count": len(self.protocol.modules),
            "role_count": len(self.protocol.roles),
            "adapters": [item.model_dump(mode="json") for item in self.adapters.values()],
            "session_count": len(sessions),
            "kernel_launch_plan": self.kernel_bridge.launch_plan(),
        }

    def start_session(
        self,
        workflow_id: str,
        *,
        workspace_id: str,
        skill_ref: str,
        adapter_family: str = "codex",
        objective: str | None = None,
        session_id: str | None = None,
        data_source_adapter_id: str | None = None,
        execution_adapter_id: str | None = None,
    ) -> dict[str, Any]:
        if adapter_family not in self.adapters:
            raise ValueError(f"unsupported adapter family: {adapter_family}")
        skill_context = self._resolve_skill_context(skill_ref)
        workflow = self._workflow(workflow_id)
        kernel_workflow_id = workflow.kernel_workflow_id
        resolved_adapters = self._resolve_kernel_adapters(
            workflow_id=kernel_workflow_id,
            workspace_id=workspace_id,
            data_source_adapter_id=data_source_adapter_id,
            execution_adapter_id=execution_adapter_id,
        )
        request_payload = {
            "workflow_id": kernel_workflow_id,
            "session_id": session_id,
            "wallet": skill_context["wallet"],
            "chain": skill_context["chain"],
            "skill_name": skill_context["skill_name"],
            "workspace_id": workspace_id,
            "workspace_dir": str(self.workspace_root),
            "objective": objective or f"Optimize wallet-style skill {skill_context['skill_slug']} via autonomous research.",
            "data_source_adapter_id": resolved_adapters["data_source"],
            "execution_adapter_id": resolved_adapters["execution"] or None,
            "metadata": {
                "source": "0t team",
                "adapter_family": adapter_family,
                "entry_workflow_id": kernel_workflow_id,
                "requested_workflow_id": workflow_id,
                "workspace_adapters": {
                    "data_source": resolved_adapters["data_source"],
                    "execution": resolved_adapters["execution"] or None,
                },
            },
            "operator_hints": {
                "skill_ref": skill_ref,
                "skill_path": skill_context["skill_path"],
                "manifest_path": skill_context["manifest_path"],
            },
        }
        dispatch = self.kernel_bridge.dispatch(
            workflow_id=kernel_workflow_id,
            request_payload=request_payload,
            action="run",
            session_id=session_id,
            metadata={"team_adapter_family": adapter_family},
        )
        final_result = _mapping(dispatch.get("final_result"))
        session_id = str(final_result.get("session_id") or _mapping(dispatch.get("kernel_output")).get("session_id") or _mapping(_mapping(dispatch.get("kernel_output")).get("session")).get("session_id") or request_payload.get("session_id") or "")
        if not session_id:
            raise RuntimeError("kernel did not return a workflow session id")
        docs = self._kernel_docs(session_id)
        session_doc = docs["session"]
        team_doc = docs["team"]
        recommendation_doc = docs["recommendation"]
        result_doc = docs["result"]
        work_items = self._project_work_items(team_doc, session_id=session_id, work_items_doc=docs["work_items"])
        recommendation = self._project_recommendation(result_doc, recommendation_doc)
        session_payload = self._project_session(session_doc, result_doc, team_doc=team_doc)
        return {
            "session": session_payload,
            "work_items": work_items,
            "leaderboard": list(result_doc.get("leaderboard") or []),
            "recommendation": recommendation,
            "kernel_dispatch": {key: value for key, value in dispatch.items() if key != "final_result"},
            "ui_hints": {
                "handoff_ready_work_items": [
                    item["work_item_id"] for item in work_items if item.get("status") == "handoff_ready"
                ]
            },
        }

    def handoff(self, session_id: str, *, role_id: str, adapter_family: str | None = None) -> dict[str, Any]:
        session_doc, team_doc, _recommendation_doc, _approval_doc, _result_doc = self._require_session(session_id)
        if role_id not in self.protocol.roles:
            raise ValueError(f"unsupported 0t team role: {role_id}")
        if adapter_family is not None and adapter_family not in self.adapters:
            raise ValueError(f"unsupported adapter family: {adapter_family}")
        dispatch_metadata = {"team_adapter_family": adapter_family} if adapter_family is not None else None
        request_payload = _mapping(session_doc.get("request"))
        dispatch = self.kernel_bridge.dispatch(
            workflow_id=str(session_doc.get("workflow_id")),
            request_payload=request_payload,
            action="handoff",
            session_id=session_id,
            metadata=dispatch_metadata,
        )
        docs = self._kernel_docs(session_id)
        team_doc = docs["team"]
        work_items = self._project_work_items(team_doc, session_id=session_id, work_items_doc=docs["work_items"])
        handoff_item = self._resolve_handoff_work_item(
            session_id,
            work_items,
            role_id=role_id,
            allowed_statuses=("handoff_ready",),
        )
        projected_agent_sessions = self._project_agent_sessions(team_doc, session_id=session_id)
        agent_session = next(
            (item for item in projected_agent_sessions if item["work_item_id"] == handoff_item["work_item_id"]),
            None,
        )
        if agent_session is None:
            raise RuntimeError(
                f"kernel did not project an agent session for handoff item {handoff_item['work_item_id']}"
            )
        projected_adapter = _string(agent_session.get("adapter_id"))
        team_adapter = _string(team_doc.get("adapter_family"))
        selected_adapter = next(
            (
                candidate
                for candidate in (projected_adapter, _string(adapter_family), team_adapter)
                if candidate in self.adapters
            ),
            None,
        )
        if selected_adapter is None:
            raise ValueError(
                f"kernel did not project a supported external adapter for handoff item {handoff_item['work_item_id']}; "
                "pass --adapter explicitly when the kernel adapter family is internal-only"
            )
        instructions_path = Path(str(handoff_item["instructions_path"] or "")).expanduser()
        handoff_markdown = instructions_path.read_text(encoding="utf-8") if instructions_path.is_file() else ""
        return {
            "agent_session": agent_session,
            "launch": AdapterLaunchEnvelope(
                adapter_id=selected_adapter,
                display_name=self.adapters[selected_adapter].display_name,
                handoff_markdown=handoff_markdown,
            ).as_dict(),
            "kernel_dispatch": {key: value for key, value in dispatch.items() if key != "final_result"},
        }

    def submit_work(
        self,
        session_id: str,
        *,
        payload: dict[str, Any],
        work_item_id: str | None = None,
        role_id: str | None = None,
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        session_doc, team_doc, recommendation_doc, _approval_doc, result_doc = self._require_session(session_id)
        request_payload = _mapping(session_doc.get("request"))
        work_items = self._project_work_items(team_doc, session_id=session_id, work_items_doc=self._kernel_docs(session_id)["work_items"])
        handoff_item = self._resolve_handoff_work_item(
            session_id,
            work_items,
            work_item_id=work_item_id,
            role_id=role_id,
            allowed_statuses=("handoff_ready",),
        )
        projected_agent_sessions = self._project_agent_sessions(team_doc, session_id=session_id)
        agent_session = next(
            (item for item in projected_agent_sessions if item["work_item_id"] == handoff_item["work_item_id"]),
            None,
        )
        if agent_session is None:
            raise ValueError(
                f"kernel has no projected agent session for handoff item {handoff_item['work_item_id']}"
            )
        if agent_session.get("status") not in {"prepared", "running"}:
            raise ValueError(
                f"kernel agent session for handoff item {handoff_item['work_item_id']} is not submit-ready: {agent_session.get('status')}"
            )
        response_path = _string(handoff_item.get("result_path"))
        if not response_path:
            raise ValueError(f"kernel handoff item {handoff_item['work_item_id']} does not expose a result_path")
        response_file = Path(response_path).expanduser()
        response_file.parent.mkdir(parents=True, exist_ok=True)
        response_payload = self._validate_submission_payload(
            work_item=handoff_item,
            payload=payload,
        )
        response_file.write_text(json.dumps(response_payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        resume_adapter = next(
            (
                candidate
                for candidate in (_string(agent_session.get("adapter_id")), _string(team_doc.get("adapter_family")))
                if candidate
            ),
            None,
        )
        dispatch_metadata = {
            "submitted_work_item_id": handoff_item["work_item_id"],
            "submitted_by_agent_id": agent_id,
        }
        if resume_adapter is not None:
            dispatch_metadata["team_adapter_family"] = resume_adapter
        dispatch = self.kernel_bridge.dispatch(
            workflow_id=str(session_doc.get("workflow_id")),
            request_payload=request_payload,
            action="resume",
            session_id=session_id,
            metadata=dispatch_metadata,
        )
        docs = self._kernel_docs(session_id)
        updated_work_items = self._project_work_items(
            docs["team"],
            session_id=session_id,
            work_items_doc=docs["work_items"],
        )
        recommendation = self._project_recommendation(docs["result"], docs["recommendation"])
        return {
            "session": self._project_session(docs["session"], docs["result"], team_doc=docs["team"]),
            "submitted_work_item_id": handoff_item["work_item_id"],
            "result_path": str(response_file),
            "work_items": updated_work_items,
            "leaderboard": list(docs["result"].get("leaderboard") or []),
            "recommendation": recommendation,
            "kernel_dispatch": {key: value for key, value in dispatch.items() if key != "final_result"},
        }

    def status(self, session_id: str) -> dict[str, Any]:
        docs = self._kernel_docs(session_id)
        session_doc = docs["session"]
        team_doc = docs["team"]
        recommendation_doc = docs["recommendation"]
        approval_doc = docs["approval"]
        result_doc = docs["result"]
        return {
            "session": self._project_session(session_doc, result_doc, team_doc=team_doc),
            "work_items": self._project_work_items(team_doc, session_id=session_id, work_items_doc=docs["work_items"]),
            "leaderboard": list(result_doc.get("leaderboard") or []),
            "recommendation": self._project_recommendation(result_doc, recommendation_doc),
            "approval": self._project_approval(approval_doc),
            "agent_sessions": self._project_agent_sessions(team_doc, session_id=session_id),
            "ui_hints": {
                "handoff_ready_work_items": [
                    item["work_item_id"]
                    for item in self._project_work_items(team_doc, session_id=session_id, work_items_doc=docs["work_items"])
                    if item["status"] == "handoff_ready"
                ]
            },
        }

    def leaderboard(self, session_id: str) -> dict[str, Any]:
        _session_doc, _team_doc, _recommendation_doc, _approval_doc, result_doc = self._require_session(session_id)
        return {"session_id": session_id, "leaderboard": list(result_doc.get("leaderboard") or [])}

    def review(self, session_id: str) -> dict[str, Any]:
        docs = self._kernel_docs(session_id)
        session_doc = docs["session"]
        team_doc = docs["team"]
        recommendation_doc = docs["recommendation"]
        approval_doc = docs["approval"]
        result_doc = docs["result"]
        work_items = self._project_work_items(team_doc, session_id=session_id, work_items_doc=docs["work_items"])
        return {
            "session": self._project_session(session_doc, result_doc, team_doc=team_doc),
            "recommendation": self._project_recommendation(result_doc, recommendation_doc),
            "approval": self._project_approval(approval_doc),
            "leaderboard": list(result_doc.get("leaderboard") or []),
            "ui_hints": {
                "open_work_items": [
                    item for item in work_items if item["status"] in {"ready", "handoff_ready", "blocked", "running"}
                ]
            },
        }

    def approve(self, session_id: str, *, variant_id: str, approved_by: str = "human", activate: bool = False) -> dict[str, Any]:
        session_doc, _team_doc, _recommendation_doc, _approval_doc, result_doc = self._require_session(session_id)
        action = "activate" if activate else "approve"
        dispatch = self.kernel_bridge.dispatch(
            workflow_id=str(session_doc.get("workflow_id")),
            request_payload=_mapping(session_doc.get("request")),
            action=action,
            session_id=session_id,
            metadata={"actor_id": approved_by},
        )
        docs = self._kernel_docs(session_id)
        session_doc = docs["session"]
        recommendation_doc = docs["recommendation"]
        approval_doc = docs["approval"]
        result_doc = docs["result"]
        return {
            "session": self._project_session(session_doc, result_doc, team_doc=docs["team"]),
            "activation": self._project_activation(session_id, approval_doc, variant_id=variant_id),
            "recommendation": self._project_recommendation(result_doc, recommendation_doc),
            "approval": self._project_approval(approval_doc),
            "kernel_dispatch": {key: value for key, value in dispatch.items() if key != "final_result"},
        }

    def archive(self, session_id: str) -> dict[str, Any]:
        session_doc, _team_doc, _recommendation_doc, _approval_doc, _result_doc = self._require_session(session_id)
        self.kernel_bridge.dispatch(
            workflow_id=str(session_doc.get("workflow_id")),
            request_payload=_mapping(session_doc.get("request")),
            action="archive",
            session_id=session_id,
        )
        docs = self._kernel_docs(session_id)
        return {
            "session": self._project_session(docs["session"], docs["result"], team_doc=docs["team"]),
            "recommendation": self._project_recommendation(docs["result"], docs["recommendation"]),
            "approval": self._project_approval(docs["approval"]),
        }


def build_agent_team_service(
    *,
    project_root_path: Path | None = None,
    workspace_root: Path | str | None = None,
) -> AgentTeamService:
    root = Path(project_root_path).expanduser().resolve() if project_root_path is not None else Path.cwd().resolve()
    workspace = Path(workspace_root).expanduser().resolve() if workspace_root is not None else (root / ".ot-workspace").resolve()
    protocol = load_team_protocol_bundle(root)
    store = TeamStateStore(workspace)
    kernel_bridge = build_nextgen_kernel_bridge(project_root=root, workspace_root=workspace)
    return AgentTeamService(
        project_root=root,
        workspace_root=workspace,
        protocol=protocol,
        store=store,
        kernel_bridge=kernel_bridge,
        adapters=build_builtin_adapters(),
    )
