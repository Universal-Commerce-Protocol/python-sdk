from typing import Any

from pydantic import BaseModel, ConfigDict


class FulfillmentRequest(BaseModel):
  model_config = ConfigDict(
    extra="allow",
  )
  methods: list[Any] | None = None
  available_methods: list[Any] | None = None


__all__ = ["FulfillmentRequest"]
