from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

from .config import StorageSettings, build_storage_settings


class ProjectionCache(Protocol):
    def get_json(self, key: str) -> dict[str, Any] | None: ...

    def set_json(self, key: str, payload: dict[str, Any], *, ttl_seconds: int) -> None: ...

    def delete_keys(self, *keys: str) -> None: ...


@dataclass(slots=True)
class NullProjectionCache:
    def get_json(self, key: str) -> dict[str, Any] | None:
        return None

    def set_json(self, key: str, payload: dict[str, Any], *, ttl_seconds: int) -> None:
        return None

    def delete_keys(self, *keys: str) -> None:
        return None


@dataclass(slots=True)
class RedisProjectionCache:
    redis_url: str
    _client: Any | None = None

    def _client_factory(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from redis import Redis
        except Exception as exc:  # pragma: no cover - optional dependency path
            raise RuntimeError("redis package is required for OT_REDIS_URL") from exc
        self._client = Redis.from_url(self.redis_url, decode_responses=True)
        return self._client

    def get_json(self, key: str) -> dict[str, Any] | None:
        raw = self._client_factory().get(key)
        if not raw:
            return None
        return json.loads(raw)

    def set_json(self, key: str, payload: dict[str, Any], *, ttl_seconds: int) -> None:
        self._client_factory().setex(key, ttl_seconds, json.dumps(payload, ensure_ascii=False, default=str))

    def delete_keys(self, *keys: str) -> None:
        filtered = [key for key in keys if key]
        if not filtered:
            return
        self._client_factory().delete(*filtered)


def build_projection_cache(*, settings: StorageSettings | None = None) -> ProjectionCache:
    resolved = settings or build_storage_settings()
    if resolved.redis_enabled:
        return RedisProjectionCache(redis_url=resolved.redis_url or "")
    return NullProjectionCache()
