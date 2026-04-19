from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable


AdapterType = Literal["data_source", "execution"]


class AdapterRegistryError(ValueError):
    """Raised when adapter registration or resolution violates registry rules."""


class AdapterCapabilityError(KeyError):
    """Raised when a caller requests a capability that an adapter does not support."""


@dataclass(slots=True, frozen=True)
class AdapterCapability:
    capability_id: str
    display_name: str
    description: str = ""
    tags: tuple[str, ...] = ()
    required_payload_keys: tuple[str, ...] = ()
    optional_payload_keys: tuple[str, ...] = ()
    normalized_result_keys: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "display_name": self.display_name,
            "description": self.description,
            "tags": list(self.tags),
            "required_payload_keys": list(self.required_payload_keys),
            "optional_payload_keys": list(self.optional_payload_keys),
            "normalized_result_keys": list(self.normalized_result_keys),
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True, frozen=True)
class AdapterManifest:
    adapter_id: str
    adapter_type: AdapterType
    adapter_version: str
    title: str
    summary: str
    capabilities: tuple[AdapterCapability, ...] = ()
    tags: tuple[str, ...] = ()
    wraps: tuple[str, ...] = ()
    is_builtin: bool = False
    workspace_compatibility: tuple[str, ...] = ("local",)
    metadata: dict[str, Any] = field(default_factory=dict)

    def supports(self, capability_id: str) -> bool:
        return any(item.capability_id == capability_id for item in self.capabilities)

    def capability_ids(self) -> tuple[str, ...]:
        return tuple(item.capability_id for item in self.capabilities)

    def capability(self, capability_id: str) -> AdapterCapability:
        for item in self.capabilities:
            if item.capability_id == capability_id:
                return item
        raise AdapterCapabilityError(f"manifest does not define capability: {capability_id}")

    def as_dict(self) -> dict[str, Any]:
        return {
            "adapter_id": self.adapter_id,
            "adapter_type": self.adapter_type,
            "adapter_version": self.adapter_version,
            "title": self.title,
            "summary": self.summary,
            "capabilities": [item.as_dict() for item in self.capabilities],
            "tags": list(self.tags),
            "wraps": list(self.wraps),
            "is_builtin": self.is_builtin,
            "workspace_compatibility": list(self.workspace_compatibility),
            "metadata": dict(self.metadata),
        }


@runtime_checkable
class AdapterContract(Protocol):
    manifest: AdapterManifest

    def describe(self) -> dict[str, Any]: ...

    def supports_capability(self, capability_id: str) -> bool: ...


@runtime_checkable
class DataSourceAdapter(AdapterContract, Protocol):
    def invoke(
        self,
        capability_id: str,
        payload: dict[str, Any],
        *,
        workspace_dir: Path | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]: ...


@runtime_checkable
class ExecutionAdapter(AdapterContract, Protocol):
    def invoke(
        self,
        capability_id: str,
        payload: dict[str, Any],
        *,
        workspace_dir: Path | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]: ...


@dataclass(slots=True, frozen=True)
class AdapterResultEnvelope:
    adapter_id: str
    adapter_type: AdapterType
    capability_id: str
    ok: bool
    request_id: str | None = None
    summary: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] | None = None
    artifacts: tuple[dict[str, Any], ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    normalized_payload_version: str = "1.0"

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "adapter_id": self.adapter_id,
            "adapter_type": self.adapter_type,
            "capability_id": self.capability_id,
            "request_id": self.request_id,
            "summary": self.summary,
            "payload": dict(self.payload),
            "result": dict(self.result),
            "error": None if self.error is None else dict(self.error),
            "artifacts": [dict(item) for item in self.artifacts],
            "metadata": dict(self.metadata),
            "normalized_payload_version": self.normalized_payload_version,
        }
