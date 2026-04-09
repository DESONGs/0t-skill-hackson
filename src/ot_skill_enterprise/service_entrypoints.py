from __future__ import annotations

import importlib.util
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def service_root() -> Path:
    return project_root() / "services" / "ave-data-service"


def _load_module(module_name: str, path: Path) -> ModuleType:
    if str(path.parent) not in sys.path:
        sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def load_service_module() -> ModuleType:
    return _load_module("ot_ave_data_service_main", service_root() / "main.py")


def load_providers_module() -> ModuleType:
    return _load_module("ot_ave_data_service_providers", service_root() / "providers.py")


def load_service_app() -> Any:
    module = load_service_module()
    return getattr(module, "app")


def build_ave_provider() -> Any:
    providers = load_providers_module()
    builder = getattr(providers, "build_provider")
    return builder()


def _serve_with_stdlib(host: str, port: int) -> int:
    service_module = load_service_module()
    provider = build_ave_provider()
    run_operation = getattr(service_module, "_run_operation")
    envelope = getattr(service_module, "_envelope")
    new_request_id = getattr(service_module, "new_request_id")

    operation_routes = {
        "/v1/discover_tokens": "discover_tokens",
        "/v1/inspect_token": "inspect_token",
        "/v1/inspect_market": "inspect_market",
        "/v1/inspect_wallet": "inspect_wallet",
        "/v1/review_signals": "review_signals",
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            return

        def _send_json(self, status_code: int, payload: Any) -> None:
            data = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _read_json_body(self) -> dict[str, Any] | None:
            content_length = int(self.headers.get("Content-Length", "0"))
            if content_length <= 0:
                return None
            raw = self.rfile.read(content_length)
            if not raw:
                return None
            return json.loads(raw.decode("utf-8"))

        def do_GET(self) -> None:  # noqa: N802
            if self.path != "/healthz":
                self._send_json(404, {"detail": "Not Found"})
                return
            request_id = new_request_id()
            payload = envelope(
                ok=True,
                operation="healthz",
                data={"status": "ok", "service": "ave-data-service"},
                provider=provider.name,
                request_id=request_id,
                latency_ms=0,
            )
            self._send_json(200, payload)

        def do_POST(self) -> None:  # noqa: N802
            try:
                payload = self._read_json_body()
            except json.JSONDecodeError:
                self._send_json(422, {"detail": "Invalid JSON body"})
                return

            if self.path.startswith("/v1/operations/"):
                operation = self.path.rsplit("/", 1)[-1]
            else:
                operation = operation_routes.get(self.path)

            if not operation:
                self._send_json(404, {"detail": "Not Found"})
                return

            response = run_operation(provider, operation, payload)
            self._send_json(200, response)

    server = ThreadingHTTPServer((host, port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


def main() -> int:
    host = os.getenv("AVE_DATA_SERVICE_BIND_HOST", "127.0.0.1")
    port = int(os.getenv("AVE_DATA_SERVICE_PORT", "8080"))
    try:
        import uvicorn
    except Exception:
        return _serve_with_stdlib(host, port)

    app = load_service_app()
    uvicorn.run(app, host=host, port=port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
