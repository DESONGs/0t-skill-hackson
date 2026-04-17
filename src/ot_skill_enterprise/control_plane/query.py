from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ot_skill_enterprise.control_plane.candidates import build_candidate_surface_service
from ot_skill_enterprise.qa import build_evaluation_store
from ot_skill_enterprise.runs import build_run_store
from ot_skill_enterprise.runtime.service import RuntimeService, build_runtime_service
from ot_skill_enterprise.storage import build_projection_cache, build_storage_settings


ACTIVE_RUN_STATUSES = {"active", "blocked", "in_progress", "pending", "queued", "running"}


def _sort_value(payload: dict[str, Any]) -> str:
    return str(payload.get("finished_at") or payload.get("started_at") or payload.get("updated_at") or "")


def _summarize_run_record(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not payload:
        return None
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    return {
        "run_id": payload.get("run_id"),
        "runtime_id": payload.get("runtime_id"),
        "runtime_session_id": payload.get("runtime_session_id"),
        "agent_id": payload.get("agent_id"),
        "flow_id": payload.get("flow_id"),
        "status": payload.get("status"),
        "ok": payload.get("ok"),
        "summary": payload.get("summary"),
        "started_at": payload.get("started_at"),
        "finished_at": payload.get("finished_at"),
        "event_count": payload.get("event_count", 0),
        "trace_count": payload.get("trace_count", 0),
        "artifact_count": payload.get("artifact_count", 0),
        "evaluation_id": payload.get("evaluation_id"),
        "metadata_keys": sorted(metadata.keys()),
        "active": str(payload.get("status", "")).lower() in ACTIVE_RUN_STATUSES,
    }


def _summarize_evaluation(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not payload:
        return None
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    return {
        "evaluation_id": payload.get("evaluation_id"),
        "run_id": payload.get("run_id"),
        "runtime_session_id": payload.get("runtime_session_id"),
        "subject_type": payload.get("subject_type"),
        "subject_id": payload.get("subject_id"),
        "overall_grade": payload.get("overall_grade") or payload.get("grade"),
        "runtime_pass": payload.get("runtime_pass", metadata.get("runtime_pass")),
        "contract_pass": payload.get("contract_pass", metadata.get("contract_pass")),
        "task_match_score": payload.get("task_match_score", metadata.get("task_match_score")),
        "summary": payload.get("summary"),
        "finding_count": len(payload.get("findings", [])),
        "suggested_action": payload.get("suggested_action", metadata.get("suggested_action")),
    }


def _summarize_candidate(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not payload:
        return None
    return {
        "candidate_id": payload.get("candidate_id") or payload.get("case_id"),
        "source_run_id": payload.get("source_run_id") or payload.get("run_id"),
        "source_evaluation_id": payload.get("source_evaluation_id") or payload.get("metadata", {}).get("evaluation_id"),
        "runtime_session_id": payload.get("runtime_session_id"),
        "target_skill_name": payload.get("target_skill_name"),
        "target_skill_kind": payload.get("target_skill_kind"),
        "candidate_type": payload.get("candidate_type"),
        "status": payload.get("status"),
        "validation_status": payload.get("validation_status"),
        "change_summary": payload.get("change_summary") or payload.get("summary"),
        "package_path": payload.get("package_path") or payload.get("package_root"),
        "bundle_sha256": payload.get("bundle_sha256"),
        "created_at": payload.get("created_at"),
    }


def _summarize_promotion(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not payload:
        return None
    return {
        "promotion_id": payload.get("promotion_id") or payload.get("submission_id"),
        "candidate_id": payload.get("candidate_id"),
        "source_run_id": payload.get("source_run_id") or payload.get("run_id"),
        "runtime_session_id": payload.get("runtime_session_id"),
        "target_skill_name": payload.get("target_skill_name"),
        "target_skill_kind": payload.get("target_skill_kind"),
        "validation_status": payload.get("validation_status"),
        "registry_status": payload.get("registry_status"),
        "package_path": payload.get("package_path") or payload.get("package_root"),
        "bundle_sha256": payload.get("bundle_sha256"),
        "created_at": payload.get("created_at"),
    }


def _summarize_session(session_id: str, persisted: dict[str, Any], runs: list[dict[str, Any]]) -> dict[str, Any]:
    ordered_runs = sorted(runs, key=_sort_value)
    latest_run = ordered_runs[-1] if ordered_runs else None
    status_counts = Counter(str(run.get("status") or "unknown").lower() for run in ordered_runs)
    return {
        "session_id": session_id,
        "runtime_id": persisted.get("runtime_id") or (latest_run.get("runtime_id") if latest_run else None),
        "status": persisted.get("status"),
        "cwd": persisted.get("cwd"),
        "agent_id": (persisted.get("metadata") or {}).get("agent_id") or (latest_run.get("agent_id") if latest_run else None),
        "flow_id": (persisted.get("metadata") or {}).get("flow_id") or (latest_run.get("flow_id") if latest_run else None),
        "run_count": len(ordered_runs),
        "active_run_count": sum(1 for run in ordered_runs if str(run.get("status") or "").lower() in ACTIVE_RUN_STATUSES),
        "status_counts": dict(status_counts),
        "first_seen_at": ordered_runs[0].get("started_at") if ordered_runs else persisted.get("started_at"),
        "last_seen_at": persisted.get("updated_at") or (latest_run.get("finished_at") or latest_run.get("started_at") if latest_run else persisted.get("started_at")),
        "latest_run": _summarize_run_record(latest_run),
        "recent_run_ids": [run.get("run_id") for run in ordered_runs[-3:]][::-1],
        "invocation_count": len(persisted.get("invocations") or []),
        "event_count": len(persisted.get("events") or []),
        "artifact_count": len(persisted.get("artifacts") or []),
        "metadata": persisted.get("metadata") or {},
    }


@dataclass(slots=True)
class ControlPlaneQueryService:
    project_root: Path
    workspace_root: Path
    runtime_service: RuntimeService
    _evaluation_store: Any = field(init=False, repr=False)
    _candidate_service: Any = field(init=False, repr=False)
    _settings: Any = field(init=False, repr=False)
    _cache: Any = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._settings = build_storage_settings(project_root=self.project_root, workspace_root=self.workspace_root)
        self._cache = build_projection_cache(settings=self._settings)
        self._evaluation_store = build_evaluation_store(self.workspace_root / "evolution-registry")
        self._candidate_service = build_candidate_surface_service(self.project_root, self.workspace_root)

    def _key(self, name: str) -> str:
        return f"runtime:{name}:{self.workspace_root}"

    def _run_store(self):
        return build_run_store(self.workspace_root / "evolution-registry")

    def _candidate_overview(self) -> dict[str, Any]:
        return self._candidate_service.overview()

    def _build_runtime_views(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        run_store = self._run_store()
        runs = [item.model_dump(mode="json") for item in run_store.load_runs_from_disk()]
        traces = [item.model_dump(mode="json") for item in run_store.load_traces_from_disk()]
        artifacts = [item.model_dump(mode="json") for item in run_store.load_artifacts_from_disk()]
        evaluations = [item.model_dump(mode="json") for item in self._evaluation_store.load_from_disk()]
        candidates = self._candidate_service.list_candidates()["items"]
        promotions = self._candidate_service.list_promotions()["items"]
        ordered_runs = sorted(runs, key=_sort_value)
        return ordered_runs, traces, artifacts, evaluations, candidates, promotions

    def list_runtimes(self) -> dict[str, Any]:
        cached = self._cache.get_json(self._key("runtimes"))
        if cached is not None:
            return cached
        payload = self.runtime_service.list_runtimes()
        self._cache.set_json(self._key("runtimes"), payload, ttl_seconds=self._settings.cache_ttl_overview)
        return payload

    def list_sessions(self) -> dict[str, Any]:
        cached = self._cache.get_json(self._key("sessions"))
        if cached is not None:
            return cached
        runs, _, _, _, _, _ = self._build_runtime_views()
        sessions_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for run in runs:
            session_id = str(run.get("runtime_session_id") or "")
            if session_id:
                sessions_map[session_id].append(run)
        persisted_sessions = {str(item.get("session_id") or ""): item for item in self.runtime_service.list_sessions().get("items", []) if item.get("session_id")}
        session_items = [_summarize_session(session_id, persisted_sessions[session_id], sessions_map.get(session_id, [])) for session_id in persisted_sessions]
        session_items.sort(key=lambda item: item.get("last_seen_at") or "", reverse=True)
        payload = {"status": "ready", "count": len(session_items), "items": session_items}
        self._cache.set_json(self._key("sessions"), payload, ttl_seconds=self._settings.cache_ttl_session_summary)
        return payload

    def list_active_runs(self) -> dict[str, Any]:
        cached = self._cache.get_json(self._key("active_runs"))
        if cached is not None:
            return cached
        runs, _, _, _, _, _ = self._build_runtime_views()
        active = [run for run in runs if str(run.get("status") or "").lower() in ACTIVE_RUN_STATUSES]
        active.sort(key=_sort_value, reverse=True)
        payload = {"status": "ready", "count": len(active), "items": [_summarize_run_record(run) for run in active]}
        self._cache.set_json(self._key("active_runs"), payload, ttl_seconds=self._settings.cache_ttl_active_runs)
        return payload

    def list_evaluations(self) -> dict[str, Any]:
        cached = self._cache.get_json(self._key("evaluations"))
        if cached is not None:
            return cached
        evaluations = [item.model_dump(mode="json") for item in self._evaluation_store.list_evaluations()]
        ordered = sorted(evaluations, key=lambda item: str(item.get("created_at") or item.get("evaluation_id") or ""), reverse=True)
        items = [_summarize_evaluation(item) for item in ordered]
        payload = {"status": "ready", "count": len(items), "items": items}
        self._cache.set_json(self._key("evaluations"), payload, ttl_seconds=self._settings.cache_ttl_overview)
        return payload

    def list_candidates(self) -> dict[str, Any]:
        cached = self._cache.get_json(self._key("candidates"))
        if cached is not None:
            return cached
        candidates = self._candidate_service.list_candidates()["items"]
        ordered = sorted(candidates, key=lambda item: str(item.get("created_at") or item.get("candidate_id") or ""), reverse=True)
        items = [_summarize_candidate(item) for item in ordered]
        payload = {"status": "ready", "count": len(items), "items": items}
        self._cache.set_json(self._key("candidates"), payload, ttl_seconds=self._settings.cache_ttl_evolution_summary)
        return payload

    def list_promotions(self) -> dict[str, Any]:
        cached = self._cache.get_json(self._key("promotions"))
        if cached is not None:
            return cached
        promotions = self._candidate_service.list_promotions()["items"]
        ordered = sorted(promotions, key=lambda item: str(item.get("created_at") or item.get("promotion_id") or ""), reverse=True)
        items = [_summarize_promotion(item) for item in ordered]
        payload = {"status": "ready", "count": len(items), "items": items}
        self._cache.set_json(self._key("promotions"), payload, ttl_seconds=self._settings.cache_ttl_evolution_summary)
        return payload

    def runtime_overview(self) -> dict[str, Any]:
        cached = self._cache.get_json(self._key("overview"))
        if cached is not None:
            return cached
        runs, traces, artifacts, evaluations, candidates, promotions = self._build_runtime_views()
        active_runs = [run for run in runs if str(run.get("status") or "").lower() in ACTIVE_RUN_STATUSES]
        runtimes = self.list_runtimes()
        sessions = self.list_sessions()["items"]
        status_counts = Counter(str(run.get("status") or "unknown").lower() for run in runs)
        latest_run = runs[-1] if runs else None
        latest_evaluation = self.list_evaluations()["items"][0] if evaluations else None
        latest_candidate = candidates[0] if candidates else None
        latest_promotion = promotions[0] if promotions else None
        latest_active_run = active_runs[-1] if active_runs else None
        payload = {
            "status": "ready",
            "mode": "runtime-dashboard",
            "workspace_root": str(self.workspace_root),
            "runtime_count": runtimes["count"],
            "run_count": len(runs),
            "active_run_count": len(active_runs),
            "session_count": len(sessions),
            "trace_count": len(traces),
            "artifact_count": len(artifacts),
            "evaluation_count": len(evaluations),
            "candidate_count": len(candidates),
            "promotion_count": len(promotions),
            "status_counts": dict(status_counts),
            "runtimes": runtimes["items"],
            "latest_run": _summarize_run_record(latest_run),
            "latest_active_run": _summarize_run_record(latest_active_run),
            "latest_evaluation": latest_evaluation,
            "latest_candidate": _summarize_candidate(latest_candidate),
            "latest_promotion": _summarize_promotion(latest_promotion),
            "runtime_notes": [
                "dashboard is read-only and reflects repository-backed control-plane state",
                "sessions are keyed by explicit runtime_session_id only",
                "candidate generation is driven by evaluation, not by legacy feedback/case/proposal/submission objects",
            ],
        }
        self._cache.set_json(self._key("overview"), payload, ttl_seconds=self._settings.cache_ttl_overview)
        return payload

    def evolution_summary(self) -> dict[str, Any]:
        cached = self._cache.get_json(self._key("evolution_summary"))
        if cached is not None:
            return cached
        evaluations = self.list_evaluations()
        candidates = self.list_candidates()
        promotions = self.list_promotions()
        payload = {
            "status": "ready",
            "scope": ["evaluation", "candidate", "promotion"],
            "counts": {
                "evaluations": evaluations["count"],
                "candidates": candidates["count"],
                "promotions": promotions["count"],
            },
            "latest": {
                "evaluations": evaluations["items"][0] if evaluations["items"] else None,
                "candidates": candidates["items"][0] if candidates["items"] else None,
                "promotions": promotions["items"][0] if promotions["items"] else None,
            },
            "note": "v3 lifecycle is evaluation -> candidate -> promotion",
        }
        self._cache.set_json(self._key("evolution_summary"), payload, ttl_seconds=self._settings.cache_ttl_evolution_summary)
        return payload


def build_control_plane_query_service(project_root: Path, workspace_root: Path) -> ControlPlaneQueryService:
    return ControlPlaneQueryService(
        project_root=project_root,
        workspace_root=workspace_root,
        runtime_service=build_runtime_service(root=project_root, workspace_dir=workspace_root),
    )
