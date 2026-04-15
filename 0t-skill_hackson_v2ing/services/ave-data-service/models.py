from __future__ import annotations

import sys
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

PROJECT_SRC = Path(__file__).resolve().parents[2] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(PROJECT_SRC))

from ot_skill_enterprise.shared.contracts import (  # noqa: E402
    DiscoverTokensRequest,
    EnvelopeMeta,
    InspectMarketRequest,
    InspectTokenRequest,
    InspectWalletRequest,
    ReviewSignalsRequest,
)


def model_validate(cls, payload: Any):
    validator = getattr(cls, "model_validate", None)
    if validator is not None:
        return validator(payload)
    return cls.parse_obj(payload)


def model_dump(instance: Any) -> dict[str, Any]:
    dumper = getattr(instance, "model_dump", None)
    if dumper is not None:
        return dumper(mode="json")
    return instance.dict()


class ProviderName(StrEnum):
    MOCK = "mock"
    AVE_REST = "ave_rest"


def new_request_id() -> str:
    return uuid4().hex


def make_meta(provider: ProviderName, request_id: str, latency_ms: int = 0) -> EnvelopeMeta:
    return EnvelopeMeta(
        provider=provider,
        request_id=request_id,
        latency_ms=latency_ms,
        cached=False,
        source="ave-data-service",
        timestamp=datetime.now(timezone.utc),
    )
