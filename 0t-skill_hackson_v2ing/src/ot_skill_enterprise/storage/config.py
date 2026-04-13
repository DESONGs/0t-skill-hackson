from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ot_skill_enterprise.service_locator import project_root as resolve_project_root


@dataclass(frozen=True, slots=True)
class StorageSettings:
    project_root: Path
    workspace_root: Path
    db_dsn: str | None
    redis_url: str | None
    blob_backend: str
    blob_root: Path
    blob_bucket: str | None
    blob_endpoint: str | None
    blob_region: str | None
    blob_prefix: str
    cache_ttl_overview: int
    cache_ttl_active_runs: int
    cache_ttl_session_summary: int
    cache_ttl_evolution_summary: int
    inline_payload_limit_bytes: int

    @property
    def postgres_enabled(self) -> bool:
        return bool(self.db_dsn)

    @property
    def redis_enabled(self) -> bool:
        return bool(self.redis_url)

    @property
    def local_blob_enabled(self) -> bool:
        return self.blob_backend == "local"


def build_storage_settings(
    *,
    project_root: Path | None = None,
    workspace_root: Path | None = None,
) -> StorageSettings:
    resolved_project_root = Path(project_root).expanduser().resolve() if project_root is not None else resolve_project_root().resolve()
    resolved_workspace_root = Path(workspace_root).expanduser().resolve() if workspace_root is not None else (resolved_project_root / ".ot-workspace").resolve()
    blob_root = Path(os.getenv("OT_BLOB_ROOT") or (resolved_workspace_root / "blob-store")).expanduser()
    if not blob_root.is_absolute():
        blob_root = (resolved_project_root / blob_root).resolve()
    return StorageSettings(
        project_root=resolved_project_root,
        workspace_root=resolved_workspace_root,
        db_dsn=(os.getenv("OT_DB_DSN") or "").strip() or None,
        redis_url=(os.getenv("OT_REDIS_URL") or "").strip() or None,
        blob_backend=(os.getenv("OT_BLOB_BACKEND") or "local").strip().lower(),
        blob_root=blob_root,
        blob_bucket=(os.getenv("OT_BLOB_BUCKET") or "").strip() or None,
        blob_endpoint=(os.getenv("OT_BLOB_ENDPOINT") or "").strip() or None,
        blob_region=(os.getenv("OT_BLOB_REGION") or "").strip() or None,
        blob_prefix=(os.getenv("OT_BLOB_PREFIX") or "ot-runtime").strip("/"),
        cache_ttl_overview=int(os.getenv("OT_CACHE_TTL_OVERVIEW") or "30"),
        cache_ttl_active_runs=int(os.getenv("OT_CACHE_TTL_ACTIVE_RUNS") or "10"),
        cache_ttl_session_summary=int(os.getenv("OT_CACHE_TTL_SESSION_SUMMARY") or "60"),
        cache_ttl_evolution_summary=int(os.getenv("OT_CACHE_TTL_EVOLUTION_SUMMARY") or "30"),
        inline_payload_limit_bytes=int(os.getenv("OT_INLINE_PAYLOAD_LIMIT_BYTES") or "4096"),
    )
