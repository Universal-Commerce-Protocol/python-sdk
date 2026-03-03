from pydantic import BaseModel, ConfigDict

from .fulfillment_create_req import Fulfillment


class Checkout(BaseModel):
  model_config = ConfigDict(
    extra="allow",
  )
  fulfillment: Fulfillment | None = None


__all__ = ["Checkout"]
