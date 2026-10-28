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
from typing import Any
import unittest

from pydantic import TypeAdapter, ValidationError
import ucp_sdk.models as models_module
from ucp_sdk.models import (
    Ap2WithCheckoutMandateCompleteRequest,
    Checkout,
    CheckoutCompleteRequest,
    CheckoutCreateRequest,
    FulfillmentDestination,
    FulfillmentDestinationBase,
    FulfillmentMethod,
    FulfillmentMethodBase,
    FulfillmentMethodCreateRequest,
    FulfillmentMethodCreateRequestBase,
    FulfillmentMethodUpdateRequest,
    FulfillmentMethodUpdateRequestBase,
    LocationDestination,
    Media,
    MediaBase,
    MediaBasePreview,
    MediaExternalVideo,
    MediaExternalVideoPreview,
    MediaImage,
    MediaImagePreview,
    MediaModel3d,
    MediaModel3dPreview,
    MediaSource,
    MediaVideo,
    MediaVideoPreview,
    Oauth2Provider,
    PickupMethod,
    PickupMethodCreateRequest,
    PickupMethodUpdateRequest,
    Product,
    Provider,
    ProviderBase,
    ShippingDestination,
    ShippingMethod,
    ShippingMethodCreateRequest,
    ShippingMethodUpdateRequest,
    Variant,
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
    """Regression tests for CheckoutCreateRequest and CheckoutCompleteRequest."""

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
        assert isinstance(req.ap2, Ap2WithCheckoutMandateCompleteRequest)
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
        assert req.buyer is not None
        self.assertEqual(str(req.buyer.email), "buyer@example.com")
        self.assertTrue(
            CheckoutCompleteRequest.model_fields["cart_id"].deprecated
        )
        with self.assertWarns(DeprecationWarning):
            self.assertIsNone(req.cart_id)

    def test_complete_request_explicit_cart_id_and_extra_fields(self) -> None:
        req = CheckoutCompleteRequest.model_validate(
            {
                "payment": self.PAYMENT_PAYLOAD,
                "cart_id": "cart_123",
                "com.example.trace_id": "trace_abc",
            }
        )
        with self.assertWarns(DeprecationWarning):
            self.assertEqual(req.cart_id, "cart_123")
        dumped = req.model_dump(exclude_none=True)
        self.assertEqual(dumped["cart_id"], "cart_123")
        self.assertEqual(dumped["com.example.trace_id"], "trace_abc")

        with self.assertRaises(ValidationError):
            CheckoutCompleteRequest.model_validate(
                {"payment": self.PAYMENT_PAYLOAD, "cart_id": 12345}
            )

    def test_create_request_validation_and_non_deprecated_cart_id(self) -> None:
        with self.assertRaises(ValidationError) as ctx:
            CheckoutCreateRequest.model_validate({})
        error_locs = [err["loc"] for err in ctx.exception.errors()]
        self.assertIn(("line_items",), error_locs)

        for bad_line_item in (
            {"item": {"id": "sku_1"}, "quantity": 0},
            {"quantity": 1},
        ):
            with self.assertRaises(ValidationError):
                CheckoutCreateRequest.model_validate(
                    {"line_items": [bad_line_item]}
                )

        self.assertFalse(
            bool(CheckoutCreateRequest.model_fields["cart_id"].deprecated)
        )
        req = CheckoutCreateRequest.model_validate(
            {
                "line_items": [{"item": {"id": "sku_1"}, "quantity": 2}],
                "cart_id": "cart_live_1",
                "com.example.channel": "mobile_web",
            }
        )
        self.assertEqual(req.cart_id, "cart_live_1")
        self.assertEqual(len(req.line_items), 1)
        self.assertEqual(req.line_items[0].item.id, "sku_1")
        self.assertEqual(req.line_items[0].quantity, 2)
        dumped = req.model_dump(exclude_none=True)
        self.assertEqual(dumped["com.example.channel"], "mobile_web")


class MediaModelTest(unittest.TestCase):
    """Tests for consolidated Media open discriminated union and variants."""

    def setUp(self) -> None:
        self.media_adapter: TypeAdapter[Any] = TypeAdapter(Media)

    def test_media_exports_and_identity_parity(self) -> None:
        expected_symbols = (
            "Media",
            "MediaImage",
            "MediaVideo",
            "MediaExternalVideo",
            "MediaModel3d",
            "MediaBase",
            "MediaSource",
            "MediaBasePreview",
            "MediaImagePreview",
            "MediaVideoPreview",
            "MediaExternalVideoPreview",
            "MediaModel3dPreview",
        )
        for name in expected_symbols:
            self.assertIn(name, models_module.__all__)
            self.assertIn(name, schemas_module.__all__)
            self.assertIs(
                getattr(models_module, name), getattr(schemas_module, name)
            )

    def test_known_media_variants_validate_and_round_trip(self) -> None:
        cases: list[tuple[dict[str, Any], type[Any], type[Any]]] = [
            (
                {
                    "type": "image",
                    "url": "https://example.com/img.webp",
                    "name": "Front view",
                    "alt_text": "Red sneaker front view",
                    "width": 1200,
                    "height": 800,
                    "preview": {
                        "url": "https://example.com/img-thumb.webp",
                        "width": 300,
                        "height": 200,
                    },
                    "sources": [
                        {
                            "url": "https://example.com/img.avif",
                            "mime_type": "image/avif",
                            "format": "avif",
                            "width": 1200,
                            "height": 800,
                            "filesize": 45000,
                        }
                    ],
                },
                MediaImage,
                MediaImagePreview,
            ),
            (
                {
                    "type": "video",
                    "url": "https://example.com/clip.mp4",
                    "duration": 15000,
                    "width": 1920,
                    "height": 1080,
                    "preview": {
                        "url": "https://example.com/clip-poster.jpg",
                        "width": 640,
                        "height": 360,
                    },
                    "sources": [
                        {
                            "url": "https://example.com/clip.webm",
                            "mime_type": "video/webm",
                            "format": "webm",
                            "filesize": 1024000,
                        }
                    ],
                },
                MediaVideo,
                MediaVideoPreview,
            ),
            (
                {
                    "type": "external_video",
                    "url": "https://video.example.com/watch?v=abc",
                    "name": "Product walkthrough",
                    "duration": 90000,
                    "preview": {
                        "url": "https://example.com/ext-poster.jpg",
                    },
                },
                MediaExternalVideo,
                MediaExternalVideoPreview,
            ),
            (
                {
                    "type": "model_3d",
                    "url": "https://example.com/shoe.glb",
                    "alt_text": "3D model of sneaker",
                    "preview": {
                        "url": "https://example.com/shoe-preview.png",
                        "width": 512,
                        "height": 512,
                    },
                    "sources": [
                        {
                            "url": "https://example.com/shoe.usdz",
                            "mime_type": "model/vnd.usdz+zip",
                            "format": "usdz",
                            "filesize": 2048000,
                        }
                    ],
                },
                MediaModel3d,
                MediaModel3dPreview,
            ),
        ]

        for payload, expected_cls, expected_preview_cls in cases:
            item = self.media_adapter.validate_python(payload)
            self.assertIsInstance(item, expected_cls)
            self.assertIsInstance(item.preview, expected_preview_cls)
            if "sources" in payload:
                self.assertIsNotNone(item.sources)
                assert item.sources is not None
                self.assertIsInstance(item.sources[0], MediaSource)
            dumped = item.model_dump(exclude_none=True, mode="json")
            self.assertEqual(dumped, payload)

    def test_unknown_media_type_validates_to_media_base_and_preserves_extras(
        self,
    ) -> None:
        payload = {
            "type": "com.example.hologram",
            "url": "https://example.com/holo.bin",
            "name": "Hologram view",
            "preview": {
                "url": "https://example.com/holo-preview.png",
                "width": 256,
                "height": 256,
            },
            "com.example.fov_degrees": 120,
        }
        item = self.media_adapter.validate_python(payload)
        self.assertIsInstance(item, MediaBase)
        assert isinstance(item, MediaBase)
        self.assertIsInstance(item.preview, MediaBasePreview)
        self.assertEqual(item.type, "com.example.hologram")
        dumped = item.model_dump(exclude_none=True, mode="json")
        self.assertEqual(dumped, payload)

    def test_media_embedded_in_product_and_variant(self) -> None:
        variant_payload = {
            "id": "var_1",
            "title": "Size 9",
            "description": {"plain": "Size 9 variant"},
            "price": {"amount": 9900, "currency": "USD"},
            "media": [
                {
                    "type": "image",
                    "url": "https://example.com/var1.jpg",
                },
                {
                    "type": "com.example.ar_spin",
                    "url": "https://example.com/var1.spin",
                    "frames": 36,
                },
            ],
        }
        product_payload = {
            "id": "prod_1",
            "title": "Runner",
            "description": {"plain": "Lightweight runner"},
            "price_range": {
                "min": {"amount": 9900, "currency": "USD"},
                "max": {"amount": 9900, "currency": "USD"},
            },
            "variants": [variant_payload],
            "media": [
                {
                    "type": "video",
                    "url": "https://example.com/runner.mp4",
                    "duration": 12000,
                },
                {
                    "type": "model_3d",
                    "url": "https://example.com/runner.glb",
                },
            ],
        }
        product = Product.model_validate(product_payload)
        self.assertIsNotNone(product.media)
        assert product.media is not None
        self.assertIsInstance(product.media[0], MediaVideo)
        self.assertIsInstance(product.media[1], MediaModel3d)

        variant = product.variants[0]
        self.assertIsInstance(variant, Variant)
        self.assertIsNotNone(variant.media)
        assert variant.media is not None
        self.assertIsInstance(variant.media[0], MediaImage)
        self.assertIsInstance(variant.media[1], MediaBase)
        dumped = product.model_dump(exclude_none=True, mode="json")
        self.assertEqual(
            dumped["variants"][0]["media"][1]["frames"],
            36,
        )

    def test_known_media_type_with_invalid_fields_raises_and_never_falls_back(
        self,
    ) -> None:
        invalid_payloads: list[dict[str, Any]] = [
            {"url": "https://example.com/missing-type.jpg"},
            {"type": "image"},
            {"type": "image", "url": "not-a-valid-url"},
            {
                "type": "image",
                "url": "https://example.com/img.jpg",
                "width": 0,
            },
            {
                "type": "video",
                "url": "https://example.com/vid.mp4",
                "height": 0,
            },
            {
                "type": "external_video",
                "url": "https://example.com/vid",
                "duration": -1,
            },
            {
                "type": "model_3d",
                "url": "https://example.com/m.glb",
                "preview": {},
            },
            {
                "type": "image",
                "url": "https://example.com/img.jpg",
                "sources": [
                    {"url": "https://example.com/s.jpg", "filesize": 0}
                ],
            },
        ]
        for bad in invalid_payloads:
            with self.assertRaises(ValidationError):
                self.media_adapter.validate_python(bad)


class OpenDiscriminatedUnionModelTest(unittest.TestCase):
    """Tests for open discriminated union validation and round-trip behavior."""

    def setUp(self) -> None:
        self.destination_adapter: TypeAdapter[Any] = TypeAdapter(
            FulfillmentDestination
        )
        self.method_adapter: TypeAdapter[Any] = TypeAdapter(FulfillmentMethod)
        self.provider_adapter: TypeAdapter[Any] = TypeAdapter(Provider)
        self.method_create_adapter: TypeAdapter[Any] = TypeAdapter(
            FulfillmentMethodCreateRequest
        )
        self.method_update_adapter: TypeAdapter[Any] = TypeAdapter(
            FulfillmentMethodUpdateRequest
        )

    def test_reexports_match_schemas_module(self) -> None:
        self.assertIs(Checkout, schemas_module.Checkout)
        self.assertIs(
            FulfillmentDestination, schemas_module.FulfillmentDestination
        )
        self.assertIs(FulfillmentMethod, schemas_module.FulfillmentMethod)
        self.assertIs(Provider, schemas_module.Provider)
        self.assertIs(
            FulfillmentMethodCreateRequest,
            schemas_module.FulfillmentMethodCreateRequest,
        )
        self.assertIs(
            FulfillmentMethodUpdateRequest,
            schemas_module.FulfillmentMethodUpdateRequest,
        )

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
        assert isinstance(dest, ShippingDestination)
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
        assert isinstance(dest, LocationDestination)
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
        assert isinstance(dest, FulfillmentDestinationBase)
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
        assert isinstance(shipping, ShippingMethod)
        assert shipping.destinations is not None
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
        assert isinstance(pickup, PickupMethod)
        assert pickup.destinations is not None
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
        assert isinstance(method, FulfillmentMethodBase)
        self.assertEqual(method.type, "drone_delivery")
        assert method.destinations is not None
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
        assert checkout.fulfillment is not None
        assert checkout.fulfillment.methods is not None
        methods = checkout.fulfillment.methods
        self.assertEqual(len(methods), 2)
        self.assertIsInstance(methods[0], ShippingMethod)
        assert isinstance(methods[0], ShippingMethod)
        assert methods[0].destinations is not None
        self.assertIsInstance(methods[0].destinations[0], ShippingDestination)
        self.assertIsInstance(methods[1], FulfillmentMethodBase)
        assert isinstance(methods[1], FulfillmentMethodBase)
        assert methods[1].destinations is not None
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

    def test_missing_or_non_string_discriminator_raises_validation_error(
        self,
    ) -> None:
        for bad_dest in (
            {"id": "dest_missing_type", "postal_code": "94043"},
            {"id": "dest_int_type", "type": 123},
            {"id": "dest_null_type", "type": None},
        ):
            with self.assertRaises(ValidationError):
                self.destination_adapter.validate_python(bad_dest)
        with self.assertRaises(ValidationError):
            self.method_adapter.validate_python(
                {"id": "m_missing_type", "line_item_ids": ["li_1"]}
            )
        with self.assertRaises(ValidationError):
            ShippingDestination.model_validate(
                {
                    "type": "shipping_address",
                    "id": "dest_bad",
                    "postal_code": 12345,
                }
            )

    def test_provider_open_discriminated_union(self) -> None:
        oauth = self.provider_adapter.validate_python(
            {
                "type": "oauth2",
                "auth_url": "https://auth.example.com/authorize",
                "required_claims": ["email"],
            }
        )
        self.assertIsInstance(oauth, Oauth2Provider)
        assert isinstance(oauth, Oauth2Provider)
        self.assertEqual(
            str(oauth.auth_url), "https://auth.example.com/authorize"
        )

        custom = self.provider_adapter.validate_python(
            {
                "type": "com.example.passkey",
                "rp_id": "example.com",
            }
        )
        self.assertIsInstance(custom, ProviderBase)
        assert isinstance(custom, ProviderBase)
        self.assertEqual(custom.type, "com.example.passkey")
        self.assertEqual(
            custom.model_dump(exclude_none=True)["rp_id"], "example.com"
        )

        with self.assertRaises(ValidationError):
            Oauth2Provider.model_validate({"type": "oauth2"})
        with self.assertRaises(ValidationError):
            self.provider_adapter.validate_python({})
        with self.assertRaises(ValidationError):
            self.provider_adapter.validate_python({"type": 123})

    def test_fulfillment_method_create_and_update_request_open_unions(
        self,
    ) -> None:
        ship_create = self.method_create_adapter.validate_python(
            {
                "type": "shipping",
                "destinations": [{"postal_code": "94043"}],
            }
        )
        self.assertIsInstance(ship_create, ShippingMethodCreateRequest)

        pick_create = self.method_create_adapter.validate_python(
            {"type": "pickup", "selected_destination_id": "loc_1"}
        )
        self.assertIsInstance(pick_create, PickupMethodCreateRequest)

        custom_create = self.method_create_adapter.validate_python(
            {"type": "drone_delivery", "landing_pad": "pad_7"}
        )
        self.assertIsInstance(custom_create, FulfillmentMethodCreateRequestBase)
        self.assertEqual(
            custom_create.model_dump(exclude_none=True)["landing_pad"],
            "pad_7",
        )

        with self.assertRaises(ValidationError):
            ShippingMethodCreateRequest.model_validate(
                {"type": "shipping", "destinations": [{"postal_code": 123}]}
            )

        ship_update = self.method_update_adapter.validate_python(
            {"type": "shipping", "line_item_ids": ["li_1"]}
        )
        self.assertIsInstance(ship_update, ShippingMethodUpdateRequest)

        pick_update = self.method_update_adapter.validate_python(
            {"type": "pickup", "line_item_ids": ["li_1"]}
        )
        self.assertIsInstance(pick_update, PickupMethodUpdateRequest)

        custom_update = self.method_update_adapter.validate_python(
            {
                "type": "drone_delivery",
                "line_item_ids": ["li_1"],
                "max_altitude_m": 120,
            }
        )
        self.assertIsInstance(custom_update, FulfillmentMethodUpdateRequestBase)
        self.assertEqual(
            custom_update.model_dump(exclude_none=True)["max_altitude_m"], 120
        )

        with self.assertRaises(ValidationError):
            ShippingMethodUpdateRequest.model_validate({"type": "shipping"})


class IdentityLinkingRoleSchemaTest(unittest.TestCase):
    """Tests for IdentityLinkingBusinessSchema and IdentityLinkingPlatformSchema."""

    BASE = {
        "version": "2026-08-25",
        "schema": "https://ucp.dev/2026-08-25/schemas/common/identity_linking",
    }

    def test_platform_and_business_role_schemas(self) -> None:
        plat_cls = models_module.IdentityLinkingPlatformSchema
        biz_cls = models_module.IdentityLinkingBusinessSchema
        spec_url = "https://ucp.dev/specification/common/identity-linking"
        plat = plat_cls.model_validate({**self.BASE, "spec": spec_url})
        self.assertEqual(str(plat.version), "2026-08-25")

        scope = "dev.ucp.shopping.order:read"
        biz = biz_cls.model_validate(
            {**self.BASE, "config": {"scopes": {scope: {}}}}
        )
        self.assertEqual(list(biz.config.scopes), [scope])
        self.assertIsNone(biz.config.scopes[scope].description)
        for bad in (
            self.BASE,
            {**self.BASE, "config": {}},
            {**self.BASE, "config": {"scopes": {"BAD": {}}}},
        ):
            with self.assertRaises(ValidationError):
                biz_cls.model_validate(bad)


class PureDtoStructureAndOpennessTest(unittest.TestCase):
    """Tests for semantic model naming, __all__ hygiene, LocationFilter, and DTO openness."""

    def test_semantic_names_present_and_synthetic_wrapper_absent(self) -> None:
        forbidden = (
            *("UCPSchemaTypes", "UcpSchemaTypes", "AnyUrl", "AwareDatetime"),
            *("BaseModel", "ConfigDict", "Field", "RootModel"),
            *("date", "timedelta", "TypeAliasType"),
        )
        for sym in forbidden:
            self.assertNotIn(sym, schemas_module.__all__)
            self.assertNotIn(sym, models_module.__all__)
            self.assertFalse(hasattr(models_module, sym), sym)
        expected = (
            *("TotalsItem", "ValueConstraintEnum", "ValueConstraintConst"),
            *("ServiceBusinessSchemaRest", "ServiceBusinessSchemaMcp"),
            *("ServiceBusinessSchemaA2a", "ServiceBusinessSchemaEmbedded"),
            *("LocationServesAddressCountry", "LocationServesAddressRegion"),
            *("LocationServesAddressPostalCode", "UnitPriceMeasure"),
            "UnitPriceReference",
        )
        for name in expected:
            self.assertIn(name, schemas_module.__all__)
            self.assertTrue(hasattr(models_module, name), name)

    def test_location_filter_open_at_utc_offset_and_naive_datetime(
        self,
    ) -> None:
        search_cls = models_module.LocationSearchRequest
        lookup_cls = models_module.LocationLookupRequest
        for model, base in ((search_cls, {}), (lookup_cls, {"ids": ["loc_1"]})):
            for open_at, offset in (
                ("2026-05-18T17:00:00Z", 0),
                ("2026-05-18T17:00:00+05:30", 19800),
            ):
                hours = {"open_at": open_at}
                req = model.model_validate(
                    {**base, "filters": {"hours": hours}}
                )
                assert req.filters is not None and req.filters.hours is not None
                assert req.filters.hours.open_at is not None
                utcoffset = req.filters.hours.open_at.utcoffset()
                assert utcoffset is not None
                self.assertEqual(utcoffset.total_seconds(), offset)
            naive = {"open_at": "2026-05-18T17:00:00"}
            with self.assertRaises(ValidationError):
                model.model_validate({**base, "filters": {"hours": naive}})

    def test_dto_openness_preserves_extra_fields(self) -> None:
        msg = {
            "type": "error",
            "code": "invalid",
            "content": "bad",
            "severity": "recoverable",
        }
        err = models_module.ErrorResponse.model_validate(
            {
                "ucp": {"version": "2026-08-25", "status": "error"},
                "messages": [msg],
                "com.example.trace": "t_1",
            }
        )
        self.assertEqual(
            err.model_dump(exclude_none=True)["com.example.trace"], "t_1"
        )

        rc = models_module.RequestConstraints.model_validate(
            {"path": "$.buyer", "com.example.meta": 42}
        )
        self.assertEqual(
            rc.model_dump(exclude_none=True)["com.example.meta"], 42
        )

        vc_adapter: TypeAdapter[Any] = TypeAdapter(
            models_module.ValueConstraint
        )
        vc_enum = vc_adapter.validate_python(
            {"enum": ["a", "b"], "com.example.extra": True}
        )
        self.assertIsInstance(vc_enum, models_module.ValueConstraintEnum)
        self.assertIs(
            vc_enum.model_dump(exclude_none=True)["com.example.extra"], True
        )

        vc_const = vc_adapter.validate_python(
            {"const": "a", "com.example.extra": "ok"}
        )
        self.assertIsInstance(vc_const, models_module.ValueConstraintConst)
        self.assertEqual(
            vc_const.model_dump(exclude_none=True)["com.example.extra"], "ok"
        )


if __name__ == "__main__":
    unittest.main()
