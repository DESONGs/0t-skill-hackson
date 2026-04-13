"""Agent integration layer.

This package is intentionally thin. It does not execute tasks on behalf of an
agent; it normalizes metadata about external agents so runs can be observed and
governed consistently.
"""

from .models import AgentAdapter, AgentCapability, AgentRegistration
from .registry import AgentAdapterRegistry, default_agent_registry
from .store import AgentStore, build_agent_store

__all__ = [
    "AgentAdapter",
    "AgentCapability",
    "AgentRegistration",
    "AgentAdapterRegistry",
    "AgentStore",
    "build_agent_store",
    "default_agent_registry",
]
