from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from ot_skill_enterprise.service_locator import project_root as resolve_project_root
from ot_skill_enterprise.skills_compiler import CandidateSummary, PromotionRecord, SkillCandidate, SkillPackageCompiler, build_skill_package_compiler
from ot_skill_enterprise.storage import build_projection_cache, build_storage_settings


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(subvalue) for key, subvalue in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _json_dump(value: Any) -> str:
    return json.dumps(_json_safe(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _read_json_file(path: Path) -> dict[str, Any] | None:
    if not path.exists() or not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _read_json_files(path: Path) -> list[dict[str, Any]]:
    if not path.exists() or not path.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for item in sorted(path.glob("*.json")):
        payload = _read_json_file(item)
        if payload is not None:
            items.append(payload)
    return items


def _write_json_file(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _candidate_summary_from_candidate(candidate: SkillCandidate, *, source: str) -> CandidateSummary:
    return CandidateSummary(
        promotion_id=None,
        candidate_id=candidate.candidate_id,
        candidate_slug=candidate.candidate_slug,
        runtime_session_id=candidate.runtime_session_id,
        candidate_type=candidate.candidate_type,
        target_skill_name=candidate.target_skill_name,
        target_skill_kind=candidate.target_skill_kind,
        change_summary=candidate.change_summary,
        source_run_id=candidate.source_run_id,
        source_evaluation_id=candidate.source_evaluation_id,
        status=candidate.status,
        package_root=candidate.package_path,
        bundle_sha256=candidate.bundle_sha256,
        validation_status=candidate.validation_status,
        registry_status=str(candidate.metadata.get("registry_status") or ""),
        source=source,
        metadata=dict(candidate.metadata),
        created_at=candidate.created_at,
    )


def _candidate_summary_from_payload(payload: Mapping[str, Any], *, source: str) -> CandidateSummary:
    body = dict(payload)
    return CandidateSummary(
        promotion_id=str(body.get("promotion_id") or body.get("submission_id") or "").strip() or None,
        candidate_id=str(body.get("candidate_id") or body.get("promotion_id") or body.get("submission_id") or ""),
        candidate_slug=str(body.get("candidate_slug") or body.get("target_skill_name") or body.get("candidate_id") or "candidate"),
        runtime_session_id=str(body.get("runtime_session_id") or body.get("metadata", {}).get("runtime_session_id") or ""),
        candidate_type=str(body.get("candidate_type") or "prompt"),
        target_skill_name=str(body.get("target_skill_name") or "generated-skill"),
        target_skill_kind=str(body.get("target_skill_kind") or "skill"),
        change_summary=str(body.get("change_summary") or body.get("summary") or "Generated skill candidate"),
        source_run_id=body.get("source_run_id") or body.get("run_id"),
        source_evaluation_id=body.get("source_evaluation_id") or body.get("evaluation_id"),
        status=str(body.get("status") or body.get("validation_status") or "draft"),
        package_root=str(body.get("package_path") or body.get("package_root") or "") or None,
        bundle_sha256=str(body.get("bundle_sha256") or "") or None,
        validation_status=str(body.get("validation_status") or body.get("status") or "pending"),
        registry_status=str(body.get("registry_status") or ""),
        source=source,
        metadata=dict(body.get("metadata") or {}),
        created_at=body.get("created_at"),
    )


def _merge_candidate_and_promotion(candidate_payload: Mapping[str, Any], promotion_payload: Mapping[str, Any] | None) -> CandidateSummary:
    base = dict(candidate_payload)
    if promotion_payload:
        metadata = dict(base.get("metadata") or {})
        metadata.update(dict(promotion_payload.get("metadata") or {}))
        base.update(
            {
                "package_path": promotion_payload.get("package_path") or base.get("package_path"),
                "bundle_sha256": promotion_payload.get("bundle_sha256") or base.get("bundle_sha256"),
                "validation_status": promotion_payload.get("validation_status") or base.get("validation_status"),
                "registry_status": promotion_payload.get("registry_status") or base.get("registry_status"),
                "status": promotion_payload.get("registry_status") or base.get("status"),
                "metadata": metadata,
            }
        )
    return _candidate_summary_from_payload(base, source=str(base.get("source") or "candidate"))


@dataclass(slots=True)
class CandidateSurfaceService:
    project_root: Path
    workspace_root: Path
    compiler: SkillPackageCompiler = field(init=False)
    _settings: Any = field(init=False, repr=False)
    _cache: Any = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.project_root = Path(self.project_root).expanduser().resolve()
        self.workspace_root = Path(self.workspace_root).expanduser().resolve()
        self.compiler = build_skill_package_compiler(self.project_root, self.workspace_root)
        self._settings = build_storage_settings(project_root=self.project_root, workspace_root=self.workspace_root)
        self._cache = build_projection_cache(settings=self._settings)

    @property
    def registry_root(self) -> Path:
        return self.workspace_root / "evolution-registry"

    @property
    def candidate_root(self) -> Path:
        return self.registry_root / "candidates"

    @property
    def promotion_root(self) -> Path:
        return self.registry_root / "promotions"

    def _candidate_path(self, candidate_id: str) -> Path:
        return self.candidate_root / f"{candidate_id}.json"

    def _promotion_path(self, promotion_id: str) -> Path:
        return self.promotion_root / f"{promotion_id}.json"

    def _candidate_records(self) -> list[dict[str, Any]]:
        if not self.candidate_root.exists():
            return []
        records: list[dict[str, Any]] = []
        for path in sorted(self.candidate_root.glob("*.json")):
            payload = _read_json_file(path)
            if payload is not None:
                records.append(payload)
        return records

    def _promotion_records(self) -> list[dict[str, Any]]:
        if not self.promotion_root.exists():
            return []
        records: list[dict[str, Any]] = []
        for path in sorted(self.promotion_root.glob("*.json")):
            payload = _read_json_file(path)
            if payload is not None:
                records.append(payload)
        return records

    def _write_candidate_record(self, candidate: SkillCandidate) -> None:
        _write_json_file(self._candidate_path(candidate.candidate_id), candidate.to_dict())

    def _write_promotion_record(self, promotion: PromotionRecord) -> None:
        _write_json_file(self._promotion_path(promotion.promotion_id), promotion.to_dict())

    def _invalidate_projections(self) -> None:
        self._cache.delete_keys(
            f"runtime:overview:{self.workspace_root}",
            f"runtime:evolution_summary:{self.workspace_root}",
            f"runtime:evaluations:{self.workspace_root}",
            f"runtime:candidates:{self.workspace_root}",
            f"runtime:promotions:{self.workspace_root}",
            f"runtime:sessions:{self.workspace_root}",
            f"runtime:active_runs:{self.workspace_root}",
            f"runtime:runtimes:{self.workspace_root}",
        )

    def _resolve_candidate(self, candidate: str | Mapping[str, Any] | SkillCandidate) -> SkillCandidate:
        if isinstance(candidate, SkillCandidate):
            return candidate
        if isinstance(candidate, Mapping):
            return SkillCandidate.from_mapping(candidate)
        candidate_payload = _read_json_file(self._candidate_path(candidate))
        if candidate_payload is not None:
            return SkillCandidate.from_mapping(candidate_payload)
        for record in self._promotion_records():
            if str(record.get("candidate_id") or "") == candidate:
                return SkillCandidate.from_mapping(record)
        raise KeyError(f"Unknown candidate: {candidate}")

    def _candidate_package_root(self, candidate: SkillCandidate, output_root: Path | None = None) -> Path:
        if output_root is not None:
            return Path(output_root).expanduser().resolve()
        return (self.workspace_root / "candidate-packages" / candidate.candidate_slug).resolve()

    def list_candidates(self) -> dict[str, Any]:
        candidate_records = {str(item.get("candidate_id") or ""): item for item in self._candidate_records() if item.get("candidate_id")}
        promotion_records = {str(item.get("candidate_id") or ""): item for item in self._promotion_records() if item.get("candidate_id")}
        merged: list[CandidateSummary] = []
        for candidate_id, record in candidate_records.items():
            merged.append(_merge_candidate_and_promotion(record, promotion_records.get(candidate_id)))
        for candidate_id, record in promotion_records.items():
            if candidate_id not in candidate_records:
                merged.append(_merge_candidate_and_promotion(record, record))
        merged.sort(key=lambda item: str(item.created_at or ""), reverse=True)
        return {"status": "ready", "count": len(merged), "items": [item.to_dict() for item in merged]}

    def list_promotions(self) -> dict[str, Any]:
        promotions = [_candidate_summary_from_payload(record, source="promotion") for record in self._promotion_records()]
        promotions.sort(key=lambda item: str(item.created_at or ""), reverse=True)
        return {"status": "ready", "count": len(promotions), "items": [item.to_dict() for item in promotions]}

    def candidate_detail(self, candidate_id: str) -> dict[str, Any] | None:
        candidate = _read_json_file(self._candidate_path(candidate_id))
        promotion = next((item for item in self._promotion_records() if str(item.get("candidate_id") or "") == candidate_id), None)
        if candidate is None and promotion is None:
            return None
        if candidate is None:
            return _merge_candidate_and_promotion(promotion or {}, promotion).to_dict()
        return _merge_candidate_and_promotion(candidate, promotion).to_dict()

    def compile_candidate(
        self,
        candidate: str | Mapping[str, Any] | SkillCandidate,
        *,
        output_root: Path | None = None,
        package_kind: str | None = None,
        force: bool = True,
    ) -> dict[str, Any]:
        resolved = self._resolve_candidate(candidate)
        if not resolved.runtime_session_id:
            raise ValueError("candidate is missing runtime_session_id")
        package_root = self._candidate_package_root(resolved, output_root=output_root)
        result = self.compiler.compile(resolved, output_root=package_root, package_kind=package_kind, force=force)
        updated = SkillCandidate.from_mapping(
            {
                **resolved.to_dict(),
                "package_path": str(result.package_root),
                "bundle_sha256": result.bundle_sha256,
                "validation_status": "pending",
                "status": "compiled",
                "manifest_preview": result.manifest,
                "metadata": {
                    **dict(resolved.metadata),
                    "package_root": str(result.package_root),
                    "bundle_sha256": result.bundle_sha256,
                    "generated_files": list(result.generated_files),
                    "package_kind": result.package_kind,
                },
            }
        )
        self._write_candidate_record(updated)
        self._invalidate_projections()
        return {
            "status": "compiled",
            "candidate": updated.to_dict(),
            "package": result.to_dict(),
        }

    def validate_candidate(
        self,
        candidate: str | Mapping[str, Any] | SkillCandidate,
        *,
        package_root: Path | str | None = None,
        action_id: str | None = None,
    ) -> dict[str, Any]:
        resolved = self._resolve_candidate(candidate)
        package_value = package_root or resolved.package_path
        if not package_value:
            raise ValueError("candidate package has not been compiled")
        root = Path(package_value).expanduser().resolve()
        if not root.exists():
            raise ValueError("candidate package has not been compiled")
        result = self.compiler.validate(root, candidate=resolved, action_id=action_id)
        updated = SkillCandidate.from_mapping(
            {
                **resolved.to_dict(),
                "package_path": str(root),
                "bundle_sha256": result.bundle_sha256,
                "validation_status": "validated" if result.ok else "validation_failed",
                "status": "validated" if result.ok else "validation_failed",
                "metadata": {
                    **dict(resolved.metadata),
                    "validation_report": result.to_dict(),
                },
            }
        )
        self._write_candidate_record(updated)
        self._invalidate_projections()
        return {
            "status": updated.validation_status,
            "candidate": updated.to_dict(),
            "validation_report": result.to_dict(),
        }

    def promote_candidate(
        self,
        candidate: str | Mapping[str, Any] | SkillCandidate,
        *,
        package_root: Path | str | None = None,
        package_kind: str | None = None,
        force: bool = True,
        action_id: str | None = None,
    ) -> dict[str, Any]:
        resolved = self._resolve_candidate(candidate)
        if resolved.validation_status not in {"validated", "passed"}:
            raise ValueError("candidate must be validated before promotion")
        package_value = package_root or resolved.package_path
        if not package_value:
            raise ValueError("candidate package has not been compiled")
        source_root = Path(package_value).expanduser().resolve()
        if not source_root.exists():
            raise ValueError("candidate package has not been compiled")
        promotion = self.compiler.promote(
            resolved,
            output_root=source_root,
            package_kind=package_kind,
            force=force,
            action_id=action_id,
        )
        self._write_promotion_record(promotion)
        promoted_candidate = SkillCandidate.from_mapping(
            {
                **resolved.to_dict(),
                "package_path": str(promotion.package_root),
                "bundle_sha256": promotion.bundle_sha256,
                "validation_status": "validated" if promotion.validation_status == "passed" else promotion.validation_status,
                "status": "promoted",
                "metadata": {
                    **dict(resolved.metadata),
                    "promotion_id": promotion.promotion_id,
                    "registry_status": promotion.registry_status,
                    "package_root": str(promotion.package_root),
                    "validation_report": dict(promotion.metadata.get("validation") or resolved.metadata.get("validation_report") or {}),
                },
            }
        )
        self._write_candidate_record(promoted_candidate)
        self._invalidate_projections()
        return {
            "status": "promoted",
            "candidate": promoted_candidate.to_dict(),
            "promotion": promotion.to_dict(),
        }

    def overview(self) -> dict[str, Any]:
        candidates = self.list_candidates()
        promotions = self.list_promotions()
        return {
            "status": "ready",
            "candidate_count": candidates["count"],
            "promotion_count": promotions["count"],
            "latest_candidate": candidates["items"][0] if candidates["items"] else None,
            "latest_promotion": promotions["items"][0] if promotions["items"] else None,
            "candidate_queue": candidates["items"][:10],
            "promotion_summary": promotions["items"][:10],
        }


def build_candidate_surface_service(
    project_root: Path | None = None,
    workspace_root: Path | None = None,
) -> CandidateSurfaceService:
    resolved_project_root = Path(project_root).expanduser().resolve() if project_root is not None else resolve_project_root()
    resolved_workspace_root = Path(workspace_root).expanduser().resolve() if workspace_root is not None else (resolved_project_root / ".ot-workspace").resolve()
    return CandidateSurfaceService(project_root=resolved_project_root, workspace_root=resolved_workspace_root)
