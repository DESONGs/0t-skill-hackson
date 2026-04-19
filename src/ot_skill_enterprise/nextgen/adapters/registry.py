from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import AdapterContract, AdapterManifest, AdapterRegistryError, AdapterType


AdapterInstance = DataSourceOrExecution = AdapterContract


@dataclass(slots=True)
class AdapterRegistration:
    manifest: AdapterManifest
    adapter: AdapterInstance
    default: bool = False
    source: str = "builtin"

    def as_dict(self) -> dict[str, Any]:
        payload = self.manifest.as_dict()
        payload.update(
            {
                "default": self.default,
                "source": self.source,
            }
        )
        return payload


class AdapterRegistry:
    def __init__(self) -> None:
        self._registrations: dict[str, AdapterRegistration] = {}
        self._defaults: dict[AdapterType, str] = {}
        self._capability_index: dict[AdapterType, dict[str, list[str]]] = {
            "data_source": {},
            "execution": {},
        }

    def register(
        self,
        adapter: AdapterInstance,
        *,
        manifest_override: AdapterManifest | None = None,
        default: bool = False,
        source: str = "builtin",
    ) -> AdapterRegistration:
        manifest = manifest_override or adapter.manifest
        if manifest.adapter_id in self._registrations:
            raise AdapterRegistryError(f"adapter already registered: {manifest.adapter_id}")
        if default:
            existing = self._defaults.get(manifest.adapter_type)
            if existing and existing != manifest.adapter_id:
                raise AdapterRegistryError(
                    f"default adapter already registered for {manifest.adapter_type}: {existing}"
                )
        registration = AdapterRegistration(
            manifest=manifest,
            adapter=adapter,
            default=default,
            source=source,
        )
        self._registrations[manifest.adapter_id] = registration
        if default:
            self._defaults[manifest.adapter_type] = manifest.adapter_id
        for capability_id in manifest.capability_ids():
            self._capability_index[manifest.adapter_type].setdefault(capability_id, []).append(manifest.adapter_id)
        return registration

    @classmethod
    def from_registrations(
        cls,
        registrations: list[tuple[AdapterInstance, AdapterManifest | None, bool, str]],
    ) -> "AdapterRegistry":
        registry = cls()
        for adapter, manifest_override, default, source in registrations:
            registry.register(adapter, manifest_override=manifest_override, default=default, source=source)
        return registry

    def get_registration(self, adapter_id: str) -> AdapterRegistration:
        try:
            return self._registrations[adapter_id]
        except KeyError as exc:
            raise AdapterRegistryError(f"adapter not registered: {adapter_id}") from exc

    def get(self, adapter_id: str) -> AdapterInstance:
        return self.get_registration(adapter_id).adapter

    def defaults(self) -> dict[str, str]:
        return dict(self._defaults)

    def list_registrations(self, adapter_type: AdapterType | None = None) -> list[AdapterRegistration]:
        registrations = list(self._registrations.values())
        if adapter_type is None:
            return registrations
        return [item for item in registrations if item.manifest.adapter_type == adapter_type]

    def capability_matrix(self, adapter_type: AdapterType | None = None) -> dict[str, list[str]]:
        if adapter_type is not None:
            return {key: list(value) for key, value in self._capability_index[adapter_type].items()}
        payload: dict[str, list[str]] = {}
        for bucket in self._capability_index.values():
            for capability_id, adapter_ids in bucket.items():
                payload.setdefault(capability_id, [])
                payload[capability_id].extend(adapter_ids)
        return payload

    def describe(self, adapter_type: AdapterType | None = None) -> list[dict[str, Any]]:
        return [item.as_dict() for item in self.list_registrations(adapter_type=adapter_type)]

    def resolve(
        self,
        adapter_type: AdapterType,
        *,
        adapter_id: str | None = None,
        capability_id: str | None = None,
        required_capabilities: tuple[str, ...] = (),
    ) -> AdapterInstance:
        registration = self.resolve_registration(
            adapter_type,
            adapter_id=adapter_id,
            capability_id=capability_id,
            required_capabilities=required_capabilities,
        )
        return registration.adapter

    def resolve_registration(
        self,
        adapter_type: AdapterType,
        *,
        adapter_id: str | None = None,
        capability_id: str | None = None,
        required_capabilities: tuple[str, ...] = (),
    ) -> AdapterRegistration:
        if adapter_id is not None:
            registration = self.get_registration(adapter_id)
            if registration.manifest.adapter_type != adapter_type:
                raise AdapterRegistryError(
                    f"adapter {adapter_id} is {registration.manifest.adapter_type}, not {adapter_type}"
                )
            self._ensure_capabilities(registration, capability_id=capability_id, required_capabilities=required_capabilities)
            return registration

        candidates = self.list_registrations(adapter_type=adapter_type)
        if capability_id is not None:
            candidate_ids = self._capability_index[adapter_type].get(capability_id, [])
            candidates = [self._registrations[item] for item in candidate_ids]
        if not candidates:
            message = f"no adapter registered for {adapter_type}"
            if capability_id:
                message = f"no adapter registered for {adapter_type} capability {capability_id}"
            raise AdapterRegistryError(message)

        default_id = self._defaults.get(adapter_type)
        ordered = candidates
        if default_id:
            ordered = sorted(candidates, key=lambda item: item.manifest.adapter_id != default_id)
        for registration in ordered:
            if self._supports_all(registration, capability_id=capability_id, required_capabilities=required_capabilities):
                return registration
        capability_list = [capability_id] if capability_id else []
        capability_list.extend(required_capabilities)
        raise AdapterRegistryError(
            f"no adapter for {adapter_type} satisfies capabilities: {', '.join(capability_list)}"
        )

    def _supports_all(
        self,
        registration: AdapterRegistration,
        *,
        capability_id: str | None,
        required_capabilities: tuple[str, ...],
    ) -> bool:
        if capability_id and not registration.adapter.supports_capability(capability_id):
            return False
        return all(registration.adapter.supports_capability(item) for item in required_capabilities)

    def _ensure_capabilities(
        self,
        registration: AdapterRegistration,
        *,
        capability_id: str | None,
        required_capabilities: tuple[str, ...],
    ) -> None:
        if not self._supports_all(registration, capability_id=capability_id, required_capabilities=required_capabilities):
            capability_list = [capability_id] if capability_id else []
            capability_list.extend(required_capabilities)
            raise AdapterRegistryError(
                f"adapter {registration.manifest.adapter_id} does not satisfy capabilities: {', '.join(capability_list)}"
            )
