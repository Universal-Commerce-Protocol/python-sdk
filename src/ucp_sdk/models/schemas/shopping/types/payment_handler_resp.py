from __future__ import annotations

from typing import Any

from pydantic import AnyUrl, BaseModel, ConfigDict

from ..._internal import Version


class PaymentHandlerResponse(BaseModel):
  model_config = ConfigDict(
    extra="allow",
  )
  id: str | None = None
  name: str | None = None
  version: Version | None = None
  spec: AnyUrl | str | None = None
  config_schema: AnyUrl | str | None = None
  instrument_schemas: list[AnyUrl | str] | None = None
  config: dict[str, Any] | None = None


__all__ = ["PaymentHandlerResponse"]
