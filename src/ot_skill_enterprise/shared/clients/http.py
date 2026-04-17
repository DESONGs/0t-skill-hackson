from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional
from urllib import error, request
from urllib.parse import urljoin


@dataclass(frozen=True)
class HttpRequest:
    method: str
    url: str
    headers: dict[str, str]
    body: Optional[bytes]
    timeout: float


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    headers: dict[str, str]
    body: bytes


class HttpClientError(RuntimeError):
    def __init__(self, message: str, *, status_code: Optional[int] = None, body: Optional[str] = None, payload: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body
        self.payload = payload


Transport = Callable[[HttpRequest], HttpResponse]


def _default_transport(http_request: HttpRequest) -> HttpResponse:
    req = request.Request(
        http_request.url,
        data=http_request.body,
        headers=http_request.headers,
        method=http_request.method,
    )
    try:
        with request.urlopen(req, timeout=http_request.timeout) as response:
            return HttpResponse(
                status_code=getattr(response, "status", 200),
                headers={key: value for key, value in response.headers.items()},
                body=response.read() or b"",
            )
    except error.HTTPError as exc:
        return HttpResponse(
            status_code=exc.code,
            headers={key: value for key, value in (exc.headers or {}).items()},
            body=exc.read() or b"",
        )


class HttpJsonClient:
    def __init__(
        self,
        base_url: str,
        *,
        default_headers: Optional[Mapping[str, str]] = None,
        timeout: float = 10.0,
        transport: Optional[Transport] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.default_headers = {"accept": "application/json", **dict(default_headers or {})}
        self.timeout = timeout
        self.transport = transport or _default_transport

    def request_json(
        self,
        method: str,
        path: str,
        *,
        payload: Any = None,
        headers: Optional[Mapping[str, str]] = None,
    ) -> Any:
        body = None if payload is None else json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        merged_headers = {**self.default_headers, **dict(headers or {})}
        if body is not None:
            merged_headers.setdefault("content-type", "application/json")
        http_request = HttpRequest(
            method=method.upper(),
            url=urljoin(f"{self.base_url}/", path.lstrip("/")),
            headers=merged_headers,
            body=body,
            timeout=self.timeout,
        )
        response = self.transport(http_request)
        text = response.body.decode("utf-8") if response.body else ""
        if not (200 <= response.status_code < 300):
            payload_data: Any = None
            if text:
                try:
                    payload_data = json.loads(text)
                except json.JSONDecodeError:
                    payload_data = text
            raise HttpClientError(
                f"request failed with status {response.status_code}",
                status_code=response.status_code,
                body=text or None,
                payload=payload_data,
            )
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise HttpClientError("response was not valid JSON", status_code=response.status_code, body=text) from exc

    def get_json(self, path: str, *, headers: Optional[Mapping[str, str]] = None) -> Any:
        return self.request_json("GET", path, headers=headers)

    def post_json(self, path: str, payload: Any, *, headers: Optional[Mapping[str, str]] = None) -> Any:
        return self.request_json("POST", path, payload=payload, headers=headers)
