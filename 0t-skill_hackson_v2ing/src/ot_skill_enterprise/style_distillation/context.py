from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


_STAGE_FILE_MAP = {
    "distill_features": "stage_distill_features.json",
    "reflection_report": "stage_reflection.json",
    "skill_build": "stage_build.json",
    "execution_outcome": "stage_execution.json",
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=_json_default))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")


def _read_json(path: Path, *, default: Any = None) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _hash_payload(value: Any) -> str:
    encoded = json.dumps(_json_safe(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _stage_status_template() -> dict[str, Any]:
    return {
        "status": "pending",
        "started_at": None,
        "finished_at": None,
        "summary": "",
        "input_artifact_ids": [],
        "output_artifact_ids": [],
        "retry_count": 0,
    }


@dataclass(slots=True)
class EphemeralContextEnvelope:
    context: str
    sources: list[dict[str, Any]] = field(default_factory=list)
    review_hints: list[dict[str, Any]] = field(default_factory=list)
    memory_items: list[dict[str, Any]] = field(default_factory=list)
    hard_constraints: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "context": self.context,
            "sources": _json_safe(self.sources),
            "review_hints": _json_safe(self.review_hints),
            "memory_items": _json_safe(self.memory_items),
            "hard_constraints": list(self.hard_constraints),
        }


@dataclass(slots=True)
class ReviewAgentDecision:
    stage: str
    summary: str
    next_stage_hints: list[str] = field(default_factory=list)
    retry_hints: list[str] = field(default_factory=list)
    context_reduction_hints: list[str] = field(default_factory=list)
    context_sources: list[dict[str, Any]] = field(default_factory=list)
    hard_constraints: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "summary": self.summary,
            "next_stage_hints": list(self.next_stage_hints),
            "retry_hints": list(self.retry_hints),
            "context_reduction_hints": list(self.context_reduction_hints),
            "context_sources": _json_safe(self.context_sources),
            "hard_constraints": list(self.hard_constraints),
            "created_at": self.created_at.isoformat(),
        }


class JobLedgerStore:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = Path(workspace_root).expanduser().resolve()

    def context_root(self, job_dir: Path) -> Path:
        return job_dir / "context"

    def ledger_path(self, job_dir: Path) -> Path:
        return self.context_root(job_dir) / "job.json"

    def load(self, job_dir: Path) -> dict[str, Any]:
        payload = _read_json(self.ledger_path(job_dir), default={})
        if isinstance(payload, dict):
            return payload
        return {}

    def create(
        self,
        job_dir: Path,
        *,
        job_id: str,
        wallet: str,
        chain: str,
        requested_skill_name: str,
        extractor_prompt: str,
        stage_order: Iterable[str],
    ) -> dict[str, Any]:
        now = _utc_now().isoformat()
        order = [str(item) for item in stage_order]
        payload = {
            "job_id": job_id,
            "wallet": wallet,
            "chain": chain,
            "requested_skill_name": requested_skill_name,
            "extractor_prompt": extractor_prompt,
            "created_at": now,
            "updated_at": now,
            "status": "pending",
            "current_stage": None,
            "stage_order": order,
            "lineage": {
                "distill_run_id": None,
                "reflection_run_id": None,
                "build_candidate_id": None,
                "promotion_id": None,
                "execution_run_id": None,
            },
            "cache_keys": {
                "wallet_fetch_key": None,
                "market_fetch_key": None,
                "compact_input_hash": None,
                "strategy_hash": None,
                "execution_plan_hash": None,
                "distill_stage_hash": None,
                "reflection_stage_hash": None,
                "skill_build_stage_hash": None,
                "execution_stage_hash": None,
            },
            "artifact_ids": {},
            "stage_statuses": {stage: _stage_status_template() for stage in order},
            "summary": {},
            "context_sources": [],
        }
        _write_json(self.ledger_path(job_dir), payload)
        return payload

    def save(self, job_dir: Path, payload: dict[str, Any]) -> dict[str, Any]:
        payload = _json_safe(payload)
        payload["updated_at"] = _utc_now().isoformat()
        _write_json(self.ledger_path(job_dir), payload)
        return payload

    def on_stage_start(
        self,
        job_dir: Path,
        *,
        stage: str,
        summary: str,
        input_artifact_ids: Iterable[str] = (),
    ) -> dict[str, Any]:
        ledger = self.load(job_dir)
        statuses = dict(ledger.get("stage_statuses") or {})
        status = dict(statuses.get(stage) or _stage_status_template())
        status["status"] = "running"
        status["started_at"] = status.get("started_at") or _utc_now().isoformat()
        status["finished_at"] = None
        status["summary"] = summary
        status["input_artifact_ids"] = list(input_artifact_ids)
        status["retry_count"] = int(status.get("retry_count") or 0)
        statuses[stage] = status
        ledger["stage_statuses"] = statuses
        ledger["current_stage"] = stage
        ledger["status"] = "running"
        return self.save(job_dir, ledger)

    def on_stage_success(
        self,
        job_dir: Path,
        *,
        stage: str,
        summary: str,
        output_artifact_ids: Iterable[str] = (),
    ) -> dict[str, Any]:
        ledger = self.load(job_dir)
        statuses = dict(ledger.get("stage_statuses") or {})
        status = dict(statuses.get(stage) or _stage_status_template())
        status["status"] = "succeeded"
        status["finished_at"] = _utc_now().isoformat()
        status["summary"] = summary
        status["output_artifact_ids"] = list(output_artifact_ids)
        statuses[stage] = status
        ledger["stage_statuses"] = statuses
        ledger["current_stage"] = stage
        return self.save(job_dir, ledger)

    def on_stage_fail(
        self,
        job_dir: Path,
        *,
        stage: str,
        summary: str,
        retry_hints: Iterable[dict[str, Any]] = (),
    ) -> dict[str, Any]:
        ledger = self.load(job_dir)
        statuses = dict(ledger.get("stage_statuses") or {})
        status = dict(statuses.get(stage) or _stage_status_template())
        status["status"] = "failed"
        status["finished_at"] = _utc_now().isoformat()
        status["summary"] = summary
        status["retry_count"] = int(status.get("retry_count") or 0) + 1
        if retry_hints:
            status["retry_hints"] = _json_safe(list(retry_hints))
        statuses[stage] = status
        ledger["stage_statuses"] = statuses
        ledger["current_stage"] = stage
        ledger["status"] = "failed"
        return self.save(job_dir, ledger)

    def finalize(
        self,
        job_dir: Path,
        *,
        status: str,
        summary: dict[str, Any],
        context_sources: Iterable[dict[str, Any]] = (),
    ) -> dict[str, Any]:
        ledger = self.load(job_dir)
        ledger["status"] = status
        ledger["summary"] = _json_safe(summary)
        if context_sources:
            ledger["context_sources"] = _json_safe(list(context_sources))
        return self.save(job_dir, ledger)

    def update_lineage(self, job_dir: Path, **values: Any) -> dict[str, Any]:
        ledger = self.load(job_dir)
        lineage = dict(ledger.get("lineage") or {})
        lineage.update({key: value for key, value in values.items() if value is not None})
        ledger["lineage"] = lineage
        return self.save(job_dir, ledger)

    def update_cache_keys(self, job_dir: Path, **values: Any) -> dict[str, Any]:
        ledger = self.load(job_dir)
        cache_keys = dict(ledger.get("cache_keys") or {})
        cache_keys.update({key: value for key, value in values.items() if value is not None})
        ledger["cache_keys"] = cache_keys
        return self.save(job_dir, ledger)

    def set_artifact_id(self, job_dir: Path, *, stage: str, artifact_id: str) -> dict[str, Any]:
        ledger = self.load(job_dir)
        artifact_ids = dict(ledger.get("artifact_ids") or {})
        artifact_ids[stage] = artifact_id
        ledger["artifact_ids"] = artifact_ids
        return self.save(job_dir, ledger)


class StageArtifactStore:
    def artifact_path(self, job_dir: Path, stage: str) -> Path:
        filename = _STAGE_FILE_MAP.get(stage, f"stage_{stage}.json")
        return job_dir / "context" / filename

    def exists(self, job_dir: Path, stage: str) -> bool:
        return self.artifact_path(job_dir, stage).is_file()

    def read(self, job_dir: Path, stage: str) -> dict[str, Any]:
        payload = _read_json(self.artifact_path(job_dir, stage), default={})
        if isinstance(payload, dict):
            return payload
        return {}

    def write(self, job_dir: Path, stage: str, payload: dict[str, Any]) -> Path:
        path = self.artifact_path(job_dir, stage)
        if path.exists():
            raise FileExistsError(f"immutable stage artifact already exists: {path}")
        _write_json(path, payload)
        return path

    def replace(self, job_dir: Path, stage: str, payload: dict[str, Any]) -> Path:
        path = self.artifact_path(job_dir, stage)
        if path.exists():
            history_dir = path.parent / "history" / stage
            history_dir.mkdir(parents=True, exist_ok=True)
            history_path = history_dir / f"{_utc_now().strftime('%Y%m%dT%H%M%S%fZ')}.json"
            history_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        _write_json(path, payload)
        return path


class StageCacheStore:
    def __init__(self, workspace_root: Path) -> None:
        self.root = Path(workspace_root).expanduser().resolve() / "style-distillation-cache"
        self.root.mkdir(parents=True, exist_ok=True)

    def _stage_root(self, stage: str) -> Path:
        root = self.root / stage
        root.mkdir(parents=True, exist_ok=True)
        return root

    def entry_path(self, stage: str, cache_key: str) -> Path:
        safe_stage = str(stage).strip() or "unknown"
        safe_key = str(cache_key).strip()
        return self._stage_root(safe_stage) / f"{safe_key}.json"

    def remember(
        self,
        *,
        stage: str,
        cache_key: str,
        job_id: str,
        artifact_path: Path,
        payload: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> Path:
        entry = {
            "stage": stage,
            "cache_key": cache_key,
            "job_id": job_id,
            "artifact_path": str(artifact_path),
            "payload": _json_safe(payload),
            "metadata": _json_safe(metadata or {}),
            "created_at": _utc_now().isoformat(),
        }
        path = self.entry_path(stage, cache_key)
        _write_json(path, entry)
        return path

    def recall(self, stage: str, cache_key: str) -> dict[str, Any]:
        payload = _read_json(self.entry_path(stage, cache_key), default={})
        if isinstance(payload, dict):
            return payload
        return {}


class StageCacheRegistry:
    def __init__(self, workspace_root: Path) -> None:
        self.root = Path(workspace_root).expanduser().resolve() / "style-distillation-cache"
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / "index.json"
        self.artifact_store = StageArtifactStore()

    def _stage_root(self, stage: str) -> Path:
        path = self.root / "stages" / stage
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _entry_path(self, stage: str, cache_key: str) -> Path:
        return self._stage_root(stage) / f"{cache_key}.json"

    def _load_index(self) -> dict[str, Any]:
        payload = _read_json(self.index_path, default={})
        if isinstance(payload, dict):
            return payload
        return {}

    def _save_index(self, payload: dict[str, Any]) -> dict[str, Any]:
        _write_json(self.index_path, payload)
        return payload

    def lookup(self, stage: str, cache_key: str) -> dict[str, Any] | None:
        if not stage or not cache_key:
            return None
        entry = _read_json(self._entry_path(stage, cache_key), default=None)
        if isinstance(entry, dict):
            payload = entry.get("payload")
            if isinstance(payload, dict):
                entry["payload"] = payload
            return entry
        index = self._load_index()
        stage_index = dict(index.get(stage) or {})
        summary = dict(stage_index.get(cache_key) or {})
        payload_path = summary.get("payload_path")
        if payload_path:
            payload = _read_json(Path(str(payload_path)), default=None)
            if isinstance(payload, dict):
                summary["payload"] = payload
                return summary
        return None

    def register(
        self,
        *,
        stage: str,
        cache_key: str,
        job_id: str,
        payload: dict[str, Any],
        summary: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload_path = self._entry_path(stage, cache_key)
        entry = {
            "stage": stage,
            "cache_key": cache_key,
            "job_id": job_id,
            "summary": summary,
            "metadata": _json_safe(metadata or {}),
            "created_at": _utc_now().isoformat(),
            "payload_path": str(payload_path),
            "payload": _json_safe(payload),
        }
        _write_json(payload_path, entry)
        index = self._load_index()
        stage_index = dict(index.get(stage) or {})
        stage_index[cache_key] = {key: value for key, value in entry.items() if key != "payload"}
        index[stage] = stage_index
        self._save_index(index)
        return entry

    def materialize(
        self,
        job_dir: Path,
        stage: str,
        cache_key: str,
        *,
        payload_override: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], Path] | None:
        entry = self.lookup(stage, cache_key)
        if not entry:
            return None
        payload = _json_safe(payload_override or entry.get("payload") or {})
        if not isinstance(payload, dict):
            return None
        payload["job_id"] = str(job_dir.name)
        payload["cache_hit"] = True
        payload["cache_key"] = cache_key
        payload["cache_source_job_id"] = entry.get("job_id")
        path = self.artifact_store.write(job_dir, stage, payload)
        return payload, path


class ReviewAgent:
    def __init__(self, context_assembler: ContextAssembler | None = None) -> None:
        self.context_assembler = context_assembler or ContextAssembler()

    def review(
        self,
        *,
        stage: str,
        summary: str,
        hints: Iterable[str] = (),
        retry_hints: Iterable[str] = (),
        context_reduction_hints: Iterable[str] = (),
        context_sources: Iterable[dict[str, Any]] = (),
        hard_constraints: Iterable[str] = (),
    ) -> ReviewAgentDecision:
        return ReviewAgentDecision(
            stage=stage,
            summary=summary,
            next_stage_hints=[str(item) for item in hints if str(item).strip()],
            retry_hints=[str(item) for item in retry_hints if str(item).strip()],
            context_reduction_hints=[str(item) for item in context_reduction_hints if str(item).strip()],
            context_sources=[_json_safe(item) for item in context_sources if isinstance(item, dict)],
            hard_constraints=[str(item) for item in hard_constraints if str(item).strip()],
        )

    def on_stage_start(self, *, stage: str, summary: str, context_sources: Iterable[dict[str, Any]] = (), hard_constraints: Iterable[str] = ()) -> ReviewAgentDecision:
        return self.review(
            stage=stage,
            summary=summary,
            hints=(),
            retry_hints=(),
            context_reduction_hints=(),
            context_sources=context_sources,
            hard_constraints=hard_constraints,
        )

    def post_stage_call(
        self,
        *,
        stage: str,
        summary: str,
        hints: Iterable[str] = (),
        retry_hints: Iterable[str] = (),
        context_reduction_hints: Iterable[str] = (),
        context_sources: Iterable[dict[str, Any]] = (),
        hard_constraints: Iterable[str] = (),
    ) -> ReviewAgentDecision:
        return self.review(
            stage=stage,
            summary=summary,
            hints=hints,
            retry_hints=retry_hints,
            context_reduction_hints=context_reduction_hints,
            context_sources=context_sources,
            hard_constraints=hard_constraints,
        )

    def on_stage_fail(
        self,
        *,
        stage: str,
        summary: str,
        retry_hints: Iterable[str] = (),
        context_sources: Iterable[dict[str, Any]] = (),
    ) -> ReviewAgentDecision:
        return self.review(
            stage=stage,
            summary=summary,
            hints=(),
            retry_hints=retry_hints,
            context_reduction_hints=("Reduce context width before retrying.",),
            context_sources=context_sources,
        )

    def on_job_end(
        self,
        *,
        stage: str,
        summary: str,
        context_sources: Iterable[dict[str, Any]] = (),
    ) -> ReviewAgentDecision:
        return self.review(
            stage=stage,
            summary=summary,
            hints=("Persist distilled patterns for later replay.",),
            context_sources=context_sources,
        )

    def after_distill(self, stage_payload: dict[str, Any]) -> ReviewAgentDecision:
        focus_token_context = [
            item
            for item in list(stage_payload.get("market_context", {}).get("focus_token_context") or [])
            if isinstance(item, dict) and (item.get("pair_address") or item.get("base_symbol") or item.get("symbol"))
        ]
        hints: list[str] = []
        retry_hints: list[str] = []
        if not focus_token_context:
            hints.append("Reflection should treat market_context as partial and rely more on completed trades.")
            retry_hints.append("Retry AVE market fetch only if real pair resolution becomes available.")
        if not stage_payload.get("entry_factors"):
            hints.append("Prefer conservative setup labels because entry factors are sparse.")
        if not stage_payload.get("risk_filters"):
            hints.append("Do not overstate risk controls when token risk filters are missing.")
        return self.post_stage_call(
            stage="distill_features",
            summary="Distill features extracted from AVE.",
            hints=hints,
            retry_hints=retry_hints,
        )

    def after_reflection(self, stage_payload: dict[str, Any]) -> ReviewAgentDecision:
        hints = ["Build stage should preserve strategy metadata and execution_intent as canonical stage outputs."]
        retry_hints: list[str] = []
        if bool(stage_payload.get("fallback_used")):
            retry_hints.append("Retry Pi reflection if compact_input changes; current result used extractor fallback.")
        return self.post_stage_call(
            stage="reflection_report",
            summary=str(stage_payload.get("summary") or "Reflection completed."),
            hints=hints,
            retry_hints=retry_hints,
        )

    def after_build(self, stage_payload: dict[str, Any]) -> ReviewAgentDecision:
        retry_hints: list[str] = []
        if str(stage_payload.get("example_readiness") or "") == "blocked_by_missing_features":
            retry_hints.append("Retry distill/reflection if stronger market_context or risk filters become available.")
        return self.post_stage_call(
            stage="skill_build",
            summary=str(stage_payload.get("summary") or "Skill build completed."),
            hints=("Execution stage should only consume promoted skill + trade_plan + execution_intent.",),
            retry_hints=retry_hints,
        )

    def on_failure(
        self,
        *,
        stage: str,
        error: str,
        retry_hints: Iterable[str] = (),
        hints: Iterable[str] = (),
        context_sources: Iterable[dict[str, Any]] = (),
    ) -> ReviewAgentDecision:
        return self.on_stage_fail(
            stage=stage,
            summary=f"{stage} failed: {error}",
            retry_hints=retry_hints,
            context_sources=context_sources,
        )


class DerivedMemoryStore:
    def __init__(self, workspace_root: Path) -> None:
        self.root = Path(workspace_root).expanduser().resolve() / "style-distillation-memory" / "derived"
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, wallet: str, chain: str) -> Path:
        digest = hashlib.sha256(f"{wallet.lower()}::{chain.lower()}".encode("utf-8")).hexdigest()[:16]
        return self.root / f"{digest}.json"

    def recall(self, wallet: str, chain: str, *, limit: int = 3) -> list[dict[str, Any]]:
        path = self._path(wallet, chain)
        items = _read_json(path, default=[])
        if not isinstance(items, list):
            return []
        now = _utc_now()
        valid: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            expires_at = item.get("expires_at")
            if expires_at:
                try:
                    if datetime.fromisoformat(str(expires_at)) < now:
                        continue
                except ValueError:
                    continue
            valid.append(item)
        valid.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return valid[:limit]

    def remember(
        self,
        *,
        wallet: str,
        chain: str,
        memory_type: str,
        summary: str,
        payload: dict[str, Any],
        ttl_days: int = 7,
    ) -> Path:
        path = self._path(wallet, chain)
        items = _read_json(path, default=[])
        if not isinstance(items, list):
            items = []
        now = _utc_now()
        entry = {
            "memory_id": hashlib.sha256(f"{memory_type}:{summary}:{now.isoformat()}".encode("utf-8")).hexdigest()[:16],
            "memory_type": memory_type,
            "summary": summary,
            "payload": _json_safe(payload),
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(days=ttl_days)).isoformat(),
        }
        items = [item for item in items if isinstance(item, dict)]
        items.append(entry)
        _write_json(path, items[-20:])
        return path


class ReviewHintStore:
    def hint_path(self, job_dir: Path, stage: str) -> Path:
        return job_dir / "context" / "review_hints" / f"{stage}.json"

    def write(self, job_dir: Path, stage: str, payload: dict[str, Any]) -> Path:
        path = self.hint_path(job_dir, stage)
        _write_json(path, payload)
        return path

    def read(self, job_dir: Path, stage: str) -> dict[str, Any]:
        payload = _read_json(self.hint_path(job_dir, stage), default={})
        if isinstance(payload, dict):
            return payload
        return {}

    def read_all(self, job_dir: Path) -> list[dict[str, Any]]:
        root = job_dir / "context" / "review_hints"
        items: list[dict[str, Any]] = []
        if not root.is_dir():
            return items
        for path in sorted(root.glob("*.json")):
            payload = _read_json(path, default={})
            if isinstance(payload, dict):
                items.append(payload)
        return items


class ContextAssembler:
    def fence(self, label: str, content: str) -> str:
        text = str(content or "").strip()
        if not text:
            return ""
        return f"<context-envelope kind=\"{label}\">\n{text}\n</context-envelope>"

    def review_hint_payload(self, *, stage: str, summary: str, hints: Iterable[str], retry_hints: Iterable[str] = ()) -> dict[str, Any]:
        return {
            "stage": stage,
            "summary": summary,
            "next_stage_hints": [str(item) for item in hints if str(item).strip()],
            "retry_hints": [str(item) for item in retry_hints if str(item).strip()],
            "context_reduction_hints": [],
            "created_at": _utc_now().isoformat(),
        }

    def build_reflection_envelope(
        self,
        *,
        wallet: str,
        chain: str,
        derived_memories: Iterable[dict[str, Any]] = (),
        review_hints: Iterable[dict[str, Any]] = (),
        retry_reason: str | None = None,
        hard_constraints: Iterable[str] = (),
        max_bytes: int = 2048,
    ) -> EphemeralContextEnvelope:
        memory_items = [dict(item) for item in derived_memories if isinstance(item, dict)]
        hint_items = [dict(item) for item in review_hints if isinstance(item, dict)]
        constraints = [str(item).strip() for item in hard_constraints if str(item).strip()]
        sections: list[str] = []
        sources: list[dict[str, Any]] = []

        if memory_items:
            lines = [f"- {str(item.get('summary') or '').strip()}" for item in memory_items if str(item.get("summary") or "").strip()]
            block = self.fence(
                "derived-memory",
                "Use these as historical background only when relevant.\n\n" + "\n".join(lines),
            )
            if block:
                sections.append(block)
                sources.extend(
                    {
                        "kind": "derived_memory",
                        "memory_id": item.get("memory_id"),
                        "memory_type": item.get("memory_type"),
                    }
                    for item in memory_items
                )

        if hint_items:
            hint_lines: list[str] = []
            for item in hint_items:
                stage = str(item.get("stage") or "review")
                for hint in item.get("next_stage_hints") or []:
                    text = str(hint).strip()
                    if text:
                        hint_lines.append(f"- [{stage}] {text}")
                for hint in item.get("retry_hints") or []:
                    text = str(hint).strip()
                    if text:
                        hint_lines.append(f"- [{stage}/retry] {text}")
            block = self.fence("review-hints", "\n".join(hint_lines))
            if block:
                sections.append(block)
                sources.extend(
                    {
                        "kind": "review_hint",
                        "stage": item.get("stage"),
                        "created_at": item.get("created_at"),
                    }
                    for item in hint_items
                )

        if retry_reason:
            block = self.fence("retry-reason", f"Current retry reason: {retry_reason.strip()}")
            if block:
                sections.append(block)
                sources.append({"kind": "retry_reason"})

        if constraints:
            block = self.fence("hard-constraints", "\n".join(f"- {item}" for item in constraints))
            if block:
                sections.append(block)
                sources.extend({"kind": "hard_constraint", "value": item} for item in constraints)

        envelope = "\n\n".join(section for section in sections if section.strip())
        while len(envelope.encode("utf-8")) > max_bytes and hint_items:
            hint_items = hint_items[:-1]
            return self.build_reflection_envelope(
                wallet=wallet,
                chain=chain,
                derived_memories=memory_items,
                review_hints=hint_items,
                retry_reason=retry_reason,
                hard_constraints=constraints,
                max_bytes=max_bytes,
            )
        return EphemeralContextEnvelope(
            context=envelope,
            sources=sources,
            review_hints=hint_items,
            memory_items=memory_items,
            hard_constraints=constraints,
        )

    def context_source(self, *, kind: str, identifier: str, path: Path | str | None = None, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = {
            "kind": kind,
            "identifier": identifier,
        }
        if path is not None:
            payload["path"] = str(path)
        if metadata:
            payload["metadata"] = _json_safe(metadata)
        return payload


def hash_payload(value: Any) -> str:
    return _hash_payload(value)
