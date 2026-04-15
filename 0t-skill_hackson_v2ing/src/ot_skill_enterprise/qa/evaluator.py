from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from ot_skill_enterprise.runs.models import RunRecord
from .models import (
    ContractEvaluationResult,
    EvaluationRecord,
    RuntimeEvaluationResult,
    TaskMatchEvaluationResult,
)
from .store import EvaluationStore, build_evaluation_store


_SEMANTIC_REVIEW_STATUSES = {
    "generate",
    "generate_with_low_confidence",
    "insufficient_signal",
    "no_pattern_detected",
    "runtime_failed",
    "needs_manual_review",
}

_RUNTIME_STATUS_ALIASES = {
    "pass": "succeeded",
    "passed": "succeeded",
    "success": "succeeded",
    "succeeded": "succeeded",
    "fail": "runtime_failed",
    "failed": "runtime_failed",
    "failure": "runtime_failed",
    "error": "runtime_failed",
    "errored": "runtime_failed",
    "partial": "runtime_failed",
    "run_failed": "runtime_failed",
    "runtime_failed": "runtime_failed",
}


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


def _runtime_events(run: RunRecord) -> list[dict[str, Any]]:
    seen: set[str] = set()
    events: list[dict[str, Any]] = []
    for event in run.runtime_events:
        if event.event_id in seen:
            continue
        seen.add(event.event_id)
        events.append(event.model_dump(mode="json"))
    for trace in run.traces or [run.trace]:
        if trace is None:
            continue
        for event in trace.events:
            if event.event_id in seen:
                continue
            seen.add(event.event_id)
            events.append(event.model_dump(mode="json"))
    return events


def _derive_findings(events: list[dict[str, Any]], summary: str) -> list[str]:
    findings: list[str] = []
    for event in events:
        event_type = str(event.get("event_type") or "")
        status = str(event.get("status") or "")
        if event_type.endswith("_failed") or status in {"failed", "partial"} or event_type == "run_failed":
            text = str(event.get("summary") or event.get("payload", {}).get("summary") or summary).strip()
            if text and text not in findings:
                findings.append(text)
    return findings


def _normalize_grade(status: str, ok: bool) -> str:
    normalized = status.strip().lower()
    if normalized in {"succeeded", "success", "passed"}:
        return "pass"
    if normalized in {"failed", "failure", "errored", "error"}:
        return "fail"
    if normalized == "runtime_failed":
        return "fail"
    if normalized in {"warn", "warning"}:
        return "warn"
    if normalized == "generate":
        return "pass" if ok else "fail"
    if normalized == "generate_with_low_confidence":
        return "warn" if ok else "fail"
    if normalized in {"insufficient_signal", "no_pattern_detected"}:
        return "warn" if ok else "fail"
    if normalized in {"pending", "pass", "fail"}:
        return normalized
    return "pass" if ok else "fail"


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if value is None or value == "":
        return []
    return [str(value)]


def _normalize_runtime_status(status: str, ok: bool) -> str:
    normalized = str(status or "").strip().lower()
    if normalized in _RUNTIME_STATUS_ALIASES:
        return _RUNTIME_STATUS_ALIASES[normalized]
    if normalized in _SEMANTIC_REVIEW_STATUSES:
        return "succeeded" if ok else "runtime_failed"
    return "succeeded" if ok else "runtime_failed"


def _normalize_review_status(
    *,
    status: str,
    runtime_status: str,
    runtime_pass: bool,
    contract_pass: bool,
    task_match_score: float,
    task_match_threshold: float,
    has_task_match_score: bool,
    metadata: Mapping[str, Any],
) -> str:
    explicit = str(
        metadata.get("review_status")
        or metadata.get("qa_review_status")
        or metadata.get("semantic_review_status")
        or status
        or ""
    ).strip().lower()
    if explicit in _SEMANTIC_REVIEW_STATUSES:
        return explicit
    if runtime_status == "runtime_failed" or not runtime_pass or not contract_pass:
        return "runtime_failed"
    if explicit in {"generate", "generate_with_low_confidence", "insufficient_signal", "no_pattern_detected"}:
        return explicit
    if has_task_match_score and task_match_score < task_match_threshold * 0.5:
        return "generate_with_low_confidence"
    return "generate"


def classify_review_status(
    *,
    status: str,
    runtime_status: str,
    runtime_pass: bool,
    contract_pass: bool,
    task_match_score: float,
    task_match_threshold: float = 0.8,
    has_task_match_score: bool = False,
    metadata: Mapping[str, Any] | None = None,
) -> str:
    return _normalize_review_status(
        status=status,
        runtime_status=runtime_status,
        runtime_pass=runtime_pass,
        contract_pass=contract_pass,
        task_match_score=task_match_score,
        task_match_threshold=task_match_threshold,
        has_task_match_score=has_task_match_score,
        metadata=metadata or {},
    )


def _build_runtime_result(normalized: RunRecord, *, status: str, metadata: Mapping[str, Any]) -> RuntimeEvaluationResult:
    runtime_status = _normalize_runtime_status(
        str(metadata.get("runtime_status") or status or normalized.status or "pending"),
        normalized.ok,
    )
    runtime_summary = str(
        metadata.get("runtime_summary")
        or metadata.get("summary")
        or normalized.summary
        or runtime_status
    ).strip()
    error_code = metadata.get("runtime_error_code")
    if error_code is None and normalized.failure is not None:
        error_code = normalized.failure.code
    passed = metadata.get("runtime_pass")
    if passed is None:
        passed = normalized.ok and runtime_status not in {"failed", "error", "partial"}
    evidence_refs = _string_list(metadata.get("runtime_evidence_refs"))
    if not evidence_refs:
        evidence_refs = [trace.trace_id for trace in normalized.traces] or [normalized.trace.trace_id]
    return RuntimeEvaluationResult(
        passed=bool(passed),
        status=runtime_status,
        summary=runtime_summary,
        error_code=str(error_code).strip() if error_code is not None and str(error_code).strip() else None,
        evidence_refs=evidence_refs,
        metadata=dict(metadata.get("runtime_metadata") or {}),
    )


def _build_contract_result(
    normalized: RunRecord,
    *,
    subject_kind: str,
    subject_id: str,
    metadata: Mapping[str, Any],
) -> ContractEvaluationResult:
    missing_fields = _string_list(metadata.get("missing_fields"))
    violations = _string_list(metadata.get("contract_violations"))
    contract_summary = str(
        metadata.get("contract_summary")
        or metadata.get("contract_reason")
        or ("contract passed" if not missing_fields and not violations else ", ".join(missing_fields or violations))
        or normalized.summary
    ).strip()
    passed = metadata.get("contract_pass")
    if passed is None:
        passed = bool(subject_kind.strip()) and bool(subject_id.strip()) and not missing_fields and not violations
        if normalized.summary.strip() == "":
            passed = False
    evidence_refs = _string_list(metadata.get("contract_evidence_refs"))
    if not evidence_refs:
        evidence_refs = list(dict.fromkeys([*normalized.trace_ids, *normalized.artifact_ids]))
    return ContractEvaluationResult(
        passed=bool(passed),
        status=str(metadata.get("contract_status") or ("pass" if passed else "fail")).strip().lower(),
        summary=contract_summary,
        missing_fields=missing_fields,
        violations=violations,
        evidence_refs=evidence_refs,
        metadata=dict(metadata.get("contract_metadata") or {}),
    )


def _build_task_match_result(normalized: RunRecord, *, metadata: Mapping[str, Any]) -> TaskMatchEvaluationResult:
    threshold = float(metadata.get("task_match_threshold") or 0.8)
    score_value = metadata.get("task_match_score")
    if score_value is None:
        score_value = 1.0 if normalized.ok else 0.0
    score = float(score_value)
    passed = metadata.get("task_match_pass")
    if passed is None:
        passed = score >= threshold
    task_summary = str(
        metadata.get("task_match_summary")
        or metadata.get("review_summary")
        or ("task matched" if passed else "task mismatch")
    ).strip()
    evidence_refs = _string_list(metadata.get("task_match_evidence_refs"))
    if not evidence_refs:
        evidence_refs = list(dict.fromkeys([*normalized.trace_ids, *normalized.artifact_ids]))
    return TaskMatchEvaluationResult(
        passed=bool(passed),
        status=str(metadata.get("task_match_status") or ("pass" if passed else "warn" if score >= threshold * 0.5 else "fail")).strip().lower(),
        summary=task_summary,
        score=score,
        threshold=threshold,
        evidence_refs=evidence_refs,
        metadata=dict(metadata.get("task_match_metadata") or {}),
    )


@dataclass
class QAEvaluator:
    store: EvaluationStore = field(default_factory=build_evaluation_store)

    def evaluate_run(
        self,
        run: RunRecord | Mapping[str, Any],
        *,
        subject_kind: str = "run",
        subject_id: str | None = None,
        status: str = "pending",
        score: float | None = None,
        summary: str = "",
        findings: list[str] | None = None,
        recommendation: str = "",
        metadata: dict[str, Any] | None = None,
        runtime_result: RuntimeEvaluationResult | Mapping[str, Any] | None = None,
        contract_result: ContractEvaluationResult | Mapping[str, Any] | None = None,
        task_match_result: TaskMatchEvaluationResult | Mapping[str, Any] | None = None,
    ) -> EvaluationRecord:
        normalized = run if isinstance(run, RunRecord) else RunRecord.model_validate(dict(run))
        events = _runtime_events(normalized)
        subject_kind = subject_kind or normalized.subject_kind or normalized.metadata.get("subject_kind", "run")
        subject_id = subject_id or normalized.subject_id or normalized.metadata.get("subject_id") or normalized.flow_id or normalized.run_id
        merged_metadata = {
            **(normalized.metadata or {}),
            **(metadata or {}),
        }
        initial_status = str(merged_metadata.get("review_status") or status or "").strip().lower()
        runtime_status_hint = str(merged_metadata.get("runtime_status") or status or normalized.status or "pending").strip().lower()
        runtime_status = _normalize_runtime_status(runtime_status_hint, normalized.ok)
        semantic_review_status = _normalize_review_status(
            status=initial_status,
            runtime_status=runtime_status,
            runtime_pass=bool(normalized.ok),
            contract_pass=bool(merged_metadata.get("contract_pass", True)),
            task_match_score=float(merged_metadata.get("task_match_score") or 0.0),
            task_match_threshold=float(merged_metadata.get("task_match_threshold") or 0.8),
            has_task_match_score="task_match_score" in merged_metadata,
            metadata=merged_metadata,
        )
        merged_metadata = {
            **merged_metadata,
            "review_status": semantic_review_status,
            "semantic_review_status": semantic_review_status,
        }
        runtime_result_obj = (
            runtime_result
            if isinstance(runtime_result, RuntimeEvaluationResult)
            else RuntimeEvaluationResult.model_validate(dict(runtime_result))
            if runtime_result is not None
            else _build_runtime_result(normalized, status=runtime_status, metadata=merged_metadata)
        )
        contract_result_obj = (
            contract_result
            if isinstance(contract_result, ContractEvaluationResult)
            else ContractEvaluationResult.model_validate(dict(contract_result))
            if contract_result is not None
            else _build_contract_result(normalized, subject_kind=subject_kind, subject_id=subject_id, metadata=merged_metadata)
        )
        if semantic_review_status in _SEMANTIC_REVIEW_STATUSES:
            merged_metadata = {
                **merged_metadata,
                "task_match_status": semantic_review_status,
            }
        task_match_result_obj = (
            task_match_result
            if isinstance(task_match_result, TaskMatchEvaluationResult)
            else TaskMatchEvaluationResult.model_validate(dict(task_match_result))
            if task_match_result is not None
            else _build_task_match_result(normalized, metadata=merged_metadata)
        )
        findings = findings or _derive_findings(events, summary or normalized.summary)
        trace_ids = [trace.trace_id for trace in normalized.traces] or [normalized.trace.trace_id]
        event_ids = [str(event.get("event_id") or "") for event in events if str(event.get("event_id") or "").strip()]
        event_types = [str(event.get("event_type") or "") for event in events if str(event.get("event_type") or "").strip()]
        artifact_ids = [artifact.artifact_id for artifact in normalized.artifacts]
        runtime_pass = bool(runtime_result_obj.passed)
        contract_pass = bool(contract_result_obj.passed)
        task_match_score = float(task_match_result_obj.score or 0.0)
        overall_grade = _normalize_grade(status, normalized.ok)
        if merged_metadata.get("overall_grade"):
            overall_grade = str(merged_metadata.get("overall_grade"))
        elif not runtime_pass or not contract_pass:
            overall_grade = "fail"
        elif task_match_score >= task_match_result_obj.threshold:
            overall_grade = "pass"
        elif task_match_score >= task_match_result_obj.threshold * 0.5:
            overall_grade = "warn"
        if semantic_review_status == "runtime_failed":
            overall_grade = "fail"
        elif semantic_review_status == "generate_with_low_confidence" and overall_grade == "pass":
            overall_grade = "warn"
        elif semantic_review_status in {"insufficient_signal", "no_pattern_detected"} and overall_grade == "pass":
            overall_grade = "warn"
        failure_reason = str(
            merged_metadata.get("failure_reason")
            or runtime_result_obj.summary
            or contract_result_obj.summary
            or task_match_result_obj.summary
            or ""
        ).strip() or None
        suggested_action = str(
            merged_metadata.get("suggested_action")
            or merged_metadata.get("recommendation")
            or recommendation
            or ""
        ).strip() or None
        evidence_refs = list(dict.fromkeys([
            *runtime_result_obj.evidence_refs,
            *contract_result_obj.evidence_refs,
            *task_match_result_obj.evidence_refs,
            *trace_ids,
            *artifact_ids,
        ]))
        payload = {
            "run_id": normalized.run_id,
            "subject_kind": subject_kind,
            "subject_id": subject_id,
            "status": status,
            "score": score,
            "summary": summary,
            "findings": findings,
            "recommendation": recommendation,
            "runtime_pass": runtime_pass,
            "contract_pass": contract_pass,
            "task_match_score": task_match_score,
            "overall_grade": overall_grade,
            "failure_reason": failure_reason,
            "suggested_action": suggested_action,
            "trace_ids": trace_ids,
            "event_ids": event_ids,
            "event_types": event_types,
            "artifact_ids": artifact_ids,
            "evidence_refs": evidence_refs,
        }
        evaluation = EvaluationRecord(
            evaluation_id=_hashed_id("eval", payload),
            run_id=normalized.run_id,
            runtime_session_id=normalized.runtime_session_id,
            subject_type=subject_kind,
            subject_id=subject_id,
            summary=summary or normalized.summary,
            runtime_result=runtime_result_obj,
            contract_result=contract_result_obj,
            task_match_result=task_match_result_obj,
            runtime_pass=runtime_pass,
            contract_pass=contract_pass,
            task_match_score=task_match_score,
            overall_grade=overall_grade,
            failure_reason=failure_reason,
            suggested_action=suggested_action,
            grade=overall_grade,
            checks=[item for item in [recommendation, runtime_result_obj.summary, contract_result_obj.summary, task_match_result_obj.summary] if item],
            trace_ids=trace_ids,
            event_ids=event_ids,
            event_types=event_types,
            artifact_ids=artifact_ids,
            findings=findings,
            evidence_refs=evidence_refs,
            metadata={
                "score": score,
                "recommendation": recommendation,
                "trace_ids": payload["trace_ids"],
                "event_ids": payload["event_ids"],
                "event_types": payload["event_types"],
                "artifact_ids": payload["artifact_ids"],
                "runtime_result": runtime_result_obj.model_dump(mode="json"),
                "contract_result": contract_result_obj.model_dump(mode="json"),
                "task_match_result": task_match_result_obj.model_dump(mode="json"),
                "review_status": semantic_review_status,
                "runtime_pass": runtime_pass,
                "contract_pass": contract_pass,
                "task_match_score": task_match_score,
                "overall_grade": overall_grade,
                "failure_reason": failure_reason,
                "suggested_action": suggested_action,
                "evidence_refs": evidence_refs,
                **(metadata or {}),
            },
        )
        self.store.record_evaluation(evaluation)
        return evaluation

    def record_evaluation(self, evaluation: EvaluationRecord | Mapping[str, Any]) -> EvaluationRecord:
        return self.store.record_evaluation(evaluation)

    def get_evaluation(self, evaluation_id: str) -> EvaluationRecord | None:
        return self.store.get_evaluation(evaluation_id)


def build_qa_evaluator(root: str | None = None) -> QAEvaluator:
    return QAEvaluator(store=build_evaluation_store(root=Path(root) if root is not None else None))
