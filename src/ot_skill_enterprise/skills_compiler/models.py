from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ot_skill_enterprise.shared.contracts.common import utc_now


def _dump_model(value: Any) -> Any:
    dumper = getattr(value, "model_dump", None)
    if dumper is not None:
        return dumper(mode="json")
    if isinstance(value, Mapping):
        return dict(value)
    return value


def _stable_payload(value: Any) -> str:
    import json

    return json.dumps(_dump_model(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _slugify(value: str) -> str:
    import re

    slug = re.sub(r"[^a-zA-Z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    return slug or "candidate"


def _short_hash(value: Any, *, length: int = 10) -> str:
    import hashlib

    digest = hashlib.sha256(_stable_payload(value).encode("utf-8")).hexdigest()
    return digest[:length]


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        raw = value.strip()
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed
        except ValueError:
            return datetime.now(timezone.utc)
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class SkillCandidate:
    candidate_id: str
    candidate_slug: str
    runtime_session_id: str
    source_run_id: str | None
    source_evaluation_id: str | None
    candidate_type: str
    target_skill_name: str
    target_skill_kind: str
    change_summary: str
    generation_spec: dict[str, Any] = field(default_factory=dict)
    manifest_preview: dict[str, Any] = field(default_factory=dict)
    package_path: str | None = None
    bundle_sha256: str | None = None
    status: str = "draft"
    validation_status: str = "pending"
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utc_now)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None = None) -> "SkillCandidate":
        body = dict(payload or {})
        metadata = dict(body.get("metadata") or {}) if isinstance(body.get("metadata"), Mapping) else {}
        generation_spec = dict(body.get("generation_spec") or {}) if isinstance(body.get("generation_spec"), Mapping) else {}
        manifest_preview = dict(body.get("manifest_preview") or {}) if isinstance(body.get("manifest_preview"), Mapping) else {}
        target_skill_name = str(body.get("target_skill_name") or metadata.get("target_skill_name") or body.get("skill_name") or "generated-skill").strip()
        candidate_type = str(body.get("candidate_type") or metadata.get("candidate_type") or "prompt").strip().lower()
        target_skill_kind = str(body.get("target_skill_kind") or metadata.get("target_skill_kind") or "skill").strip()
        change_summary = str(body.get("change_summary") or metadata.get("change_summary") or body.get("summary") or "Generated skill candidate").strip()
        source_run_id = body.get("source_run_id") or body.get("run_id") or metadata.get("source_run_id") or metadata.get("run_id")
        source_evaluation_id = body.get("source_evaluation_id") or metadata.get("source_evaluation_id") or body.get("evaluation_id")
        candidate_id = str(body.get("candidate_id") or metadata.get("candidate_id") or "").strip()
        if not candidate_id:
            hash_input = {
                "target_skill_name": target_skill_name,
                "candidate_type": candidate_type,
                "change_summary": change_summary,
                "source_run_id": source_run_id,
                "source_evaluation_id": source_evaluation_id,
            }
            candidate_id = f"candidate-{_short_hash(hash_input)}"
        candidate_slug = str(
            body.get("candidate_slug")
            or metadata.get("candidate_slug")
            or f"{_slugify(target_skill_name)}-{_short_hash(candidate_id, length=8)}"
        ).strip()
        runtime_session_id = str(body.get("runtime_session_id") or metadata.get("runtime_session_id") or "").strip()
        status = str(body.get("status") or metadata.get("status") or "draft").strip().lower()
        validation_status = str(body.get("validation_status") or metadata.get("validation_status") or "pending").strip().lower()
        return cls(
            candidate_id=candidate_id,
            candidate_slug=candidate_slug,
            runtime_session_id=runtime_session_id,
            source_run_id=str(source_run_id).strip() if source_run_id is not None else None,
            source_evaluation_id=str(source_evaluation_id).strip() if source_evaluation_id is not None else None,
            candidate_type=candidate_type or "prompt",
            target_skill_name=target_skill_name,
            target_skill_kind=target_skill_kind or "skill",
            change_summary=change_summary,
            generation_spec=generation_spec,
            manifest_preview=manifest_preview,
            package_path=str(body.get("package_path") or metadata.get("package_path") or "").strip() or None,
            bundle_sha256=str(body.get("bundle_sha256") or metadata.get("bundle_sha256") or "").strip() or None,
            status=status or "draft",
            validation_status=validation_status or "pending",
            metadata={k: v for k, v in metadata.items() if v is not None},
            created_at=_parse_datetime(body.get("created_at") or metadata.get("created_at")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "candidate_slug": self.candidate_slug,
            "runtime_session_id": self.runtime_session_id,
            "source_run_id": self.source_run_id,
            "source_evaluation_id": self.source_evaluation_id,
            "candidate_type": self.candidate_type,
            "target_skill_name": self.target_skill_name,
            "target_skill_kind": self.target_skill_kind,
            "change_summary": self.change_summary,
            "generation_spec": dict(self.generation_spec),
            "manifest_preview": dict(self.manifest_preview),
            "package_path": self.package_path,
            "bundle_sha256": self.bundle_sha256,
            "status": self.status,
            "validation_status": self.validation_status,
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
        }


@dataclass(slots=True)
class CandidateSummary:
    promotion_id: str | None
    candidate_id: str
    candidate_slug: str
    runtime_session_id: str | None
    candidate_type: str
    target_skill_name: str
    target_skill_kind: str
    change_summary: str
    source_run_id: str | None
    source_evaluation_id: str | None
    status: str
    package_root: str | None = None
    bundle_sha256: str | None = None
    validation_status: str | None = None
    registry_status: str | None = None
    source: str = "submission"
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "promotion_id": self.promotion_id,
            "candidate_id": self.candidate_id,
            "candidate_slug": self.candidate_slug,
            "runtime_session_id": self.runtime_session_id,
            "candidate_type": self.candidate_type,
            "target_skill_name": self.target_skill_name,
            "target_skill_kind": self.target_skill_kind,
            "change_summary": self.change_summary,
            "source_run_id": self.source_run_id,
            "source_evaluation_id": self.source_evaluation_id,
            "status": self.status,
            "package_root": self.package_root,
            "bundle_sha256": self.bundle_sha256,
            "validation_status": self.validation_status,
            "registry_status": self.registry_status,
            "source": self.source,
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
        }


@dataclass(slots=True)
class PackageBuildResult:
    candidate: SkillCandidate
    package_root: Path
    package_kind: str
    generated_files: tuple[str, ...]
    bundle_sha256: str
    manifest: dict[str, Any]
    actions: dict[str, Any]
    interface: dict[str, Any]
    skill_md: str
    created_at: datetime = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate": self.candidate.to_dict(),
            "package_root": str(self.package_root),
            "package_kind": self.package_kind,
            "generated_files": list(self.generated_files),
            "bundle_sha256": self.bundle_sha256,
            "manifest": dict(self.manifest),
            "actions": dict(self.actions),
            "interface": dict(self.interface),
            "skill_md": self.skill_md,
            "created_at": self.created_at,
        }


@dataclass(slots=True)
class PackageValidationResult:
    candidate: SkillCandidate
    package_root: Path
    package_kind: str
    bundle_sha256: str
    ok: bool
    phases: tuple[dict[str, Any], ...]
    issues: tuple[dict[str, Any], ...] = ()
    warnings: tuple[dict[str, Any], ...] = ()
    created_at: datetime = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate": self.candidate.to_dict(),
            "package_root": str(self.package_root),
            "package_kind": self.package_kind,
            "bundle_sha256": self.bundle_sha256,
            "ok": self.ok,
            "phases": [dict(item) for item in self.phases],
            "issues": [dict(item) for item in self.issues],
            "warnings": [dict(item) for item in self.warnings],
            "created_at": self.created_at,
        }


@dataclass(slots=True)
class PromotionRecord:
    promotion_id: str
    candidate: SkillCandidate
    package_root: Path
    package_kind: str
    bundle_sha256: str
    validation_status: str
    registry_status: str
    package_name: str
    runtime_session_id: str
    created_at: datetime = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "promotion_id": self.promotion_id,
            "candidate_id": self.candidate.candidate_id,
            "candidate_slug": self.candidate.candidate_slug,
            "candidate_type": self.candidate.candidate_type,
            "source_run_id": self.candidate.source_run_id,
            "source_evaluation_id": self.candidate.source_evaluation_id,
            "target_skill_name": self.candidate.target_skill_name,
            "target_skill_kind": self.candidate.target_skill_kind,
            "runtime_session_id": self.runtime_session_id,
            "package_root": str(self.package_root),
            "package_path": str(self.package_root),
            "package_kind": self.package_kind,
            "bundle_sha256": self.bundle_sha256,
            "validation_status": self.validation_status,
            "registry_status": self.registry_status,
            "package_name": self.package_name,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }


__all__ = [
    "CandidateSummary",
    "PackageBuildResult",
    "PackageValidationResult",
    "PromotionRecord",
    "SkillCandidate",
]
