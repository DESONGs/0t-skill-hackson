from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping, Optional, Protocol
from uuid import uuid4

from ot_skill_enterprise.qa.models import EvaluationRecord
from ot_skill_enterprise.runs.models import RunRecord, RunTrace
from ot_skill_enterprise.shared.contracts import AnalysisCase, AnalysisFeedback, AnalysisProposal, ArtifactRef
from ot_skill_enterprise.shared.contracts.common import utc_now

from .models import PromotionRecord, PromotionSubmission, SkillCandidate


class EvolutionRegistryProtocol(Protocol):
    def record_agent(self, agent: Any) -> Any: ...

    def record_run(self, run: Any) -> Any: ...

    def record_trace(self, trace: Any) -> Any: ...

    def record_artifact(self, artifact: Any) -> Any: ...

    def record_evaluation(self, evaluation: Any) -> Any: ...

    def record_candidate(self, candidate: SkillCandidate) -> Mapping[str, Any]: ...

    def record_promotion(self, promotion: PromotionRecord) -> Mapping[str, Any]: ...

    def record_feedback(self, feedback: AnalysisFeedback) -> Mapping[str, Any]: ...

    def record_case(self, case: AnalysisCase) -> Mapping[str, Any]: ...

    def record_proposal(self, proposal: AnalysisProposal) -> Mapping[str, Any]: ...

    def record_submission(self, submission: PromotionSubmission) -> Mapping[str, Any]: ...

    def candidate_path(self, candidate_id: str) -> Path | None: ...

    def promotion_path(self, promotion_id: str) -> Path | None: ...

    def case_path(self, case_id: str) -> Path | None: ...

    def proposal_path(self, proposal_id: str) -> Path | None: ...

    def submission_path(self, submission_id: str) -> Path | None: ...


_CASEFUL_STATUSES = {"failed", "partial"}


def _dump_model(value: Any) -> Any:
    dumper = getattr(value, "model_dump", None)
    if dumper is not None:
        return dumper(mode="json")
    return value


def _stable_payload(value: Any) -> str:
    return json.dumps(_dump_model(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hashed_id(prefix: str, value: Any, *, length: int = 12) -> str:
    digest = hashlib.sha256(_stable_payload(value).encode("utf-8")).hexdigest()
    return f"{prefix}-{digest[:length]}"


def _normalize_feedback(feedback: AnalysisFeedback | Mapping[str, Any]) -> AnalysisFeedback:
    if isinstance(feedback, AnalysisFeedback):
        return feedback
    return AnalysisFeedback.model_validate(dict(feedback))


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if value is None or value == "":
        return []
    return [str(value)]


def _stringify_metadata_value(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if value is None:
        return ""
    return str(value)


def _stringify_metadata(metadata: Mapping[str, Any]) -> dict[str, str]:
    return {key: _stringify_metadata_value(value) for key, value in metadata.items() if value is not None}


def _runtime_event_dicts(events: list[Any] | None) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for event in events or []:
        if isinstance(event, Mapping):
            normalized.append(dict(event))
            continue
        dumper = getattr(event, "model_dump", None)
        if dumper is not None:
            payload = dumper(mode="json")
            if isinstance(payload, Mapping):
                normalized.append(dict(payload))
    return normalized


def _runtime_artifacts(artifacts: list[ArtifactRef] | list[Mapping[str, Any]] | None) -> list[ArtifactRef]:
    normalized: list[ArtifactRef] = []
    for index, artifact in enumerate(artifacts or []):
        if isinstance(artifact, ArtifactRef):
            normalized.append(artifact)
            continue
        body = dict(artifact)
        metadata = dict(body.get("metadata") or {}) if isinstance(body.get("metadata"), Mapping) else {}
        extras = {
            key: value
            for key, value in body.items()
            if key
            not in {
                "artifact_id",
                "artifactId",
                "id",
                "kind",
                "type",
                "artifact_type",
                "uri",
                "url",
                "href",
                "path",
                "label",
                "name",
                "title",
                "metadata",
            }
        }
        metadata.update(extras)
        uri = body.get("uri") or body.get("url") or body.get("href") or body.get("path")
        normalized.append(
            ArtifactRef(
                artifact_id=str(body.get("artifact_id") or body.get("artifactId") or body.get("id") or f"artifact-{index + 1}"),
                kind=str(body.get("kind") or body.get("type") or body.get("artifact_type") or "artifact"),
                uri=str(uri) if uri is not None else None,
                label=str(body.get("label") or body.get("name") or body.get("title") or "") or None,
                metadata=metadata,
            )
        )
    return normalized


def _select_terminal_event(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    for event in reversed(events):
        event_type = str(event.get("event_type") or "")
        status = str(event.get("status") or "")
        if event_type.endswith("_failed") or event_type == "run_failed" or status in {"failed", "partial"}:
            return event
    return events[-1] if events else None


def _runtime_subject_value(run: RunRecord, *, subject_kind: str | None = None, subject_id: str | None = None) -> tuple[str, str]:
    resolved_kind = subject_kind or run.subject_kind or run.metadata.get("subject_kind") or "run"
    resolved_id = subject_id or run.subject_id or run.metadata.get("subject_id") or run.flow_id or run.run_id
    return str(resolved_kind), str(resolved_id)


def _normalize_runtime_run(run: RunRecord | Mapping[str, Any]) -> RunRecord:
    if isinstance(run, RunRecord):
        return run
    body = dict(run)
    metadata = dict(body.get("metadata") or {}) if isinstance(body.get("metadata"), Mapping) else {}
    run_id = str(body.get("run_id") or metadata.get("run_id") or body.get("request_id") or uuid4().hex)
    runtime_id = str(body.get("runtime_id") or metadata.get("runtime_id") or "runtime")
    runtime_session_id = str(body.get("runtime_session_id") or metadata.get("runtime_session_id") or "")
    if not runtime_session_id:
        raise ValueError("runtime_session_id is required for evolution normalization")
    subject_kind = str(body.get("subject_kind") or metadata.get("subject_kind") or "run")
    subject_id = str(body.get("subject_id") or metadata.get("subject_id") or run_id)
    agent_id = str(body.get("agent_id") or metadata.get("agent_id") or subject_id or "runtime-agent")
    flow_id = str(body.get("flow_id") or metadata.get("flow_id") or body.get("preset_id") or subject_id or run_id)
    status = str(body.get("status") or metadata.get("status") or ("failed" if body.get("ok") is False else "succeeded"))
    summary = str(body.get("summary") or metadata.get("summary") or run_id)
    started_at = body.get("started_at") or body.get("startedAt")
    finished_at = body.get("finished_at") or body.get("finishedAt")
    return RunRecord(
        run_id=run_id,
        runtime_id=runtime_id,
        runtime_session_id=runtime_session_id,
        subject_kind=subject_kind,
        subject_id=subject_id,
        agent_id=agent_id,
        flow_id=flow_id,
        status=status,
        ok=bool(body.get("ok")) if "ok" in body else status not in {"failed", "partial", "error"},
        summary=summary,
        input_payload=_mapping(body.get("input_payload") or body.get("input") or body.get("inputs")),
        output_payload=_mapping(body.get("output_payload") or body.get("output") or body.get("result")),
        skill_ids=_string_list(body.get("skill_ids")),
        provider_ids=_string_list(body.get("provider_ids")),
        artifacts=[],
        traces=[],
        trace=RunTrace(trace_id=f"trace-{run_id}", run_id=run_id, runtime_session_id=runtime_session_id),
        failure=None,
        started_at=started_at or utc_now(),
        finished_at=finished_at or utc_now(),
        metadata=metadata,
    )


def _normalize_evaluation(evaluation: EvaluationRecord | Mapping[str, Any]) -> EvaluationRecord:
    if isinstance(evaluation, EvaluationRecord):
        return evaluation
    return EvaluationRecord.model_validate(dict(evaluation))


def _candidate_type_for(evaluation: EvaluationRecord, candidate_type: str | None = None) -> str:
    resolved = candidate_type or str(evaluation.metadata.get("candidate_type") or evaluation.subject_type or "general")
    return resolved.strip() or "general"


def _target_skill_name_for(evaluation: EvaluationRecord, target_skill_name: str | None = None) -> str:
    resolved = target_skill_name or str(
        evaluation.metadata.get("target_skill_name")
        or evaluation.subject_id
        or evaluation.metadata.get("subject_id")
        or evaluation.run_id
    )
    return resolved.strip() or evaluation.run_id


def _target_skill_kind_for(evaluation: EvaluationRecord, target_skill_kind: str | None = None) -> str:
    resolved = target_skill_kind or str(
        evaluation.metadata.get("target_skill_kind")
        or evaluation.metadata.get("skill_kind")
        or evaluation.subject_type
        or "general"
    )
    return resolved.strip() or "general"


def _candidate_manifest_preview(
    evaluation: EvaluationRecord,
    *,
    target_skill_name: str,
    target_skill_kind: str,
    candidate_type: str,
) -> dict[str, Any]:
    return {
        "candidate": {
            "target_skill_name": target_skill_name,
            "target_skill_kind": target_skill_kind,
            "candidate_type": candidate_type,
            "source_run_id": evaluation.run_id,
            "source_evaluation_id": evaluation.evaluation_id,
        },
        "evaluation": {
            "overall_grade": evaluation.overall_grade,
            "runtime_pass": evaluation.runtime_pass,
            "contract_pass": evaluation.contract_pass,
            "task_match_score": evaluation.task_match_score,
            "summary": evaluation.summary,
            "findings": list(evaluation.findings),
        },
    }


def build_skill_candidate(
    evaluation: EvaluationRecord | Mapping[str, Any],
    *,
    target_skill_name: str | None = None,
    target_skill_kind: str | None = None,
    candidate_type: str | None = None,
    status: str = "generated",
    generation_spec: dict[str, Any] | None = None,
    manifest_preview: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    registry: EvolutionRegistryProtocol | None = None,
) -> SkillCandidate:
    normalized = _normalize_evaluation(evaluation)
    resolved_name = _target_skill_name_for(normalized, target_skill_name)
    resolved_kind = _target_skill_kind_for(normalized, target_skill_kind)
    resolved_type = _candidate_type_for(normalized, candidate_type)
    resolved_generation_spec = {
        "run_id": normalized.run_id,
        "evaluation_id": normalized.evaluation_id,
        "runtime_session_id": normalized.runtime_session_id,
        "summary": normalized.summary,
        "findings": list(normalized.findings),
        "overall_grade": normalized.overall_grade,
        "runtime_pass": normalized.runtime_pass,
        "contract_pass": normalized.contract_pass,
        "task_match_score": normalized.task_match_score,
        **(generation_spec or {}),
    }
    resolved_manifest_preview = manifest_preview or _candidate_manifest_preview(
        normalized,
        target_skill_name=resolved_name,
        target_skill_kind=resolved_kind,
        candidate_type=resolved_type,
    )
    candidate_id = _hashed_id(
        "candidate",
        {
            "run_id": normalized.run_id,
            "evaluation_id": normalized.evaluation_id,
            "target_skill_name": resolved_name,
            "target_skill_kind": resolved_kind,
            "candidate_type": resolved_type,
            "summary": normalized.summary,
            "findings": normalized.findings,
            "overall_grade": normalized.overall_grade,
        },
    )
    candidate = SkillCandidate(
        candidate_id=candidate_id,
        source_run_id=normalized.run_id,
        source_evaluation_id=normalized.evaluation_id,
        candidate_type=resolved_type,
        target_skill_name=resolved_name,
        target_skill_kind=resolved_kind,
        change_summary=str(
            normalized.metadata.get("change_summary")
            or normalized.metadata.get("candidate_change_summary")
            or normalized.suggested_action
            or normalized.failure_reason
            or normalized.summary
            or "candidate generated from evaluation"
        ).strip(),
        generation_spec=resolved_generation_spec,
        manifest_preview=resolved_manifest_preview,
        status=status,
        validation_status="pending",
        package_path=normalized.metadata.get("package_path"),
        bundle_sha256=normalized.metadata.get("bundle_sha256"),
        runtime_session_id=normalized.runtime_session_id,
        metadata={
            "evaluation_id": normalized.evaluation_id,
            "overall_grade": normalized.overall_grade,
            "runtime_pass": normalized.runtime_pass,
            "contract_pass": normalized.contract_pass,
            "task_match_score": normalized.task_match_score,
            "failure_reason": normalized.failure_reason,
            "suggested_action": normalized.suggested_action,
            "evidence_refs": list(normalized.evidence_refs),
            "trace_ids": list(normalized.trace_ids),
            "event_ids": list(normalized.event_ids),
            "artifact_ids": list(normalized.artifact_ids),
            **(metadata or {}),
        },
    )
    if registry is not None:
        record = getattr(registry, "record_candidate", None)
        if callable(record):
            record(candidate)
        else:  # pragma: no cover - compatibility fallback
            registry.record_case(
                {
                    "case_id": candidate.candidate_id,
                    "run_id": candidate.source_run_id,
                    "runtime_session_id": candidate.runtime_session_id,
                    "subject_id": candidate.target_skill_name,
                    "status": candidate.status,
                    "summary": candidate.change_summary,
                    "payload_json": candidate.model_dump(mode="json"),
                }
            )
    return candidate


def create_promotion_record(
    candidate: SkillCandidate | Mapping[str, Any],
    *,
    package_path: str | None = None,
    bundle_sha256: str | None = None,
    validation_status: str = "validated",
    registry_status: str = "pending",
    package_manifest: dict[str, Any] | None = None,
    validation_report: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    registry: EvolutionRegistryProtocol | None = None,
) -> PromotionRecord:
    normalized = candidate if isinstance(candidate, SkillCandidate) else SkillCandidate.model_validate(dict(candidate))
    candidate_slug = _slugify(f"{normalized.target_skill_name}-{normalized.candidate_id}")
    resolved_bundle_sha256 = bundle_sha256 or normalized.bundle_sha256 or hashlib.sha256(_stable_payload(normalized.manifest_preview).encode("utf-8")).hexdigest()
    resolved_package_manifest = package_manifest or {
        "candidate": normalized.manifest_preview.get("candidate", {}),
        "evaluation": normalized.manifest_preview.get("evaluation", {}),
    }
    resolved_validation_report = validation_report or {
        "validation_status": validation_status,
        "candidate_id": normalized.candidate_id,
        "target_skill_name": normalized.target_skill_name,
        "target_skill_kind": normalized.target_skill_kind,
        "runtime_session_id": normalized.runtime_session_id,
    }
    promotion_id = _hashed_id(
        "promotion",
        {
            "candidate_id": normalized.candidate_id,
            "target_skill_name": normalized.target_skill_name,
            "validation_status": validation_status,
            "registry_status": registry_status,
            "bundle_sha256": resolved_bundle_sha256,
        },
    )
    promotion = PromotionRecord(
        promotion_id=promotion_id,
        candidate_id=normalized.candidate_id,
        source_run_id=normalized.source_run_id,
        source_evaluation_id=normalized.source_evaluation_id,
        candidate_type=normalized.candidate_type,
        candidate_slug=candidate_slug,
        target_skill_name=normalized.target_skill_name,
        target_skill_kind=normalized.target_skill_kind,
        package_path=package_path or normalized.package_path,
        bundle_sha256=resolved_bundle_sha256,
        validation_status=validation_status,
        registry_status=registry_status,
        package_manifest=resolved_package_manifest,
        validation_report=resolved_validation_report,
        lineage={
            "candidate_id": normalized.candidate_id,
            "source_run_id": normalized.source_run_id,
            "source_evaluation_id": normalized.source_evaluation_id,
            "candidate_type": normalized.candidate_type,
            "target_skill_name": normalized.target_skill_name,
            "target_skill_kind": normalized.target_skill_kind,
        },
        runtime_session_id=normalized.runtime_session_id,
        metadata={
            "candidate_status": normalized.status,
            "candidate_validation_status": normalized.validation_status,
            "candidate_manifest_preview": normalized.manifest_preview,
            "candidate_generation_spec": normalized.generation_spec,
            "candidate_metadata": normalized.metadata,
            **(metadata or {}),
        },
    )
    if registry is not None:
        record = getattr(registry, "record_promotion", None)
        if callable(record):
            record(promotion)
        else:  # pragma: no cover - compatibility fallback
            registry.record_submission(
                {
                    "submission_id": promotion.promotion_id,
                    "run_id": promotion.source_run_id,
                    "runtime_session_id": promotion.runtime_session_id,
                    "subject_id": promotion.target_skill_name,
                    "status": promotion.validation_status,
                    "summary": promotion.candidate_slug,
                    "payload_json": promotion.model_dump(mode="json"),
                }
            )
    return promotion


def advance_evaluation(
    evaluation: EvaluationRecord | Mapping[str, Any],
    *,
    target_skill_name: str | None = None,
    target_skill_kind: str | None = None,
    candidate_type: str | None = None,
    package_path: str | None = None,
    bundle_sha256: str | None = None,
    validation_status: str = "validated",
    registry_status: str = "pending",
    registry: EvolutionRegistryProtocol | None = None,
) -> dict[str, Any]:
    normalized = _normalize_evaluation(evaluation)
    candidate = build_skill_candidate(
        normalized,
        target_skill_name=target_skill_name,
        target_skill_kind=target_skill_kind,
        candidate_type=candidate_type,
        registry=registry,
    )
    promotion = create_promotion_record(
        candidate,
        package_path=package_path,
        bundle_sha256=bundle_sha256,
        validation_status=validation_status,
        registry_status=registry_status,
        registry=registry,
    )
    return {
        "evaluation": normalized,
        "candidate": candidate,
        "promotion": promotion,
    }


def build_runtime_feedback(
    run: RunRecord | Mapping[str, Any],
    *,
    events: list[Any] | None = None,
    artifacts: list[ArtifactRef] | list[Mapping[str, Any]] | None = None,
    subject_kind: str | None = None,
    subject_id: str | None = None,
    skill_id: str | None = None,
    action_id: str | None = None,
    status: str | None = None,
    summary: str | None = None,
    error_code: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> AnalysisFeedback:
    normalized_run = _normalize_runtime_run(run)
    runtime_events = _runtime_event_dicts(events)
    runtime_artifacts = _runtime_artifacts(artifacts or list(normalized_run.artifacts))
    terminal_event = _select_terminal_event(runtime_events)
    resolved_subject_kind, resolved_subject_id = _runtime_subject_value(
        normalized_run,
        subject_kind=subject_kind,
        subject_id=subject_id,
    )
    resolved_status = (status or str((terminal_event or {}).get("status") or normalized_run.status or "failed")).strip().lower()
    resolved_skill_id = (
        skill_id
        or str((terminal_event or {}).get("skill_id") or normalized_run.metadata.get("skill_id") or normalized_run.flow_id or normalized_run.agent_id or "runtime")
    ).strip()
    resolved_action_id = (
        action_id
        or str((terminal_event or {}).get("action_id") or (terminal_event or {}).get("event_type") or normalized_run.metadata.get("action_id") or "runtime_event")
    ).strip()
    resolved_summary = (
        summary
        or str((terminal_event or {}).get("summary") or normalized_run.summary or normalized_run.metadata.get("summary") or "")
    ).strip()
    resolved_error_code = error_code
    if resolved_error_code is None and terminal_event is not None:
        resolved_error_code = str((terminal_event.get("metadata") or {}).get("error_code") or (terminal_event.get("payload") or {}).get("error_code") or "").strip() or None
    if resolved_error_code is None and not normalized_run.ok:
        failure = normalized_run.failure
        if failure is not None:
            resolved_error_code = failure.code
    event_ids = [str(item.get("event_id") or "") for item in runtime_events if str(item.get("event_id") or "").strip()]
    trace_ids = [trace.trace_id for trace in normalized_run.traces] or [normalized_run.trace.trace_id]
    event_types = [str(item.get("event_type") or "") for item in runtime_events if str(item.get("event_type") or "").strip()]
    feedback = AnalysisFeedback(
        run_id=normalized_run.run_id,
        skill_id=resolved_skill_id,
        action_id=resolved_action_id,
        status=resolved_status,
        summary=resolved_summary or normalized_run.summary,
        artifacts=runtime_artifacts,
        error_code=resolved_error_code,
        metadata=_stringify_metadata({
            "subject_kind": resolved_subject_kind,
            "subject_id": resolved_subject_id,
            "source_kind": "runtime",
            "source_id": normalized_run.run_id,
            "run_status": normalized_run.status,
            "ok": normalized_run.ok,
            "event_ids": event_ids,
            "event_types": event_types,
            "trace_ids": trace_ids,
            "artifact_ids": [artifact.artifact_id for artifact in runtime_artifacts],
            "step_ids": [str(item.get("step_id") or "") for item in runtime_events if str(item.get("step_id") or "").strip()],
            "skill_ids": [str(item.get("skill_id") or "") for item in runtime_events if str(item.get("skill_id") or "").strip()],
            "action_ids": [str(item.get("action_id") or "") for item in runtime_events if str(item.get("action_id") or "").strip()],
            **(normalized_run.metadata or {}),
            **(metadata or {}),
        }),
    )
    return feedback


def advance_runtime_feedback(
    run: RunRecord | Mapping[str, Any],
    *,
    events: list[Any] | None = None,
    artifacts: list[ArtifactRef] | list[Mapping[str, Any]] | None = None,
    subject_kind: str | None = None,
    subject_id: str | None = None,
    skill_id: str | None = None,
    action_id: str | None = None,
    status: str | None = None,
    summary: str | None = None,
    error_code: str | None = None,
    metadata: dict[str, Any] | None = None,
    registry: EvolutionRegistryProtocol | None = None,
) -> dict[str, Any]:
    normalized_run = _normalize_runtime_run(run)
    runtime_events = _runtime_event_dicts(events)
    runtime_artifacts = _runtime_artifacts(artifacts or list(normalized_run.artifacts))
    terminal_event = _select_terminal_event(runtime_events)
    resolved_status = (status or str((terminal_event or {}).get("status") or normalized_run.status or "failed")).strip().lower()
    resolved_summary = (
        summary
        or str((terminal_event or {}).get("summary") or normalized_run.summary or normalized_run.metadata.get("summary") or "")
    ).strip()
    failure_reason = error_code
    if failure_reason is None and terminal_event is not None:
        failure_reason = str((terminal_event.get("metadata") or {}).get("error_code") or (terminal_event.get("payload") or {}).get("error_code") or "").strip() or None
    subject_kind_resolved, subject_id_resolved = _runtime_subject_value(
        normalized_run,
        subject_kind=subject_kind,
        subject_id=subject_id,
    )
    evidence_refs = list(dict.fromkeys([*normalized_run.trace_ids, *normalized_run.artifact_ids]))
    if not evidence_refs:
        evidence_refs = [trace.trace_id for trace in normalized_run.traces] or [normalized_run.trace.trace_id]
    runtime_result = RuntimeEvaluationResult(
        passed=resolved_status not in {"failed", "partial", "error"} and normalized_run.ok,
        status=resolved_status,
        summary=resolved_summary or normalized_run.summary,
        error_code=failure_reason,
        evidence_refs=evidence_refs,
        metadata={
            "source_kind": "runtime",
            "source_id": normalized_run.run_id,
            "subject_kind": subject_kind_resolved,
            "subject_id": subject_id_resolved,
            **(metadata or {}),
        },
    )
    contract_result = ContractEvaluationResult(
        passed=bool(resolved_summary),
        status="pass" if resolved_summary else "fail",
        summary=resolved_summary or normalized_run.summary,
        evidence_refs=evidence_refs,
        metadata={
            "source_kind": "runtime",
            "source_id": normalized_run.run_id,
            "subject_kind": subject_kind_resolved,
            "subject_id": subject_id_resolved,
            **(metadata or {}),
        },
    )
    task_match_score = float((metadata or {}).get("task_match_score") or (1.0 if normalized_run.ok else 0.0))
    task_match_result = TaskMatchEvaluationResult(
        passed=task_match_score >= float((metadata or {}).get("task_match_threshold") or 0.8),
        status="pass" if task_match_score >= 0.8 else "warn" if task_match_score >= 0.5 else "fail",
        summary=resolved_summary or normalized_run.summary,
        score=task_match_score,
        threshold=float((metadata or {}).get("task_match_threshold") or 0.8),
        evidence_refs=evidence_refs,
        metadata={
            "source_kind": "runtime",
            "source_id": normalized_run.run_id,
            "subject_kind": subject_kind_resolved,
            "subject_id": subject_id_resolved,
            **(metadata or {}),
        },
    )
    evaluation = EvaluationRecord(
        evaluation_id=_hashed_id(
            "eval",
            {
                "run_id": normalized_run.run_id,
                "runtime_session_id": normalized_run.runtime_session_id,
                "subject_kind": subject_kind_resolved,
                "subject_id": subject_id_resolved,
                "summary": resolved_summary or normalized_run.summary,
                "status": resolved_status,
                "failure_reason": failure_reason,
                "task_match_score": task_match_score,
            },
        ),
        run_id=normalized_run.run_id,
        runtime_session_id=normalized_run.runtime_session_id,
        subject_type=subject_kind_resolved,
        subject_id=subject_id_resolved,
        runtime_result=runtime_result,
        contract_result=contract_result,
        task_match_result=task_match_result,
        runtime_pass=runtime_result.passed,
        contract_pass=contract_result.passed,
        task_match_score=task_match_score,
        overall_grade="pending",
        failure_reason=failure_reason,
        suggested_action=str((metadata or {}).get("suggested_action") or (metadata or {}).get("recommendation") or "").strip() or None,
        summary=resolved_summary or normalized_run.summary,
        trace_ids=[trace.trace_id for trace in normalized_run.traces] or [normalized_run.trace.trace_id],
        event_ids=[str(item.get("event_id") or "") for item in runtime_events if str(item.get("event_id") or "").strip()],
        event_types=[str(item.get("event_type") or "") for item in runtime_events if str(item.get("event_type") or "").strip()],
        artifact_ids=[artifact.artifact_id for artifact in runtime_artifacts],
        evidence_refs=evidence_refs,
        checks=[],
        findings=[],
        metadata={
            "source_kind": "runtime",
            "source_id": normalized_run.run_id,
            "subject_kind": subject_kind_resolved,
            "subject_id": subject_id_resolved,
            "runtime_events": runtime_events,
            "runtime_artifacts": [artifact.model_dump(mode="json") for artifact in runtime_artifacts],
            **(metadata or {}),
        },
    )
    return advance_evaluation(evaluation, registry=registry)


def _slugify(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", value.lower().strip())
    return text.strip("-") or "runtime"


def intake_feedback(
    feedback: AnalysisFeedback | Mapping[str, Any],
    *,
    registry: EvolutionRegistryProtocol | None = None,
) -> AnalysisFeedback:
    normalized = _normalize_feedback(feedback)
    if registry is not None:
        registry.record_feedback(normalized)
    return normalized


def create_case(
    feedback: AnalysisFeedback | Mapping[str, Any],
    *,
    registry: EvolutionRegistryProtocol | None = None,
) -> AnalysisCase:
    normalized = _normalize_feedback(feedback)
    if registry is not None and not isinstance(feedback, AnalysisFeedback):
        registry.record_feedback(normalized)
    status = normalized.status.strip().lower()
    if status not in _CASEFUL_STATUSES:
        raise ValueError(f"feedback status does not create a case: {normalized.status}")

    source_payload = {
        "run_id": normalized.run_id,
        "skill_id": normalized.skill_id,
        "action_id": normalized.action_id,
        "status": status,
        "summary": normalized.summary.strip(),
        "error_code": normalized.error_code,
        "artifacts": [artifact.model_dump(mode="json") for artifact in normalized.artifacts],
        "metadata": normalized.metadata,
    }
    case_id = _hashed_id("case", source_payload)
    problem_type = "failed_run" if status == "failed" else "partial_run"
    severity = "high" if status == "failed" else "medium"
    pattern_summary = f"{normalized.skill_id}/{normalized.action_id} returned {status}: {normalized.summary.strip()}"
    tags = [normalized.skill_id, normalized.action_id, status]
    if normalized.error_code:
        tags.append(normalized.error_code)
    case = AnalysisCase(
        case_id=case_id,
        source={
            "run_id": normalized.run_id,
            "skill_id": normalized.skill_id,
            "action_id": normalized.action_id,
            "status": normalized.status,
            "summary": normalized.summary,
            "artifacts": [artifact.model_dump(mode="json") for artifact in normalized.artifacts],
            "error_code": normalized.error_code,
            "metadata": normalized.metadata,
        },
        pattern={"problem_type": problem_type, "summary": pattern_summary, "tags": tags},
        evidence=[artifact for artifact in normalized.artifacts],
        severity=severity,
        metadata={
            "analysis_scope": "runtime",
            "feedback_status": normalized.status,
            "case_signature": _hashed_id("sig", source_payload),
        },
    )
    if registry is not None:
        registry.record_case(case)
    return case


def create_candidate_proposal(
    case: AnalysisCase | Mapping[str, Any],
    *,
    target_skill_name: str | None = None,
    decision_mode: str = "candidate",
    target_layer: str | None = None,
    registry: EvolutionRegistryProtocol | None = None,
) -> AnalysisProposal:
    normalized = case if isinstance(case, AnalysisCase) else AnalysisCase.model_validate(dict(case))
    source_metadata = dict(normalized.source.metadata)
    resolved_target_skill_name = target_skill_name or source_metadata.get("target_skill_name") or normalized.source.skill_id
    resolved_target_layer = target_layer or source_metadata.get("target_layer") or source_metadata.get("subject_kind") or "skill"
    change_summary = (
        "Harden partial-run recovery and completeness checks"
        if normalized.pattern.problem_type == "partial_run"
        else "Add explicit failure surfacing and fallback handling"
    )
    proposal_id = _hashed_id(
        "proposal",
        {
            "case_id": normalized.case_id,
            "problem_type": normalized.pattern.problem_type,
            "summary": normalized.pattern.summary,
            "severity": normalized.severity,
            "target_skill_name": resolved_target_skill_name,
            "change_summary": change_summary,
            "decision_mode": decision_mode,
            "target_layer": resolved_target_layer,
        },
    )
    proposal = AnalysisProposal(
        proposal_id=proposal_id,
        case_id=normalized.case_id,
        target_skill_name=resolved_target_skill_name,
        decision_mode=decision_mode,
        change_summary=change_summary,
        target_layer=resolved_target_layer,
        metadata={
            "evolution_scope": source_metadata.get("subject_kind", "skill"),
            "case_problem_type": normalized.pattern.problem_type,
            "case_severity": normalized.severity,
            "case_signature": normalized.metadata.get("case_signature", ""),
            "subject_id": source_metadata.get("subject_id", ""),
            "agent_id": source_metadata.get("agent_id", ""),
            "flow_id": source_metadata.get("flow_id", ""),
            "provider_id": source_metadata.get("provider_id", ""),
        },
    )
    if registry is not None:
        registry.record_proposal(proposal)
    return proposal


def create_promotion_submission(
    proposal: AnalysisProposal | Mapping[str, Any],
    *,
    case: AnalysisCase | Mapping[str, Any] | None = None,
    run_id: str | None = None,
    registry: EvolutionRegistryProtocol | None = None,
) -> PromotionSubmission:
    normalized_proposal = proposal if isinstance(proposal, AnalysisProposal) else AnalysisProposal.model_validate(dict(proposal))
    normalized_case = None
    if case is not None:
        normalized_case = case if isinstance(case, AnalysisCase) else AnalysisCase.model_validate(dict(case))
    case_id = normalized_proposal.case_id if normalized_case is None else normalized_case.case_id
    resolved_run_id = run_id or (normalized_case.source.run_id if normalized_case is not None else f"run-{case_id}")
    candidate_id = _hashed_id("candidate", {"proposal_id": normalized_proposal.proposal_id, "case_id": case_id, "run_id": resolved_run_id})
    candidate_slug = _slugify(f"{normalized_proposal.target_skill_name}-{candidate_id}")
    manifest = {
        "candidate": {
            "id": candidate_id,
            "slug": candidate_slug,
            "proposal_id": normalized_proposal.proposal_id,
            "case_id": case_id,
            "target_skill_name": normalized_proposal.target_skill_name,
            "decision_mode": normalized_proposal.decision_mode,
            "change_summary": normalized_proposal.change_summary,
            "target_layer": normalized_proposal.target_layer,
        },
        "lineage": {
            "case_id": case_id,
            "proposal_id": normalized_proposal.proposal_id,
            "decision_mode": normalized_proposal.decision_mode,
            "target_skill_name": normalized_proposal.target_skill_name,
        },
    }
    submission_id = _hashed_id(
        "submission",
        {
            "candidate_id": candidate_id,
            "proposal_id": normalized_proposal.proposal_id,
            "case_id": case_id,
            "run_id": resolved_run_id,
            "manifest": manifest,
        },
    )
    bundle_sha256 = hashlib.sha256(_stable_payload(manifest).encode("utf-8")).hexdigest()
    bundle_path_obj = registry.submission_path(submission_id) if registry is not None else None
    bundle_path = str(bundle_path_obj) if bundle_path_obj is not None else None
    submission = PromotionSubmission(
        submission_id=submission_id,
        case_id=case_id,
        proposal_id=normalized_proposal.proposal_id,
        candidate_id=candidate_id,
        candidate_slug=candidate_slug,
        run_id=resolved_run_id,
        target_skill_name=normalized_proposal.target_skill_name,
        decision_mode=normalized_proposal.decision_mode,
        bundle_path=bundle_path,
        bundle_sha256=bundle_sha256,
        evaluation_summary={
            "case_id": case_id,
            "proposal_id": normalized_proposal.proposal_id,
            "decision_mode": normalized_proposal.decision_mode,
            "change_summary": normalized_proposal.change_summary,
            "target_layer": normalized_proposal.target_layer,
        },
        manifest=manifest,
        lineage=manifest["lineage"],
        metadata={
            "evolution_scope": normalized_proposal.metadata.get("evolution_scope", normalized_proposal.target_layer),
            "candidate_id": candidate_id,
            "candidate_slug": candidate_slug,
            "subject_id": normalized_proposal.metadata.get("subject_id", ""),
        },
    )
    if registry is not None:
        registry.record_submission(submission)
    return submission


def advance_feedback(
    feedback: AnalysisFeedback | Mapping[str, Any],
    *,
    registry: EvolutionRegistryProtocol | None = None,
) -> dict[str, Any]:
    normalized = intake_feedback(feedback, registry=registry)
    metadata = dict(normalized.metadata)
    runtime_result = RuntimeEvaluationResult(
        passed=normalized.status.strip().lower() not in {"failed", "partial", "error"},
        status=normalized.status.strip().lower(),
        summary=normalized.summary,
        error_code=normalized.error_code,
        evidence_refs=[artifact.artifact_id for artifact in normalized.artifacts],
        metadata=metadata,
    )
    contract_result = ContractEvaluationResult(
        passed=bool(normalized.summary.strip()),
        status="pass" if normalized.summary.strip() else "fail",
        summary=normalized.summary,
        evidence_refs=[artifact.artifact_id for artifact in normalized.artifacts],
        metadata=metadata,
    )
    task_match_score = float(metadata.get("task_match_score") or (1.0 if runtime_result.passed else 0.0))
    task_match_result = TaskMatchEvaluationResult(
        passed=task_match_score >= 0.8,
        status="pass" if task_match_score >= 0.8 else "warn" if task_match_score >= 0.5 else "fail",
        summary=normalized.summary,
        score=task_match_score,
        threshold=float(metadata.get("task_match_threshold") or 0.8),
        evidence_refs=[artifact.artifact_id for artifact in normalized.artifacts],
        metadata=metadata,
    )
    evaluation = EvaluationRecord(
        evaluation_id=_hashed_id(
            "eval",
            {
                "run_id": normalized.run_id,
                "skill_id": normalized.skill_id,
                "action_id": normalized.action_id,
                "status": normalized.status,
                "summary": normalized.summary,
                "error_code": normalized.error_code,
            },
        ),
        run_id=normalized.run_id,
        runtime_session_id=str(metadata.get("runtime_session_id") or normalized.metadata.get("runtime_session_id") or normalized.run_id),
        subject_type=str(metadata.get("subject_kind") or metadata.get("subject_type") or "skill"),
        subject_id=str(metadata.get("subject_id") or normalized.skill_id or normalized.run_id),
        runtime_result=runtime_result,
        contract_result=contract_result,
        task_match_result=task_match_result,
        runtime_pass=runtime_result.passed,
        contract_pass=contract_result.passed,
        task_match_score=task_match_score,
        overall_grade="pending",
        failure_reason=normalized.error_code or (normalized.summary if not runtime_result.passed else None),
        suggested_action=metadata.get("suggested_action") or metadata.get("recommendation"),
        summary=normalized.summary,
        trace_ids=_string_list(metadata.get("trace_ids")),
        event_ids=_string_list(metadata.get("event_ids")),
        event_types=_string_list(metadata.get("event_types")),
        artifact_ids=[artifact.artifact_id for artifact in normalized.artifacts],
        evidence_refs=[artifact.artifact_id for artifact in normalized.artifacts],
        checks=[],
        findings=[],
        metadata=metadata,
    )
    return advance_evaluation(evaluation, registry=registry)
