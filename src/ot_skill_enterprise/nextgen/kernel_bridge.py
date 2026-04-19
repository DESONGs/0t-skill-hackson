from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import uuid4

from ot_skill_enterprise.runtime.service import RuntimeService, build_runtime_service
from ot_skill_enterprise.service_locator import project_root as resolve_project_root


WorkflowFallback = Callable[[Mapping[str, Any]], dict[str, Any]]


def _default_project_root() -> Path:
    return resolve_project_root()


def configured_workflow_runtime(explicit: str | None = None) -> str:
    candidate = str(explicit or os.environ.get("OT_WORKFLOW_RUNTIME") or "ts-kernel").strip().lower()
    if candidate not in {"ts-kernel", "python-compat"}:
        return "ts-kernel"
    return candidate


def _load_json_file(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _mapping_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


@dataclass(slots=True)
class WorkflowKernelBridge:
    project_root: Path
    workspace_root: Path
    runtime_service: RuntimeService
    runtime_mode: str = "ts-kernel"

    def launch_plan(self) -> dict[str, Any]:
        plan = self.runtime_service.registry.resolve("pi").launch_plan()
        return {
            "status": "ready",
            "kernel_runtime": "pi",
            "pi_mode": "workflow",
            "workflow_runtime_mode": self.runtime_mode,
            **plan,
        }

    def dispatch(
        self,
        *,
        workflow_id: str,
        request_payload: Mapping[str, Any],
        action: str = "run",
        session_id: str | None = None,
        prompt: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        fallback_runner: WorkflowFallback | None = None,
        allow_failure: bool = False,
    ) -> dict[str, Any]:
        resolved_session_id = str(session_id or request_payload.get("session_id") or "").strip() or None
        launch_plan = self.launch_plan()
        if self.runtime_mode == "python-compat":
            if fallback_runner is None:
                payload = {
                    "status": "failed",
                    "workflow_id": workflow_id,
                    "launch_plan": launch_plan,
                    "error": "python-compat mode requires a fallback runner",
                }
                if allow_failure:
                    return payload
                raise RuntimeError(payload["error"])
            final_result = fallback_runner(request_payload)
            return {
                "status": "fallback",
                "workflow_id": workflow_id,
                "launch_plan": launch_plan,
                "runtime_mode": "python-compat",
                "final_result": final_result,
            }

        kernel_request = {
            "workflow_session": {
                "workflow_id": workflow_id,
                "session_id": resolved_session_id,
                "request": dict(request_payload),
                "workspace_dir": str(self.workspace_root),
                "action": action,
            },
            "pi_mode": "workflow",
        }
        kernel_metadata = {
            "pi_mode": "workflow",
            "flow_id": workflow_id,
            "subject_kind": "workflow_session",
            "subject_id": str(
                request_payload.get("wallet") or request_payload.get("skill_name") or request_payload.get("session_id") or workflow_id
            ),
            "agent_id": "pi-kernel",
            "agent_display_name": "Pi Workflow Kernel",
            "kernel_dispatch_id": f"kernel-{uuid4().hex[:10]}",
            **dict(metadata or {}),
        }
        previous_python_executable = os.environ.get("OT_WORKFLOW_PYTHON_EXECUTABLE")
        os.environ["OT_WORKFLOW_PYTHON_EXECUTABLE"] = sys.executable
        try:
            result = self.runtime_service.run(
                runtime_id="pi",
                prompt=prompt or f"Dispatch workflow {workflow_id}",
                session_id=resolved_session_id,
                cwd=self.project_root,
                input_payload=kernel_request,
                metadata=kernel_metadata,
            )
            output_payload = dict(result.transcript.output_payload or {})
            final_result = self._resolve_final_result(output_payload)
            return {
                "status": "ran",
                "workflow_id": workflow_id,
                "launch_plan": launch_plan,
                "runtime_mode": self.runtime_mode,
                "runtime_run": result.as_dict(full=False),
                "kernel_output": output_payload,
                "final_result": final_result,
            }
        except Exception:
            payload = {
                "status": "failed",
                "workflow_id": workflow_id,
                "launch_plan": launch_plan,
                "runtime_mode": self.runtime_mode,
            }
            if allow_failure:
                return payload
            raise
        finally:
            if previous_python_executable is None:
                os.environ.pop("OT_WORKFLOW_PYTHON_EXECUTABLE", None)
            else:
                os.environ["OT_WORKFLOW_PYTHON_EXECUTABLE"] = previous_python_executable

    def _resolve_final_result(self, output_payload: Mapping[str, Any]) -> dict[str, Any]:
        direct = output_payload.get("final_result")
        if isinstance(direct, Mapping):
            return dict(direct)
        result_payload = output_payload.get("result")
        if isinstance(result_payload, Mapping):
            return dict(result_payload)
        result_path = output_payload.get("result_path")
        if result_path:
            path = Path(str(result_path)).expanduser().resolve()
            if path.exists():
                payload = _load_json_file(path)
                final_result = payload.get("final_result")
                if isinstance(final_result, Mapping):
                    return dict(final_result)
                return payload
        session_payload = _mapping_dict(output_payload.get("session"))
        direct_session_result = session_payload.get("final_result")
        if isinstance(direct_session_result, Mapping):
            return dict(direct_session_result)
        nested_result_path = session_payload.get("result_path")
        if nested_result_path:
            path = Path(str(nested_result_path)).expanduser().resolve()
            if path.exists():
                payload = _load_json_file(path)
                final_result = payload.get("final_result")
                if isinstance(final_result, Mapping):
                    return dict(final_result)
                return payload
        session_file = output_payload.get("session_file")
        if session_file:
            path = Path(str(session_file)).expanduser().resolve()
            if path.exists():
                payload = _load_json_file(path)
                final_result = payload.get("final_result")
                if isinstance(final_result, Mapping):
                    return dict(final_result)
                return payload
        nested_session_file = session_payload.get("session_file")
        if nested_session_file:
            path = Path(str(nested_session_file)).expanduser().resolve()
            if path.exists():
                payload = _load_json_file(path)
                final_result = payload.get("final_result")
                if isinstance(final_result, Mapping):
                    return dict(final_result)
                return payload
        return {}


def build_nextgen_kernel_bridge(
    *,
    project_root: Path | None = None,
    workspace_root: Path | None = None,
    runtime_mode: str | None = None,
) -> WorkflowKernelBridge:
    resolved_project_root = (project_root or _default_project_root()).expanduser().resolve()
    resolved_workspace_root = (
        Path(workspace_root).expanduser().resolve()
        if workspace_root is not None
        else (resolved_project_root / ".ot-workspace").resolve()
    )
    resolved_workspace_root.mkdir(parents=True, exist_ok=True)
    runtime_service = build_runtime_service(root=resolved_project_root, workspace_dir=resolved_workspace_root)
    return WorkflowKernelBridge(
        project_root=resolved_project_root,
        workspace_root=resolved_workspace_root,
        runtime_service=runtime_service,
        runtime_mode=configured_workflow_runtime(runtime_mode),
    )
