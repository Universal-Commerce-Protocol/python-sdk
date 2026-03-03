from pydantic import BaseModel, ConfigDict, Field, RootModel

from .types.fulfillment_req import FulfillmentRequest


class Fulfillment(RootModel[FulfillmentRequest]):
  root: FulfillmentRequest = Field(..., title="Fulfillment")


class Checkout(BaseModel):
  model_config = ConfigDict(
    extra="allow",
  )
  fulfillment: Fulfillment | None = None


__all__ = ["Checkout", "Fulfillment"]
