from __future__ import annotations

import asyncio
import inspect
import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable


class HTTPException(Exception):
    def __init__(self, status_code: int, detail: Any) -> None:
        super().__init__(str(detail))
        self.status_code = status_code
        self.detail = detail


@dataclass(slots=True)
class Route:
    method: str
    path: str
    handler: Callable[..., Any]


class FastAPI:
    def __init__(self, title: str = "", version: str = "", description: str = "") -> None:
        self.title = title
        self.version = version
        self.description = description
        self.routes: list[Route] = []

    def get(self, path: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        return self._register("GET", path)

    def post(self, path: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        return self._register("POST", path)

    def _register(self, method: str, path: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(handler: Callable[..., Any]) -> Callable[..., Any]:
            self.routes.append(Route(method=method.upper(), path=path, handler=handler))
            return handler

        return decorator

    def _match_path(self, route_path: str, request_path: str) -> dict[str, str] | None:
        route_parts = route_path.strip("/").split("/")
        request_parts = request_path.strip("/").split("/")
        if route_parts == [""] and request_parts == [""]:
            return {}
        if len(route_parts) != len(request_parts):
            return None
        params: dict[str, str] = {}
        for route_part, request_part in zip(route_parts, request_parts):
            if route_part.startswith("{") and route_part.endswith("}"):
                params[route_part[1:-1]] = request_part
                continue
            if route_part != request_part:
                return None
        return params

    def _find_route(self, method: str, path: str) -> tuple[Route, dict[str, str]] | None:
        for route in self.routes:
            if route.method != method:
                continue
            params = self._match_path(route.path, path)
            if params is not None:
                return route, params
        return None

    async def __call__(self, scope: dict[str, Any], receive: Callable[..., Awaitable[dict[str, Any]]], send: Callable[..., Awaitable[None]]) -> None:
        if scope["type"] != "http":
            return

        method = scope["method"].upper()
        path = scope["path"]
        matched = self._find_route(method, path)
        if matched is None:
            await self._send_json(send, 404, {"detail": "Not Found"})
            return
        route, path_params = matched

        body = b""
        while True:
            message = await receive()
            if message["type"] != "http.request":
                continue
            body += message.get("body", b"")
            if not message.get("more_body", False):
                break

        request_data: Any = None
        if body:
            try:
                request_data = json.loads(body.decode("utf-8"))
            except json.JSONDecodeError:
                await self._send_json(send, 422, {"detail": "Invalid JSON body"})
                return

        try:
            result = self._invoke(route.handler, request_data, path_params)
            if inspect.isawaitable(result):
                result = await result
            status_code = 200
            if isinstance(result, tuple) and len(result) == 2:
                status_code, result = result
            await self._send_json(send, status_code, result)
        except HTTPException as exc:
            await self._send_json(send, exc.status_code, {"detail": exc.detail})
        except Exception as exc:  # pragma: no cover - safety net
            await self._send_json(send, 500, {"detail": str(exc)})

    def _invoke(self, handler: Callable[..., Any], request_data: Any, path_params: dict[str, str]) -> Any:
        signature = inspect.signature(handler)
        kwargs: dict[str, Any] = {}
        for name, parameter in signature.parameters.items():
            if parameter.kind not in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            ):
                continue
            if name in path_params:
                kwargs[name] = path_params[name]
                continue
            kwargs[name] = request_data
        return handler(**kwargs)

    async def _send_json(self, send: Callable[..., Awaitable[None]], status_code: int, payload: Any) -> None:
        data = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": status_code,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(data)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": data})
