from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Optional, TypeVar, Union

from pydantic import BaseModel

from ..contracts.ave_data import (
    DiscoverTokensRequest,
    DiscoverTokensResponse,
    InspectMarketRequest,
    InspectMarketResponse,
    InspectTokenRequest,
    InspectTokenResponse,
    InspectWalletRequest,
    InspectWalletResponse,
    ReviewSignalsRequest,
    ReviewSignalsResponse,
)
from .http import HttpJsonClient

ResponseT = TypeVar("ResponseT", bound=BaseModel)
RequestT = TypeVar("RequestT", bound=BaseModel)


def _normalize_request(
    request_model: type[RequestT],
    request: Optional[Union[RequestT, Mapping[str, Any]]],
    kwargs: dict[str, Any],
) -> RequestT:
    if isinstance(request, request_model):
        if kwargs:
            raise TypeError("cannot mix a model instance with keyword arguments")
        return request
    payload: dict[str, Any] = {}
    if request is not None:
        if not isinstance(request, Mapping):
            raise TypeError(f"expected {request_model.__name__} or mapping input")
        payload.update(request)
    payload.update(kwargs)
    return request_model.model_validate(payload)


class AveDataServiceClient:
    def __init__(
        self,
        base_url: str,
        *,
        api_key: Optional[str] = None,
        api_key_header: str = "authorization",
        api_key_prefix: str = "Bearer ",
        timeout: float = 10.0,
        http_client: Optional[HttpJsonClient] = None,
    ) -> None:
        headers: dict[str, str] = {}
        if api_key is not None:
            headers[api_key_header] = f"{api_key_prefix}{api_key}" if api_key_prefix else api_key
        if http_client is None:
            self.http = HttpJsonClient(base_url, default_headers=headers, timeout=timeout)
        elif headers:
            merged_headers = {**http_client.default_headers, **headers}
            self.http = HttpJsonClient(
                http_client.base_url,
                default_headers=merged_headers,
                timeout=http_client.timeout,
                transport=http_client.transport,
            )
        else:
            self.http = http_client

    def _call(self, operation: str, payload: BaseModel, response_model: type[ResponseT]) -> ResponseT:
        body = self.http.post_json(f"/v1/operations/{operation}", payload.model_dump(mode="json", exclude_none=True))
        return response_model.model_validate(body)

    def discover_tokens(
        self,
        request: Optional[Union[DiscoverTokensRequest, Mapping[str, Any]]] = None,
        **kwargs: Any,
    ) -> DiscoverTokensResponse:
        payload = _normalize_request(DiscoverTokensRequest, request, kwargs)
        return self._call("discover_tokens", payload, DiscoverTokensResponse)

    def inspect_token(
        self,
        request: Optional[Union[InspectTokenRequest, Mapping[str, Any]]] = None,
        **kwargs: Any,
    ) -> InspectTokenResponse:
        payload = _normalize_request(InspectTokenRequest, request, kwargs)
        return self._call("inspect_token", payload, InspectTokenResponse)

    def inspect_market(
        self,
        request: Optional[Union[InspectMarketRequest, Mapping[str, Any]]] = None,
        **kwargs: Any,
    ) -> InspectMarketResponse:
        payload = _normalize_request(InspectMarketRequest, request, kwargs)
        return self._call("inspect_market", payload, InspectMarketResponse)

    def inspect_wallet(
        self,
        request: Optional[Union[InspectWalletRequest, Mapping[str, Any]]] = None,
        **kwargs: Any,
    ) -> InspectWalletResponse:
        payload = _normalize_request(InspectWalletRequest, request, kwargs)
        return self._call("inspect_wallet", payload, InspectWalletResponse)

    def review_signals(
        self,
        request: Optional[Union[ReviewSignalsRequest, Mapping[str, Any]]] = None,
        **kwargs: Any,
    ) -> ReviewSignalsResponse:
        payload = _normalize_request(ReviewSignalsRequest, request, kwargs)
        return self._call("review_signals", payload, ReviewSignalsResponse)
