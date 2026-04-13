"""Compatibility shims for legacy skill-facing provider wrappers."""

from .gateway import GatewayCompatRunner, run_action

__all__ = ["GatewayCompatRunner", "run_action"]
