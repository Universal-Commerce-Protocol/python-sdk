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

import copy
from pathlib import Path
import unittest

from pydantic import TypeAdapter, ValidationError

from preprocess_schemas import _create_single_variant
from ucp_sdk.models.schemas.shopping.types import (
    totals_create_request,
    totals_update_request,
)


class CreateSingleVariantTest(unittest.TestCase):
    def test_array_root_keeps_its_shape_and_filters_nested_properties(self):
        schema = {
            "$id": "https://ucp.dev/schemas/totals.json",
            "title": "Totals",
            "type": "array",
            "items": {
                "allOf": [
                    {"$ref": "total.json"},
                    {
                        "type": "object",
                        "properties": {
                            "lines": {
                                "type": "array",
                                "ucp_request": "omit",
                            },
                            "note": {
                                "type": "string",
                                "ucp_request": "optional",
                            },
                        },
                        "required": ["lines", "note"],
                    },
                ]
            },
        }
        original = copy.deepcopy(schema)

        variant = _create_single_variant(
            schema,
            "create",
            "totals",
            Path("schemas/totals.json"),
            {},
        )

        self.assertNotIn("properties", variant)
        self.assertNotIn("required", variant)
        nested_object = variant["items"]["allOf"][1]
        self.assertEqual(
            nested_object["properties"], {"note": {"type": "string"}}
        )
        self.assertEqual(nested_object["required"], [])
        self.assertEqual(schema, original)


class GeneratedTotalsRequestTest(unittest.TestCase):
    def test_totals_request_variants_only_accept_lists(self):
        cases = [
            (
                totals_create_request,
                totals_create_request.TotalsCreateRequest,
                "TotalsCreateRequest1",
                "TotalsCreateRequestItem",
            ),
            (
                totals_update_request,
                totals_update_request.TotalsUpdateRequest,
                "TotalsUpdateRequest1",
                "TotalsUpdateRequestItem",
            ),
        ]

        for module, alias, empty_model, item_model in cases:
            with self.subTest(alias=alias):
                self.assertFalse(hasattr(module, empty_model))
                self.assertFalse(hasattr(module, item_model))

                adapter = TypeAdapter(alias)
                with self.assertRaises(ValidationError):
                    adapter.validate_python({})

                totals = adapter.validate_python(
                    [
                        {"type": "subtotal", "amount": 100},
                        {"type": "total", "amount": 100},
                    ]
                )
                self.assertNotIn("lines", type(totals[0]).model_fields)


if __name__ == "__main__":
    unittest.main()
