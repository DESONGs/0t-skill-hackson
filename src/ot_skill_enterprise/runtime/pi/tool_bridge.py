from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from uuid import uuid4

from ..models import RuntimeToolCall


ToolHandler = Callable[[Mapping[str, Any], dict[str, Any]], Any]


@dataclass(slots=True)
class ToolBinding:
    tool_name: str
    handler: ToolHandler
    description: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class PiToolBridge:
    def __init__(self, *, tool_bindings: Mapping[str, ToolHandler] | None = None) -> None:
        self._tools: dict[str, ToolBinding] = {}
        for tool_name, handler in dict(tool_bindings or {}).items():
            self.register_tool(tool_name, handler)

    def register_tool(
        self,
        tool_name: str,
        handler: ToolHandler,
        *,
        description: str = "",
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        self._tools[str(tool_name).strip()] = ToolBinding(
            tool_name=str(tool_name).strip(),
            handler=handler,
            description=description,
            metadata=dict(metadata or {}),
        )

    def describe(self) -> list[dict[str, Any]]:
        return [
            {
                "tool_name": binding.tool_name,
                "description": binding.description,
                "metadata": dict(binding.metadata),
            }
            for binding in self._tools.values()
        ]

    def has_tool(self, tool_name: str) -> bool:
        return str(tool_name).strip() in self._tools

    def dispatch(
        self,
        tool_name: str,
        args: Mapping[str, Any] | None = None,
        *,
        runtime_id: str,
        session_id: str,
        invocation_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> RuntimeToolCall:
        resolved_name = str(tool_name).strip()
        tool_call = RuntimeToolCall(
            tool_call_id=f"toolcall-{uuid4().hex[:10]}",
            runtime_id=runtime_id,
            session_id=session_id,
            invocation_id=invocation_id,
            tool_name=resolved_name,
            args=dict(args or {}),
            status="running",
            metadata=dict(metadata or {}),
        )
        binding = self._tools.get(resolved_name)
        if binding is None:
            tool_call.status = "missing"
            tool_call.result = {
                "ok": False,
                "error": {
                    "code": "tool_not_found",
                    "message": f"Tool {resolved_name!r} is not registered in the Pi tool bridge",
                },
            }
            return tool_call

        try:
            result = binding.handler(dict(args or {}), {"runtime_id": runtime_id, "session_id": session_id, "invocation_id": invocation_id, "metadata": dict(metadata or {})})
            tool_call.status = "succeeded"
            if hasattr(result, "model_dump"):
                dumped = result.model_dump(mode="json")
                tool_call.result = dumped if isinstance(dumped, dict) else {"value": dumped}
            elif isinstance(result, dict):
                tool_call.result = dict(result)
            else:
                tool_call.result = {"value": result}
        except Exception as exc:  # pragma: no cover - bridge is intentionally tolerant
            tool_call.status = "failed"
            tool_call.result = {
                "ok": False,
                "error": {
                    "code": "tool_execution_failed",
                    "message": str(exc),
                },
            }
        return tool_call

