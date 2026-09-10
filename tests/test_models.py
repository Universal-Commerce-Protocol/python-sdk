# Copyright 2026 UCP Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Rigorous unit tests for OpenAPI 3.1 generated Pydantic models."""

import unittest
from typing import cast

from pydantic import TypeAdapter, ValidationError

from ucp_sdk.models import (
    Cart,
    Checkout,
    Order,
)
from ucp_sdk.models.schemas.common.types.amount import Price
from ucp_sdk.models.schemas.common.types.payment import Payment
from ucp_sdk.models.schemas.common.types.payment_instrument import (
    AvailablePaymentInstrument,
    PaymentInstrument,
    SelectedPaymentInstrument,
)
from ucp_sdk.models.schemas.common.types.totals import (
    Total,
)
from ucp_sdk.models.schemas.shopping.cart import (
    CartCreateRequest,
    CartUpdateRequest,
)
from ucp_sdk.models.schemas.shopping.checkout import (
    CheckoutCompleteRequest,
    CheckoutCreateRequest,
    CheckoutUpdateRequest,
)
from ucp_sdk.models.schemas.shopping.order import (
    OrderCreateRequest,
    OrderUpdateRequest,
)
from ucp_sdk.models.schemas.shopping.types.fulfillment_destination import (
    FulfillmentDestination,
    LocationDestination,
    ShippingDestination,
)
from ucp_sdk.models.schemas.shopping.types.line_item import (
    LineItemCreateRequest,
)


class TestModelImportParity(unittest.TestCase):
    """Verify modular domain import paths and root exports resolve to identical classes."""

    def test_root_and_modular_class_identity(self) -> None:
        from ucp_sdk.models.schemas.shopping.cart import Cart as ModularCart
        from ucp_sdk.models.schemas.shopping.checkout import (
            Checkout as ModularCheckout,
        )
        from ucp_sdk.models.schemas.shopping.order import Order as ModularOrder

        self.assertIs(Checkout, ModularCheckout)
        self.assertIs(Cart, ModularCart)
        self.assertIs(Order, ModularOrder)


class TestPolymorphicFulfillmentDestination(unittest.TestCase):
    """Verify polymorphic discriminated union deserialization for fulfillment destinations."""

    def setUp(self) -> None:
        self.adapter = TypeAdapter(FulfillmentDestination)

    def test_deserialize_shipping_destination(self) -> None:
        raw = {
            "id": "dest_ship_001",
            "type": "shipping_address",
            "street_address": "1600 Amphitheatre Pkwy",
            "address_locality": "Mountain View",
            "address_region": "CA",
            "postal_code": "94043",
            "address_country": "US",
        }
        dest = cast(ShippingDestination, self.adapter.validate_python(raw))
        self.assertIsInstance(dest, ShippingDestination)
        self.assertEqual(dest.type, "shipping_address")
        self.assertEqual(dest.id, "dest_ship_001")
        self.assertEqual(dest.address_locality, "Mountain View")

    def test_deserialize_location_destination(self) -> None:
        raw = {
            "id": "dest_loc_002",
            "type": "business_location",
            "name": "Flagship Retail Store",
        }
        dest = cast(LocationDestination, self.adapter.validate_python(raw))
        self.assertIsInstance(dest, LocationDestination)
        self.assertEqual(dest.type, "business_location")
        self.assertEqual(dest.id, "dest_loc_002")
        self.assertEqual(dest.name, "Flagship Retail Store")

    def test_reject_invalid_destination_type(self) -> None:
        raw = {
            "id": "dest_err_003",
            "type": "drone_teleport",
            "name": "Pad 1",
        }
        with self.assertRaises(ValidationError):
            self.adapter.validate_python(raw)

    def test_reject_missing_discriminator(self) -> None:
        raw = {
            "id": "dest_err_004",
            "name": "Unknown",
        }
        with self.assertRaises(ValidationError):
            self.adapter.validate_python(raw)


class TestDirectionalRequestSlicing(unittest.TestCase):
    """Verify directional request models enforce input-side semantics."""

    def test_checkout_create_request_valid(self) -> None:
        req = CheckoutCreateRequest(
            currency="USD",
            line_items=[
                LineItemCreateRequest(
                    item={"id": "item_123"},
                    quantity=2,
                )
            ],
        )
        data = req.model_dump(exclude_none=True)
        self.assertEqual(data["currency"], "USD")
        self.assertEqual(len(data["line_items"]), 1)
        self.assertEqual(data["line_items"][0]["quantity"], 2)
        # Verify server-managed fields are not present on CreateRequest
        self.assertFalse(hasattr(req, "created_at"))
        self.assertFalse(hasattr(req, "completed_at"))

    def test_checkout_update_request(self) -> None:
        req = CheckoutUpdateRequest(
            currency="EUR",
            line_items=[],
        )
        self.assertEqual(req.currency, "EUR")
        self.assertEqual(req.line_items, [])

    def test_checkout_complete_request(self) -> None:
        req = CheckoutCompleteRequest(payment=Payment())
        self.assertIsNotNone(req.payment)

    def test_cart_create_and_update_requests(self) -> None:
        req = CartCreateRequest(currency="USD", line_items=[])
        self.assertEqual(req.currency, "USD")

        update = CartUpdateRequest(currency="GBP", line_items=[])
        self.assertEqual(update.currency, "GBP")

    def test_order_create_and_update_requests(self) -> None:
        req = OrderCreateRequest(
            ucp={"version": "2026-08-25"},
            id="ord_req_1",
            checkout_id="chk_1",
            permalink_url="https://example.com/orders/ord_req_1",
            line_items=[],
            fulfillment={"fulfillments": []},
            totals=[],
        )
        self.assertEqual(req.id, "ord_req_1")

        update = OrderUpdateRequest(
            ucp={"version": "2026-08-25"},
            id="ord_req_1",
            checkout_id="chk_1",
            permalink_url="https://example.com/orders/ord_req_1",
            line_items=[],
            fulfillment={"fulfillments": []},
            totals=[],
        )
        self.assertEqual(update.id, "ord_req_1")


class TestDomainEntitySerialization(unittest.TestCase):
    """Verify domain root models serialize and deserialize cleanly."""

    def test_checkout_roundtrip(self) -> None:
        checkout_dict = {
            "ucp": {"version": "2026-08-25", "payment_handlers": {}},
            "id": "chk_test_999",
            "status": "ready_for_complete",
            "currency": "USD",
            "line_items": [
                {
                    "id": "li_1",
                    "item": {"id": "prod_1", "title": "Widget", "price": 2500},
                    "quantity": 1,
                    "totals": [
                        {
                            "type": "subtotal",
                            "amount": 2500,
                        }
                    ],
                }
            ],
            "totals": [
                {
                    "type": "total",
                    "amount": 2500,
                },
            ],
            "links": [],
        }
        checkout = Checkout.model_validate(checkout_dict)
        self.assertEqual(checkout.id, "chk_test_999")
        self.assertEqual(checkout.currency, "USD")
        self.assertEqual(len(checkout.line_items), 1)
        self.assertEqual(checkout.line_items[0].id, "li_1")

        dumped = checkout.model_dump(exclude_none=True)
        self.assertEqual(dumped["id"], "chk_test_999")
        self.assertEqual(dumped["currency"], "USD")
        self.assertEqual(dumped["status"], "ready_for_complete")

    def test_cart_roundtrip(self) -> None:
        cart_dict = {
            "ucp": {"version": "2026-08-25"},
            "id": "cart_test_123",
            "currency": "USD",
            "line_items": [],
            "totals": [],
        }
        cart = Cart.model_validate(cart_dict)
        self.assertEqual(cart.id, "cart_test_123")
        dumped = cart.model_dump(exclude_none=True)
        self.assertEqual(dumped["id"], "cart_test_123")

    def test_order_roundtrip(self) -> None:
        order_dict = {
            "ucp": {"version": "2026-08-25"},
            "id": "ord_test_456",
            "checkout_id": "chk_test_999",
            "permalink_url": "https://example.com/orders/ord_test_456",
            "line_items": [],
            "fulfillment": {"fulfillments": []},
            "currency": "USD",
            "totals": [],
        }
        order = Order.model_validate(order_dict)
        self.assertEqual(order.id, "ord_test_456")
        dumped = order.model_dump(exclude_none=True)
        self.assertEqual(dumped["id"], "ord_test_456")


class TestCommonTypes(unittest.TestCase):
    """Verify common auxiliary types."""

    def test_totals_and_amounts(self) -> None:
        total = Total(type="tax", amount=150)
        self.assertEqual(total.type, "tax")
        self.assertEqual(total.amount, 150)

        price = Price(amount=5000, currency="USD")
        self.assertEqual(price.amount, 5000)
        self.assertEqual(price.currency, "USD")

    def test_payment_instrument(self) -> None:
        inst = AvailablePaymentInstrument(
            type="card",
        )
        self.assertEqual(inst.type, "card")

        pi = PaymentInstrument(id="pi_1", handler_id="ph_stripe", type="card")
        self.assertEqual(pi.type, "card")
        self.assertEqual(pi.id, "pi_1")
        self.assertEqual(pi.handler_id, "ph_stripe")

        spi = SelectedPaymentInstrument(
            id="pi_2", handler_id="ph_stripe", type="card", selected=True
        )
        self.assertEqual(spi.id, "pi_2")
        self.assertTrue(spi.selected)


class TestValidationInvariants(unittest.TestCase):
    """Verify strict Pydantic v2 type checking and validation invariants."""

    def test_amount_type_validation(self) -> None:
        # Price amount must be integer (cents/minor units)
        with self.assertRaises(ValidationError):
            Price(amount="not_a_number", currency="USD")  # type: ignore[arg-type]

    def test_signals_validation(self) -> None:
        from ucp_sdk.models.schemas.common.types.signals import Signals

        sig = Signals(ip="192.168.1.1", user_agent="Mozilla/5.0")
        self.assertEqual(sig.ip, "192.168.1.1")
        self.assertEqual(sig.user_agent, "Mozilla/5.0")

    def test_profile_and_jwk(self) -> None:
        from ucp_sdk.models.schemas.profile import JwkPublicKey, Profile

        key = JwkPublicKey(
            kid="key-1", kty="EC", crv="P-256", x="base64_x", y="base64_y"
        )
        self.assertEqual(key.kty, "EC")
        self.assertEqual(key.kid, "key-1")

        prof = Profile(
            ucp={"version": "2026-08-25"},
            id="prof_001",
            name="Test Merchant",
            url="https://merchant.example.com",
            keys=[key],
        )
        self.assertEqual(prof.id, "prof_001")
        self.assertEqual(len(prof.keys), 1)

    def test_error_response_models(self) -> None:
        from ucp_sdk.models.schemas.common.types.error_response import (
            ErrorResponse,
        )
        from ucp_sdk.models.schemas.common.types.message import MessageError

        msg = MessageError(
            content="Malformed payload",
            severity="unrecoverable",
            code="invalid_request",
        )
        resp = ErrorResponse(
            ucp={"version": "2026-08-25"},
            messages=[msg],
        )
        self.assertEqual(len(resp.messages), 1)
        self.assertEqual(resp.messages[0].code, "invalid_request")
        self.assertEqual(resp.messages[0].content, "Malformed payload")


if __name__ == "__main__":
    unittest.main()
