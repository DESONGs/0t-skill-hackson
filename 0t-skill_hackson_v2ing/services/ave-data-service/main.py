from __future__ import annotations

import time
from typing import Any

try:  # pragma: no cover - exercised implicitly when FastAPI exists
    from fastapi import Body, FastAPI
except Exception:  # pragma: no cover - current environment fallback
    from framework import FastAPI

    Body = lambda *args, **kwargs: None  # type: ignore[assignment]

from errors import ErrorCode, ServiceError, normalize_error_details
from models import (
    DiscoverTokensRequest,
    InspectMarketRequest,
    InspectTokenRequest,
    InspectWalletRequest,
    ProviderName,
    ReviewSignalsRequest,
    make_meta,
    model_dump,
    model_validate,
    new_request_id,
)
from providers import ProviderAdapter, build_provider

REQUEST_MODELS = {
    "discover_tokens": DiscoverTokensRequest,
    "inspect_token": InspectTokenRequest,
    "inspect_market": InspectMarketRequest,
    "inspect_wallet": InspectWalletRequest,
    "review_signals": ReviewSignalsRequest,
}


def _envelope(
    *,
    ok: bool,
    operation: str,
    data: dict[str, Any] | None,
    provider: ProviderName,
    request_id: str,
    latency_ms: int,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "operation": operation,
        "request_id": request_id,
        "data": data or {},
        "error": error,
        "meta": model_dump(make_meta(provider=provider, request_id=request_id, latency_ms=latency_ms)),
    }


def _service_error_to_envelope(
    error: ServiceError,
    *,
    operation: str,
    provider: ProviderName,
    request_id: str,
    latency_ms: int,
) -> dict[str, Any]:
    return _envelope(
        ok=False,
        operation=operation,
        data={},
        provider=provider,
        request_id=request_id,
        latency_ms=latency_ms,
        error={
            "code": error.code,
            "message": error.message,
            "details": normalize_error_details(error.details),
        },
    )


def _parse_request(model_cls, payload: Any, *, request_id: str):
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ServiceError(
            code=ErrorCode.VALIDATION_ERROR,
            message="Request body must be a JSON object",
            status_code=422,
            details={"request_id": request_id},
        )
    payload = dict(payload)
    try:
        return model_validate(model_cls, payload)
    except Exception as exc:
        raise ServiceError(
            code=ErrorCode.VALIDATION_ERROR,
            message="Request validation failed",
            status_code=422,
            details={"request_id": request_id, "reason": str(exc)},
        ) from exc


def _run_operation(provider: ProviderAdapter, operation: str, payload: dict[str, Any] | None) -> dict[str, Any]:
    request_id = new_request_id()
    started = time.perf_counter()
    try:
        model_cls = REQUEST_MODELS[operation]
        request = _parse_request(model_cls, payload, request_id=request_id)
        handler = getattr(provider, operation)
        data = handler(request)
        return _envelope(
            ok=True,
            operation=operation,
            data=data,
            provider=provider.name,
            request_id=request_id,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
    except KeyError as exc:
        error = ServiceError(
            code=ErrorCode.NOT_FOUND,
            message=f"Unsupported operation: {operation}",
            status_code=404,
            details={"operation": operation, "request_id": request_id},
        )
        return _service_error_to_envelope(
            error,
            operation=operation,
            provider=provider.name,
            request_id=request_id,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
    except ServiceError as error:
        return _service_error_to_envelope(
            error,
            operation=operation,
            provider=provider.name,
            request_id=request_id,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )


def create_app(provider: ProviderAdapter | None = None) -> FastAPI:
    app = FastAPI(
        title="AVE Data Service",
        version="0.1.0",
        description="Minimal AVE data-only service with adapter boundary and standard envelope.",
    )
    provider = provider or build_provider()

    @app.get("/healthz")
    async def health() -> dict[str, Any]:
        request_id = new_request_id()
        return _envelope(
            ok=True,
            operation="healthz",
            data={"status": "ok", "service": "ave-data-service"},
            provider=provider.name,
            request_id=request_id,
            latency_ms=0,
        )

    @app.post("/v1/operations/{operation}")
    async def operation_route(operation: str, payload: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
        return _run_operation(provider, operation, payload)

    @app.post("/v1/discover_tokens")
    async def discover_tokens(payload: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
        return _run_operation(provider, "discover_tokens", payload)

    @app.post("/v1/inspect_token")
    async def inspect_token(payload: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
        return _run_operation(provider, "inspect_token", payload)

    @app.post("/v1/inspect_market")
    async def inspect_market(payload: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
        return _run_operation(provider, "inspect_market", payload)

    @app.post("/v1/inspect_wallet")
    async def inspect_wallet(payload: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
        return _run_operation(provider, "inspect_wallet", payload)

    @app.post("/v1/review_signals")
    async def review_signals(payload: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
        return _run_operation(provider, "review_signals", payload)

    return app


app = create_app()
