from __future__ import annotations

from typing import Any, Iterable

from .templates import build_flow_templates

from .models import FlowTemplate


class FlowTemplateRegistry:
    def __init__(self, templates: Iterable[FlowTemplate] | None = None) -> None:
        self._templates = tuple(templates or ())

    def list(self) -> list[FlowTemplate]:
        return list(self._templates)

    def get(self, template_id: str) -> FlowTemplate:
        for template in self._templates:
            if template.template_id == template_id:
                return template
        raise KeyError(f"Unknown flow template: {template_id}")

    def to_dict(self) -> dict[str, Any]:
        return {"templates": [template.to_dict() for template in self.list()]}


def build_default_flow_registry() -> FlowTemplateRegistry:
    return FlowTemplateRegistry(build_flow_templates())


def list_flow_templates() -> list[FlowTemplate]:
    return build_default_flow_registry().list()
