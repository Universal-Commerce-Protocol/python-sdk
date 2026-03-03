from typing import Any

from pydantic import BaseModel, ConfigDict


class Checkout(BaseModel):
  model_config = ConfigDict(
    extra="allow",
  )
  discounts: Any | None = None


__all__ = ["Checkout"]
