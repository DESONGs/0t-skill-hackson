from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from ot_skill_enterprise.lab import build_skill_candidate
from ot_skill_enterprise.qa.evaluator import QAEvaluator
from ot_skill_enterprise.registry import build_evolution_registry
from ot_skill_enterprise.runtime.models import RuntimeSession
from ot_skill_enterprise.runtime.store import build_runtime_session_store
from ot_skill_enterprise.storage import build_projection_cache, build_storage_settings

from .models import ArtifactRecord, RunRecord, RunTrace, RuntimeEvent


@dataclass(slots=True)
class RunPipelineResult:
    run: RunRecord
    runtime_events: list[RuntimeEvent]
    traces: list[RunTrace]
    artifacts: list[ArtifactRecord]
    evaluation: Any | None
    lifecycle: dict[str, Any] | None = None

    @staticmethod
    def _json_safe(value: Any) -> Any:
        return json.loads(json.dumps(value, ensure_ascii=False, default=lambda item: item.model_dump(mode="json") if hasattr(item, "model_dump") else str(item)))

    def summary_dict(self) -> dict[str, Any]:
        evaluation_summary = None
        if self.evaluation is not None:
            evaluation_summary = {
                "evaluation_id": self.evaluation.evaluation_id,
                "run_id": self.evaluation.run_id,
                "subject_type": self.evaluation.subject_type,
                "subject_id": self.evaluation.subject_id,
                "grade": self.evaluation.grade,
                "summary": self.evaluation.summary,
                "finding_count": len(self.evaluation.findings),
            }
        lifecycle_summary = None
        if self.lifecycle is not None:
            payload = self._json_safe(self.lifecycle)
            if isinstance(payload, dict):
                lifecycle_summary = {
                    "candidate_id": payload.get("candidate", {}).get("candidate_id") if isinstance(payload.get("candidate"), dict) else None,
                }
        return {
            "run": {
                "run_id": self.run.run_id,
                "runtime_id": self.run.runtime_id,
                "runtime_session_id": self.run.runtime_session_id,
                "agent_id": self.run.agent_id,
                "flow_id": self.run.flow_id,
                "status": self.run.status,
                "ok": self.run.ok,
                "summary": self.run.summary,
                "started_at": self.run.model_dump(mode="json").get("started_at"),
                "finished_at": self.run.model_dump(mode="json").get("finished_at"),
                "trace_ids": list(self.run.trace_ids),
                "artifact_ids": list(self.run.artifact_ids),
                "event_count": self.run.event_count,
                "trace_count": self.run.trace_count,
                "artifact_count": self.run.artifact_count,
                "evaluation_id": self.run.evaluation_id,
            },
            "evaluation": evaluation_summary,
            "candidate_lifecycle": lifecycle_summary,
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "run": self.run.model_dump(mode="json"),
            "runtime_events": [item.model_dump(mode="json") for item in self.runtime_events],
            "traces": [item.model_dump(mode="json") for item in self.traces],
            "artifacts": [item.model_dump(mode="json") for item in self.artifacts],
            "evaluation": self.evaluation.model_dump(mode="json") if self.evaluation is not None else None,
            "candidate_lifecycle": self._json_safe(self.lifecycle) if self.lifecycle is not None else None,
        }


@dataclass(slots=True)
class RunIngestionPipeline:
    registry_root: Path

    def record(self, payload: Mapping[str, Any]) -> RunPipelineResult:
        from .intake import (
            _first,
            _mapping,
            _nested_value,
            _normalize_agent,
            _normalize_artifacts,
            _normalize_evaluation,
            _normalize_run,
            _normalize_runtime_events,
            _normalize_traces,
        )

        root = self.registry_root.expanduser().resolve()
        workspace_root = root.parent if root.name == "evolution-registry" else root
        settings = build_storage_settings(workspace_root=workspace_root)
        registry = build_evolution_registry(root)
        qa = QAEvaluator(store=registry.evaluations)
        cache = build_projection_cache(settings=settings)
        body = dict(payload)
        derived_id = str(_first(body, "run_id", "runId", "request_id", default=body.get("subject_id") or uuid4().hex))
        runtime_session_id = str(_first(body, "runtime_session_id", "runtimeSessionId", default=_nested_value(body, "runtime_session_id") or _mapping(body.get("metadata")).get("runtime_session_id") or ""))
        if not runtime_session_id:
            raise ValueError("runtime_session_id is required for run ingestion")

        explicit_traces = body.get("traces")
        if isinstance(explicit_traces, list) and explicit_traces:
            traces = _normalize_traces(body, run_id=derived_id, runtime_session_id=runtime_session_id, runtime_events=[])
        else:
            traces = []

        runtime_events = _normalize_runtime_events(body, run_id=derived_id, runtime_session_id=runtime_session_id, traces=traces)
        if not traces:
            traces = _normalize_traces(body, run_id=derived_id, runtime_session_id=runtime_session_id, runtime_events=runtime_events)
        artifacts = _normalize_artifacts(body, run_id=derived_id, runtime_session_id=runtime_session_id, runtime_events=runtime_events)
        run = _normalize_run(body, runtime_events=runtime_events, traces=traces, artifacts=artifacts)
        agent = _normalize_agent(body, run=run)
        session_store = build_runtime_session_store(root=root)
        session_store.record_session(
            RuntimeSession(
                session_id=run.runtime_session_id,
                runtime_id=run.runtime_id,
                status="failed" if not run.ok else "succeeded",
                metadata={
                    "agent_id": run.agent_id,
                    "flow_id": run.flow_id,
                    "source": body.get("source") or "run-pipeline",
                },
            )
        )

        registry.record_agent(agent)
        for trace in traces:
            registry.record_trace(trace)
        for artifact in artifacts:
            registry.record_artifact(artifact)

        evaluation = _normalize_evaluation(body, run=run, runtime_events=runtime_events, artifacts=artifacts, qa=qa)
        registry.record_evaluation(evaluation)
        run.evaluation_id = evaluation.evaluation_id
        run = registry.record_run(run)

        lifecycle: dict[str, Any] | None = None
        review_hook = body.get("llm_review_hook") or _mapping(body.get("metadata")).get("llm_review_hook")
        candidate_generation_spec = body.get("candidate_generation_spec") or _mapping(body.get("metadata")).get("candidate_generation_spec")
        candidate_manifest_preview = body.get("candidate_manifest_preview") or _mapping(body.get("metadata")).get("candidate_manifest_preview")
        candidate_metadata = body.get("candidate_metadata") or _mapping(body.get("metadata")).get("candidate_metadata")
        disable_candidate_generation = bool(
            body.get("disable_candidate_generation")
            or _mapping(body.get("metadata")).get("disable_candidate_generation")
        )
        should_generate_candidate = evaluation.overall_grade != "pass" or bool(evaluation.suggested_action)
        if isinstance(review_hook, Mapping) and bool(review_hook.get("should_generate_candidate")):
            should_generate_candidate = True
        if should_generate_candidate and not disable_candidate_generation:
            candidate = build_skill_candidate(
                evaluation,
                target_skill_name=str(
                    body.get("target_skill_name")
                    or body.get("skill_name")
                    or run.subject_id
                    or run.flow_id
                    or run.agent_id
                ),
                target_skill_kind=str(
                    body.get("target_skill_kind")
                    or body.get("skill_kind")
                    or run.subject_kind
                    or "prompt"
                ),
                candidate_type=str(body.get("candidate_type") or run.subject_kind or "general"),
                generation_spec=dict(candidate_generation_spec) if isinstance(candidate_generation_spec, Mapping) else None,
                manifest_preview=dict(candidate_manifest_preview) if isinstance(candidate_manifest_preview, Mapping) else None,
                metadata=dict(candidate_metadata) if isinstance(candidate_metadata, Mapping) else None,
                registry=registry,
            )
            lifecycle = {
                "evaluation": evaluation.model_dump(mode="json"),
                "candidate": candidate.model_dump(mode="json"),
            }
            if isinstance(review_hook, Mapping):
                lifecycle["llm_review_hook"] = dict(review_hook)

        cache.delete_keys(
            f"runtime:overview:{workspace_root}",
            f"runtime:session:{run.runtime_session_id}:summary",
            f"runtime:active_runs:{workspace_root}",
            f"runtime:latest_evaluation:{workspace_root}",
            f"runtime:evaluations:{workspace_root}",
            f"runtime:candidates:{workspace_root}",
            f"runtime:promotions:{workspace_root}",
            f"runtime:evolution_summary:{workspace_root}",
            f"runtime:runtimes:{workspace_root}",
            f"runtime:sessions:{workspace_root}",
        )

        return RunPipelineResult(
            run=run,
            runtime_events=runtime_events,
            traces=traces,
            artifacts=artifacts,
            evaluation=evaluation,
            lifecycle=lifecycle,
        )
