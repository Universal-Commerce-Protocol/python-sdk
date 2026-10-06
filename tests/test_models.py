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

import unittest

from pydantic import TypeAdapter, ValidationError
from ucp_sdk.models import (
    Checkout,
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
