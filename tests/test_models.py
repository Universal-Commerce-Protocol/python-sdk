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

"""Unit tests for open discriminated unions and model exports in ucp_sdk.models."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import unittest

from pydantic import TypeAdapter, ValidationError
import ucp_sdk.models as models_module
from ucp_sdk.models import (
    Ap2WithCheckoutMandateCompleteRequest,
    Checkout,
    CheckoutCompleteRequest,
    FulfillmentDestination,
    FulfillmentDestinationBase,
    FulfillmentMethod,
    FulfillmentMethodBase,
    LocationDestination,
    PickupMethod,
    ShippingDestination,
    ShippingMethod,
)
import ucp_sdk.models.schemas as schemas_module


class PackageLazyReexportTest(unittest.TestCase):
    """Tests for PEP 562 lazy re-exports on ucp_sdk.models and schemas."""

    def _fresh_python_env(self) -> dict[str, str]:
        assert models_module.__file__ is not None
        src_root = str(Path(models_module.__file__).resolve().parents[2])
        env = dict(os.environ)
        existing = env.get("PYTHONPATH")
        env["PYTHONPATH"] = (
            f"{src_root}{os.pathsep}{existing}" if existing else src_root
        )
        return env

    def test_public_all_matches_between_models_and_schemas(self) -> None:
        self.assertTrue(hasattr(models_module, "__all__"))
        self.assertEqual(models_module.__all__, schemas_module.__all__)
        self.assertGreater(len(models_module.__all__), 0)
        for symbol in models_module.__all__:
            self.assertFalse(symbol.startswith("_"), symbol)

    def test_package_imports_are_lazy_in_fresh_interpreter(self) -> None:
        script = (
            "import pickle, sys\n"
            "import ucp_sdk\n"
            "assert 'ucp_sdk.models' not in sys.modules\n"
            "import ucp_sdk.models as models\n"
            "import ucp_sdk.models.schemas as schemas\n"
            "assert 'ucp_sdk.models.schemas._generated' not in sys.modules\n"
            "assert 'Checkout' in dir(models)\n"
            "assert 'Checkout' in dir(schemas)\n"
            "assert 'Checkout' not in models.__dict__\n"
            "assert 'ucp_sdk.models.schemas._generated' not in sys.modules\n"
            "cls = models.Checkout\n"
            "assert 'ucp_sdk.models.schemas._generated' in sys.modules\n"
            "assert models.__dict__['Checkout'] is cls\n"
            "assert schemas.__dict__['Checkout'] is cls\n"
            "assert cls is schemas.Checkout\n"
            "ns: dict[str, object] = {}\n"
            "exec('from ucp_sdk.models import *', ns)\n"
            "exported = {k for k in ns if k != '__builtins__'}\n"
            "assert exported == set(models.__all__)\n"
            "req = models.CheckoutCompleteRequest.model_validate(\n"
            "    {'payment': {'instruments': [{'id': 'pi_1', 'handler_id': 'h1', 'type': 'card'}]}}\n"
            ")\n"
            "assert pickle.loads(pickle.dumps(req)) == req\n"
        )
        subprocess.run(
            [sys.executable, "-c", script],
            env=self._fresh_python_env(),
            check=True,
        )

    def test_unknown_and_private_attributes_raise_attribute_error(self) -> None:
        for attr in ("NonExistentUcpModel", "_enforce_contains_totals"):
            with self.assertRaises(AttributeError):
                getattr(models_module, attr)
            with self.assertRaises(AttributeError):
                getattr(schemas_module, attr)


class CheckoutRequestModelTest(unittest.TestCase):
    """Regression tests for CheckoutCompleteRequest projection rules."""

    PAYMENT_PAYLOAD = {
        "instruments": [
            {
                "id": "pi_1",
                "handler_id": "handler_card",
                "type": "card",
            }
        ]
    }

    def test_complete_request_requires_payment_and_allows_omitting_ap2_and_buyer(
        self,
    ) -> None:
        with self.assertRaises(ValidationError) as ctx:
            CheckoutCompleteRequest.model_validate({})
        error_locs = [err["loc"] for err in ctx.exception.errors()]
        self.assertIn(("payment",), error_locs)

        req = CheckoutCompleteRequest.model_validate(
            {"payment": self.PAYMENT_PAYLOAD}
        )
        self.assertIsNone(req.ap2)
        self.assertIsNone(req.buyer)

    def test_complete_request_requires_valid_checkout_mandate_when_ap2_present(
        self,
    ) -> None:
        for invalid_ap2 in ({}, {"checkout_mandate": "not valid jwt!"}):
            with self.assertRaises(ValidationError) as ctx:
                CheckoutCompleteRequest.model_validate(
                    {"payment": self.PAYMENT_PAYLOAD, "ap2": invalid_ap2}
                )
            error_locs = [err["loc"] for err in ctx.exception.errors()]
            self.assertIn(("ap2", "checkout_mandate"), error_locs)

        mandate = "eyJhbGciOiJFUzI1NiJ9.eyJjaGVja291dCI6MX0.c2ln~kb1"
        req = CheckoutCompleteRequest.model_validate(
            {
                "payment": self.PAYMENT_PAYLOAD,
                "ap2": {"checkout_mandate": mandate},
            }
        )
        self.assertIsInstance(req.ap2, Ap2WithCheckoutMandateCompleteRequest)
        self.assertEqual(req.ap2.checkout_mandate, mandate)

    def test_complete_request_buyer_optional_and_cart_id_deprecated(
        self,
    ) -> None:
        req = CheckoutCompleteRequest.model_validate(
            {
                "payment": self.PAYMENT_PAYLOAD,
                "buyer": {"email": "buyer@example.com"},
            }
        )
        self.assertIsNotNone(req.buyer)
        self.assertEqual(req.buyer.email, "buyer@example.com")
        self.assertTrue(
            CheckoutCompleteRequest.model_fields["cart_id"].deprecated
        )
        with self.assertWarns(DeprecationWarning):
            self.assertIsNone(req.cart_id)


class OpenDiscriminatedUnionModelTest(unittest.TestCase):
    """Tests for open discriminated union validation and round-trip behavior."""

    def setUp(self) -> None:
        self.destination_adapter = TypeAdapter(FulfillmentDestination)
        self.method_adapter = TypeAdapter(FulfillmentMethod)

    def test_reexports_match_schemas_module(self) -> None:
        self.assertIs(Checkout, schemas_module.Checkout)
        self.assertIs(
            FulfillmentDestination, schemas_module.FulfillmentDestination
        )
        self.assertIs(FulfillmentMethod, schemas_module.FulfillmentMethod)

    def test_known_shipping_destination_validates_to_shipping_destination(
        self,
    ) -> None:
        payload = {
            "type": "shipping_address",
            "id": "dest_ship_1",
            "postal_code": "94043",
            "address_country": "US",
        }
        dest = self.destination_adapter.validate_python(payload)
        self.assertIsInstance(dest, ShippingDestination)
        self.assertEqual(dest.type, "shipping_address")
        self.assertEqual(dest.id, "dest_ship_1")
        self.assertEqual(dest.postal_code, "94043")

    def test_known_location_destination_validates_to_location_destination(
        self,
    ) -> None:
        payload = {
            "type": "business_location",
            "id": "dest_loc_1",
            "name": "Downtown Store",
        }
        dest = self.destination_adapter.validate_python(payload)
        self.assertIsInstance(dest, LocationDestination)
        self.assertEqual(dest.type, "business_location")
        self.assertEqual(dest.id, "dest_loc_1")
        self.assertEqual(dest.name, "Downtown Store")

    def test_unknown_destination_validates_to_base_and_preserves_extra_fields(
        self,
    ) -> None:
        payload = {
            "type": "locker",
            "id": "lk_1",
            "name": "Locker bank 7",
        }
        dest = self.destination_adapter.validate_python(payload)
        self.assertIsInstance(dest, FulfillmentDestinationBase)
        self.assertEqual(dest.type, "locker")
        self.assertEqual(dest.id, "lk_1")
        dumped = dest.model_dump(exclude_none=True)
        self.assertEqual(dumped, payload)

    def test_known_fulfillment_methods_validate_to_concrete_variants(
        self,
    ) -> None:
        shipping = self.method_adapter.validate_python(
            {
                "id": "m_ship",
                "type": "shipping",
                "line_item_ids": ["li_1"],
                "destinations": [
                    {
                        "type": "shipping_address",
                        "id": "dest_1",
                        "postal_code": "94043",
                    }
                ],
            }
        )
        self.assertIsInstance(shipping, ShippingMethod)
        self.assertIsInstance(shipping.destinations[0], ShippingDestination)

        pickup = self.method_adapter.validate_python(
            {
                "id": "m_pick",
                "type": "pickup",
                "line_item_ids": ["li_1"],
                "destinations": [
                    {
                        "type": "business_location",
                        "id": "loc_1",
                        "name": "Main St",
                    }
                ],
            }
        )
        self.assertIsInstance(pickup, PickupMethod)
        self.assertIsInstance(pickup.destinations[0], LocationDestination)

    def test_unknown_fulfillment_method_validates_to_base_and_preserves_extras(
        self,
    ) -> None:
        payload = {
            "type": "drone_delivery",
            "id": "m_1",
            "line_item_ids": ["li_1"],
            "max_weight_kg": 5,
            "destinations": [
                {"type": "locker", "id": "lk_1", "name": "Locker bank 7"}
            ],
        }
        method = self.method_adapter.validate_python(payload)
        self.assertIsInstance(method, FulfillmentMethodBase)
        self.assertEqual(method.type, "drone_delivery")
        self.assertIsInstance(
            method.destinations[0], FulfillmentDestinationBase
        )
        dumped = method.model_dump(exclude_none=True)
        self.assertEqual(dumped["max_weight_kg"], 5)
        self.assertEqual(dumped["destinations"][0]["name"], "Locker bank 7")

    def test_embedded_in_checkout_response_payload(self) -> None:
        checkout_payload = {
            "ucp": {
                "version": "2026-08-25",
                "payment_handlers": {},
            },
            "id": "chk_123",
            "status": "incomplete",
            "currency": "USD",
            "line_items": [
                {
                    "id": "li_1",
                    "item": {
                        "id": "sku_1",
                        "title": "Widget",
                        "price": 1500,
                    },
                    "quantity": 1,
                    "totals": [{"type": "total", "amount": 1500}],
                }
            ],
            "totals": [
                {"type": "subtotal", "amount": 1500},
                {"type": "total", "amount": 1500},
            ],
            "links": [
                {
                    "type": "privacy_policy",
                    "url": "https://example.com/privacy",
                }
            ],
            "fulfillment": {
                "methods": [
                    {
                        "id": "m_ship",
                        "type": "shipping",
                        "line_item_ids": ["li_1"],
                        "destinations": [
                            {
                                "type": "shipping_address",
                                "id": "dest_1",
                                "postal_code": "94043",
                            }
                        ],
                    },
                    {
                        "id": "m_drone",
                        "type": "drone_delivery",
                        "line_item_ids": ["li_1"],
                        "max_weight_kg": 5,
                        "destinations": [
                            {
                                "type": "locker",
                                "id": "lk_1",
                                "name": "Locker bank 7",
                            }
                        ],
                    },
                ]
            },
        }
        checkout = Checkout.model_validate(checkout_payload)
        methods = checkout.fulfillment.methods
        self.assertEqual(len(methods), 2)
        self.assertIsInstance(methods[0], ShippingMethod)
        self.assertIsInstance(methods[0].destinations[0], ShippingDestination)
        self.assertIsInstance(methods[1], FulfillmentMethodBase)
        self.assertIsInstance(
            methods[1].destinations[0], FulfillmentDestinationBase
        )
        dumped = checkout.model_dump(exclude_none=True)
        self.assertEqual(
            dumped["fulfillment"]["methods"][1]["max_weight_kg"], 5
        )
        self.assertEqual(
            dumped["fulfillment"]["methods"][1]["destinations"][0]["name"],
            "Locker bank 7",
        )

    def test_missing_discriminator_raises_validation_error(self) -> None:
        with self.assertRaises(ValidationError):
            self.destination_adapter.validate_python(
                {"id": "dest_missing_type", "postal_code": "94043"}
            )
        with self.assertRaises(ValidationError):
            self.method_adapter.validate_python(
                {"id": "m_missing_type", "line_item_ids": ["li_1"]}
            )

    def test_known_discriminator_with_malformed_field_raises_validation_error(
        self,
    ) -> None:
        # postal_code must be a string; known discriminator "shipping_address"
        # must NOT silently fall back to FulfillmentDestinationBase.
        with self.assertRaises(ValidationError):
            self.destination_adapter.validate_python(
                {
                    "type": "shipping_address",
                    "id": "dest_bad",
                    "postal_code": 12345,
                }
            )


if __name__ == "__main__":
    unittest.main()
