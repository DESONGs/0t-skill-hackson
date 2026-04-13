from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..contracts import ProviderAdapter


@dataclass(slots=True)
class ProviderRegistration:
    provider_name: str
    adapter: ProviderAdapter
    actions: tuple[str, ...] = field(default_factory=tuple)
    compat: bool = False
    tags: tuple[str, ...] = field(default_factory=tuple)


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, ProviderRegistration] = {}
        self._action_to_provider: dict[str, str] = {}

    def register(
        self,
        adapter: ProviderAdapter,
        *,
        provider_name: str | None = None,
        actions: tuple[str, ...] | None = None,
        compat: bool = False,
        tags: tuple[str, ...] = (),
    ) -> ProviderRegistration:
        resolved_actions = actions or getattr(adapter, "supported_actions", ())
        registration = ProviderRegistration(
            provider_name=provider_name or getattr(adapter, "name", adapter.__class__.__name__.lower()),
            adapter=adapter,
            actions=tuple(resolved_actions),
            compat=compat,
            tags=tuple(tags),
        )
        self._providers[registration.provider_name] = registration
        for action_name in registration.actions:
            self._action_to_provider[action_name] = registration.provider_name
        return registration

    def resolve(self, action_name: str) -> ProviderAdapter:
        provider_name = self._action_to_provider.get(action_name)
        if provider_name is None:
            raise KeyError(f"no provider registered for action {action_name}")
        return self._providers[provider_name].adapter

    def describe(self) -> list[dict[str, Any]]:
        return [
            {
                "provider_name": registration.provider_name,
                "actions": list(registration.actions),
                "compat": registration.compat,
                "tags": list(registration.tags),
            }
            for registration in self._providers.values()
        ]

    def registered_actions(self) -> tuple[str, ...]:
        return tuple(self._action_to_provider.keys())


def build_default_registry(adapter: ProviderAdapter) -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register(adapter, compat=True, tags=("ave", "data", "compat"))
    return registry
