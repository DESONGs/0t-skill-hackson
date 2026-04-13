from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .config import StorageSettings, build_storage_settings


def _json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str).encode("utf-8")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True, slots=True)
class BlobWriteResult:
    uri: str
    checksum: str
    size_bytes: int
    content_type: str


class BlobStore(Protocol):
    def put_json(self, key: str, payload: Any) -> BlobWriteResult: ...

    def put_bytes(self, key: str, data: bytes, *, content_type: str = "application/octet-stream") -> BlobWriteResult: ...

    def read_bytes(self, uri: str) -> bytes: ...


@dataclass(slots=True)
class LocalBlobStore:
    root: Path

    def __post_init__(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def _path_for_key(self, key: str) -> Path:
        clean = key.lstrip("/").replace("..", "_")
        path = self.root / clean
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def put_json(self, key: str, payload: Any) -> BlobWriteResult:
        return self.put_bytes(key, _json_bytes(payload), content_type="application/json")

    def put_bytes(self, key: str, data: bytes, *, content_type: str = "application/octet-stream") -> BlobWriteResult:
        path = self._path_for_key(key)
        path.write_bytes(data)
        return BlobWriteResult(
            uri=f"file://{path}",
            checksum=_sha256_bytes(data),
            size_bytes=len(data),
            content_type=content_type,
        )

    def read_bytes(self, uri: str) -> bytes:
        if uri.startswith("file://"):
            return Path(uri.removeprefix("file://")).read_bytes()
        return Path(uri).read_bytes()


@dataclass(slots=True)
class S3BlobStore:
    bucket: str
    prefix: str
    endpoint_url: str | None = None
    region_name: str | None = None
    _client: Any | None = None

    def _client_factory(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import boto3
        except Exception as exc:  # pragma: no cover - optional dependency path
            raise RuntimeError("boto3 is required for S3 blob backend") from exc
        self._client = boto3.client("s3", endpoint_url=self.endpoint_url, region_name=self.region_name)
        return self._client

    def _object_key(self, key: str) -> str:
        clean = key.lstrip("/").replace("..", "_")
        return f"{self.prefix}/{clean}" if self.prefix else clean

    def put_json(self, key: str, payload: Any) -> BlobWriteResult:
        return self.put_bytes(key, _json_bytes(payload), content_type="application/json")

    def put_bytes(self, key: str, data: bytes, *, content_type: str = "application/octet-stream") -> BlobWriteResult:
        object_key = self._object_key(key)
        self._client_factory().put_object(Bucket=self.bucket, Key=object_key, Body=data, ContentType=content_type)
        return BlobWriteResult(
            uri=f"s3://{self.bucket}/{object_key}",
            checksum=_sha256_bytes(data),
            size_bytes=len(data),
            content_type=content_type,
        )

    def read_bytes(self, uri: str) -> bytes:
        if not uri.startswith("s3://"):
            raise ValueError(f"unsupported S3 blob uri: {uri}")
        _, _, remainder = uri.partition("s3://")
        bucket, _, key = remainder.partition("/")
        response = self._client_factory().get_object(Bucket=bucket, Key=key)
        return response["Body"].read()


def build_blob_store(
    *,
    settings: StorageSettings | None = None,
    project_root: Path | None = None,
    workspace_root: Path | None = None,
) -> BlobStore:
    resolved = settings or build_storage_settings(project_root=project_root, workspace_root=workspace_root)
    if resolved.blob_backend == "local":
        return LocalBlobStore(root=resolved.blob_root)
    if resolved.blob_backend in {"s3", "minio"}:
        if not resolved.blob_bucket:
            raise RuntimeError("OT_BLOB_BUCKET is required for s3/minio blob backend")
        return S3BlobStore(
            bucket=resolved.blob_bucket,
            prefix=resolved.blob_prefix,
            endpoint_url=resolved.blob_endpoint,
            region_name=resolved.blob_region,
        )
    raise RuntimeError(f"unsupported OT_BLOB_BACKEND: {resolved.blob_backend}")
