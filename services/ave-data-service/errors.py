from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    VALIDATION_ERROR = "VALIDATION_ERROR"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    NOT_FOUND = "NOT_FOUND"
    INTERNAL_ERROR = "INTERNAL_ERROR"


@dataclass(slots=True)
class ServiceError(Exception):
    code: ErrorCode
    message: str
    status_code: int = 500
    details: dict[str, Any] = field(default_factory=dict)


def normalize_error_details(details: dict[str, Any] | None) -> dict[str, Any]:
    return details or {}
