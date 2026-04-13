"""Built-in control-plane flow templates.

These templates mirror the currently available analysis presets while giving
the control plane a stable home for future agent-agnostic flows.
"""

from .analysis import build_flow_templates

__all__ = ["build_flow_templates"]
