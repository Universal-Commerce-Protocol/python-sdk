"""Service models."""

from __future__ import annotations

from .models import (
    ServiceBase,
    ServiceBusinessSchema,
    ServicePlatformSchema,
    ServiceResponseSchema,
)

Base = ServiceBase
__all__ = [
    "ServiceBase",
    "ServiceBusinessSchema",
    "ServicePlatformSchema",
    "ServiceResponseSchema",
    "Base",
]
