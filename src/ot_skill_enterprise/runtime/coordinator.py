from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from ot_skill_enterprise.runs.pipeline import RunIngestionPipeline, RunPipelineResult

from .contracts import RuntimeAdapter, RuntimeExecutor
from .execution import RuntimeExecutionRequest, RuntimeLaunchSpec
from .models import RuntimeArtifact, RuntimeEvent, RuntimeInvocation, RuntimeSession
from .store import RuntimeSessionStore
from .transcript import RuntimeTranscript
from .translator import DefaultRuntimeTranslator


@dataclass(slots=True)
class RuntimeRunResult:
    runtime_id: str
    session: RuntimeSession
    invocation: RuntimeInvocation
    execution: dict[str, Any]
    transcript: RuntimeTranscript
    pipeline: RunPipelineResult

    @staticmethod
    def _json_safe(value: Any) -> Any:
        return json.loads(json.dumps(value, ensure_ascii=False, default=lambda item: item.model_dump(mode="json") if hasattr(item, "model_dump") else str(item)))

    def as_dict(self, *, full: bool = True) -> dict[str, Any]:
        summary = {
            "runtime_id": self.runtime_id,
            "session": {
                "session_id": self.session.session_id,
                "runtime_id": self.session.runtime_id,
                "status": self.session.status,
                "updated_at": self.session.model_dump(mode="json").get("updated_at"),
            },
            "invocation": {
                "invocation_id": self.invocation.invocation_id,
                "status": self.invocation.status,
                "summary": self.invocation.summary,
                "finished_at": self.invocation.model_dump(mode="json").get("finished_at"),
            },
            **self.pipeline.summary_dict(),
        }
        if not full:
            return summary
        summary.update(
            {
                "execution": {
                    **dict(self.execution),
                    "started_at": str(self.execution.get("started_at") or ""),
                    "finished_at": str(self.execution.get("finished_at") or ""),
                },
                "transcript": self.transcript.model_dump(mode="json"),
                "runtime_events": [item.model_dump(mode="json") for item in self.pipeline.runtime_events],
                "traces": [item.model_dump(mode="json") for item in self.pipeline.traces],
                "artifacts": [item.model_dump(mode="json") for item in self.pipeline.artifacts],
                "candidate_lifecycle": self._json_safe(self.pipeline.lifecycle) if self.pipeline.lifecycle is not None else None,
            }
        )
        return summary


@dataclass(slots=True)
class RuntimeRunCoordinator:
    project_root: Path
    workspace_root: Path
    session_store: RuntimeSessionStore
    executor: RuntimeExecutor
    translator: DefaultRuntimeTranslator
    registry_root: Path

    def _session_dir(self, session_id: str) -> Path:
        path = self.workspace_root / "runtime-sessions" / session_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _launch_spec(self, adapter: RuntimeAdapter) -> RuntimeLaunchSpec:
        plan = adapter.launch_plan()
        launcher = [str(item) for item in list(plan.get("runtime_launcher") or []) if str(item).strip()]
        return RuntimeLaunchSpec(
            runtime_id=adapter.descriptor.runtime_id,
            launcher=launcher,
            entrypoint=str(plan.get("entrypoint")) if plan.get("entrypoint") else None,
            cwd=str(plan.get("runtime_root") or self.project_root),
            mode=str(plan.get("mode") or "release"),
            metadata={key: value for key, value in plan.items() if key not in {"runtime_launcher", "entrypoint", "runtime_root", "mode"}},
        )

    @staticmethod
    def _metadata_text(metadata: Mapping[str, Any] | None, *keys: str, default: str | None = None) -> str | None:
        payload = dict(metadata or {})
        for key in keys:
            value = payload.get(key)
            text = str(value).strip() if value is not None else ""
            if text:
                return text
        return default

    def run(
        self,
        *,
        adapter: RuntimeAdapter,
        prompt: str,
        session_id: str | None = None,
        cwd: Path | str | None = None,
        input_payload: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> RuntimeRunResult:
        runtime_id = adapter.descriptor.runtime_id
        session = adapter.start_session(session_id=session_id, cwd=cwd, inputs=input_payload, metadata=metadata)
        invocation = adapter.start_invocation(
            session.session_id,
            kind="turn",
            prompt=prompt,
            input_payload=input_payload,
            metadata=metadata,
        )
        self.session_store.record_session(session)

        run_id = f"run-{uuid4().hex[:12]}"
        request = RuntimeExecutionRequest(
            runtime_id=runtime_id,
            session_id=session.session_id,
            invocation_id=invocation.invocation_id,
            workspace_dir=str(self.workspace_root),
            session_workspace=str(self._session_dir(session.session_id)),
            cwd=str(Path(cwd).expanduser().resolve()) if cwd is not None else str(self.project_root),
            prompt=prompt,
            input_payload=dict(input_payload or {}),
            metadata={"run_id": run_id, **dict(metadata or {})},
            launch_spec=self._launch_spec(adapter),
        )
        execution = None
        try:
            execution = self.executor.execute(request)
            translation = self.translator.apply(
                adapter,
                transcript=execution.transcript,
                session_id=session.session_id,
                invocation_id=invocation.invocation_id,
            )
            invocation = adapter.finish_invocation(
                session.session_id,
                invocation.invocation_id,
                status=execution.transcript.status,
                summary=execution.transcript.summary,
                output_payload=execution.transcript.output_payload,
                metadata=execution.transcript.metadata,
            )
            session = adapter.close_session(
                session.session_id,
                status="failed" if execution.transcript.status in {"failed", "error"} else "succeeded",
                metadata=execution.transcript.metadata,
            )
            self.session_store.record_session(session)
            snapshot = adapter.snapshot_session(session.session_id)
            pipeline = RunIngestionPipeline(self.registry_root).record(
                {
                    "run_id": run_id,
                    "runtime_id": runtime_id,
                    "runtime_session_id": snapshot.session_id,
                    "subject_kind": self._metadata_text(metadata, "subject_kind", default="runtime_session"),
                    "subject_id": self._metadata_text(metadata, "subject_id", default=snapshot.session_id),
                    "agent_id": self._metadata_text(metadata, "agent_id", default=f"{runtime_id}-runtime"),
                    "agent": {
                        "agent_id": self._metadata_text(metadata, "agent_id", default=f"{runtime_id}-runtime"),
                        "display_name": self._metadata_text(metadata, "agent_display_name", default=f"{runtime_id.upper()} Runtime"),
                        "execution_mode": self._metadata_text(metadata, "agent_execution_mode", default="embedded"),
                        "metadata": {
                            "runtime_id": runtime_id,
                            **dict((metadata or {}).get("agent_metadata") or {}),
                        },
                    },
                    "flow_id": self._metadata_text(metadata, "flow_id", default=runtime_id),
                    "status": execution.transcript.status,
                    "ok": execution.transcript.ok,
                    "summary": execution.transcript.summary,
                    "input_payload": {"prompt": prompt, **dict(input_payload or {})},
                    "output_payload": dict(execution.transcript.output_payload),
                    "provider_ids": list(execution.transcript.provider_ids),
                    "skill_ids": list(execution.transcript.skill_ids),
                    "events": [self._normalize_runtime_event(run_id, item) for item in translation.runtime_events],
                    "artifacts": [self._normalize_artifact(run_id, item) for item in translation.artifacts],
                    "metadata": {
                        "runtime_id": runtime_id,
                        "session_id": snapshot.session_id,
                        "invocation_id": invocation.invocation_id,
                        "launch_spec": request.launch_spec.model_dump(mode="json"),
                        **dict(metadata or {}),
                        **dict(execution.transcript.metadata),
                    },
                }
            )
            return RuntimeRunResult(
                runtime_id=runtime_id,
                session=snapshot,
                invocation=invocation,
                execution={
                    "command": execution.command,
                    "returncode": execution.returncode,
                    "launch_spec": request.launch_spec.model_dump(mode="json"),
                    "started_at": execution.started_at,
                    "finished_at": execution.finished_at,
                },
                transcript=execution.transcript,
                pipeline=pipeline,
            )
        except Exception:
            adapter.finish_invocation(
                session.session_id,
                invocation.invocation_id,
                status="failed",
                summary="runtime execution raised unhandled exception",
                output_payload={},
                metadata={},
            )
            session = adapter.close_session(
                session.session_id,
                status="failed",
                metadata={},
            )
            final_session = session if isinstance(session, RuntimeSession) else adapter.snapshot_session(session.session_id)
            self.session_store.record_session(final_session)
            raise

    @staticmethod
    def _normalize_runtime_event(run_id: str, event: RuntimeEvent) -> dict[str, Any]:
        payload = dict(event.payload)
        metadata = dict(event.metadata)
        metadata.update(
            {
                "runtime_id": event.runtime_id,
                "runtime_session_id": event.session_id,
                "session_id": event.session_id,
                "invocation_id": event.invocation_id,
            }
        )
        if event.tool_call is not None:
            payload.setdefault("tool_call", event.tool_call.model_dump(mode="json"))
            payload.setdefault("tool_name", event.tool_call.tool_name)
        if event.artifact is not None:
            payload.setdefault("artifact", event.artifact.model_dump(mode="json"))
        return {
            "event_id": event.event_id,
            "run_id": run_id,
            "runtime_session_id": event.session_id,
            "trace_id": event.invocation_id,
            "event_type": event.event_type,
            "status": event.tool_call.status if event.tool_call is not None else payload.get("status"),
            "summary": event.message or payload.get("summary"),
            "artifact_id": event.artifact.artifact_id if event.artifact is not None else None,
            "timestamp": event.timestamp,
            "payload": payload,
            "metadata": metadata,
        }

    @staticmethod
    def _normalize_artifact(run_id: str, artifact: RuntimeArtifact) -> dict[str, Any]:
        metadata = dict(artifact.metadata)
        metadata.update(
            {
                "run_id": run_id,
                "runtime_id": artifact.runtime_id,
                "runtime_session_id": artifact.session_id,
                "session_id": artifact.session_id,
                "invocation_id": artifact.invocation_id,
                "tool_call_id": artifact.tool_call_id,
            }
        )
        return {
            "artifact_id": artifact.artifact_id,
            "runtime_session_id": artifact.session_id,
            "kind": artifact.kind,
            "uri": artifact.uri,
            "label": artifact.label,
            "source_step_id": artifact.invocation_id,
            "metadata": metadata,
        }
