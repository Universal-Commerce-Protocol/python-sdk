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

"""Tests for the schema preprocessing pipeline."""

import contextlib
import copy
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import postprocess_models
import preprocess_schemas

try:
    from pydantic import TypeAdapter, ValidationError

    from ucp_sdk.models.schemas.shopping.types.description import Description
    from ucp_sdk.models.schemas.shopping.types.total import Total
    from ucp_sdk.models.schemas.shopping.types.totals import Totals
    from ucp_sdk.models.schemas.shopping.types.totals_create_request import (
        TotalsCreateRequest,
    )
    from ucp_sdk.models.schemas.shopping.types.totals_update_request import (
        TotalsUpdateRequest,
    )

    HAVE_SDK = True
except ImportError:  # pragma: no cover
    HAVE_SDK = False


class SchemaNormalizationTest(unittest.TestCase):
    """Tests schema flattening and reference normalization."""

    def test_resolve_local_ref_supports_objects_and_arrays(self) -> None:
        """Local JSON pointers resolve object keys and array indexes."""
        schema = {"$defs": {"choices": [{"const": "first"}]}}

        resolved = preprocess_schemas.resolve_local_ref(
            "#/$defs/choices/0", schema
        )

        self.assertEqual(resolved, {"const": "first"})
        self.assertIsNone(
            preprocess_schemas.resolve_local_ref("#/$defs/choices/1", schema)
        )
        self.assertIsNone(
            preprocess_schemas.resolve_local_ref("other.json", schema)
        )

    def test_preprocess_flattens_and_distributes_properties(self) -> None:
        """Flattened base fields are distributed to polymorphic branches."""
        schema = {
            "$defs": {
                "base": {
                    "type": "object",
                    "properties": {"id": {"type": "string"}},
                    "required": ["id"],
                }
            },
            "allOf": [{"$ref": "#/$defs/base"}],
            "oneOf": [
                {
                    "properties": {"kind": {"const": "physical"}},
                    "required": ["kind"],
                }
            ],
        }

        preprocess_schemas.preprocess_full_schema(schema)

        self.assertNotIn("allOf", schema)
        self.assertEqual(schema["required"], ["id"])
        branch = schema["oneOf"][0]
        self.assertEqual(set(branch["properties"]), {"id", "kind"})
        self.assertEqual(set(branch["required"]), {"id", "kind"})
        self.assertEqual(branch["type"], "object")

    def test_preprocess_preserves_multiple_conditional_branches(self) -> None:
        """Each conditional allOf branch survives schema flattening."""
        negative = {
            "if": {"properties": {"type": {"const": "discount"}}},
            "then": {"properties": {"amount": {"exclusiveMaximum": 0}}},
        }
        non_negative = {
            "if": {"properties": {"type": {"const": "subtotal"}}},
            "then": {"properties": {"amount": {"minimum": 0}}},
        }
        schema = {
            "type": "object",
            "properties": {
                "type": {"type": "string"},
                "amount": {"type": "integer"},
            },
            "allOf": [negative, non_negative],
        }

        preprocess_schemas.preprocess_full_schema(schema)

        self.assertEqual(schema["allOf"], [negative, non_negative])

    def test_preprocess_inlines_entity_fields(self) -> None:
        """The shared entity definition is inlined without its metadata."""
        entity = {
            "title": "Entity",
            "description": "Shared entity fields.",
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
        }
        schema = {
            "allOf": [
                {"$ref": "ucp.json#/$defs/entity"},
                {
                    "type": "object",
                    "properties": {"value": {"type": "integer"}},
                    "required": ["value"],
                },
            ]
        }

        preprocess_schemas.preprocess_full_schema(schema, entity)

        self.assertEqual(set(schema["properties"]), {"id", "value"})
        self.assertEqual(set(schema["required"]), {"id", "value"})
        self.assertNotIn("title", schema)
        self.assertNotIn("description", schema)

    def test_flatten_dotted_defs_rewrites_local_refs(self) -> None:
        """Dotted definition names and local references stay aligned."""
        schema = {
            "$defs": {
                "checkout": {"type": "string"},
                "dev.ucp.shopping.checkout": {"type": "object"},
            },
            "properties": {
                "checkout": {"$ref": "#/$defs/dev.ucp.shopping.checkout"}
            },
        }

        rename_map = preprocess_schemas.flatten_dotted_defs(schema)

        self.assertEqual(
            rename_map,
            {"dev.ucp.shopping.checkout": "dev_ucp_shopping_checkout"},
        )
        self.assertIn("dev_ucp_shopping_checkout", schema["$defs"])
        self.assertEqual(
            schema["properties"]["checkout"]["$ref"],
            "#/$defs/dev_ucp_shopping_checkout",
        )

    def test_rewrite_external_defs_refs_uses_target_rename_map(self) -> None:
        """External references follow renames made in the target schema."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_path = root / "source.json"
            target_path = root / "target.json"
            schema = {
                "properties": {
                    "checkout": {
                        "$ref": ("target.json#/$defs/dev.ucp.shopping.checkout")
                    }
                }
            }

            preprocess_schemas._rewrite_external_defs_refs(
                source_path,
                schema,
                {
                    str(target_path.resolve()): {
                        "dev.ucp.shopping.checkout": "checkout"
                    }
                },
            )

        self.assertEqual(
            schema["properties"]["checkout"]["$ref"],
            "target.json#/$defs/checkout",
        )


class RequestMetadataTest(unittest.TestCase):
    """Tests operation-specific request metadata rules."""

    def test_get_required_ops_collects_all_declared_operations(self) -> None:
        """String and mapping markers contribute their operations."""
        schema = {
            "properties": {
                "id": {"ucp_request": "omit"},
                "payment": {
                    "ucp_request": {
                        "complete": "required",
                        "update": "optional",
                    }
                },
                "plain": {"type": "string"},
            }
        }

        self.assertEqual(
            preprocess_schemas.get_required_ops(schema),
            {"create", "update", "complete"},
        )

    def test_eval_prop_inclusion_applies_operation_overrides(self) -> None:
        """Operation markers override base required and inclusion rules."""
        cases = [
            ("default-required", {}, "create", ["field"], (True, True)),
            (
                "simple-optional",
                {"ucp_request": "optional"},
                "create",
                ["field"],
                (True, False),
            ),
            (
                "simple-omit",
                {"ucp_request": "omit"},
                "create",
                [],
                (False, False),
            ),
            (
                "operation-required",
                {"ucp_request": {"create": "required"}},
                "create",
                [],
                (True, True),
            ),
            (
                "operation-omit",
                {"ucp_request": {"create": "omit"}},
                "create",
                ["field"],
                (False, True),
            ),
            (
                "transition-omit",
                {
                    "ucp_request": {
                        "update": {
                            "transition": {"from": "required", "to": "omit"}
                        }
                    }
                },
                "update",
                ["field"],
                (False, True),
            ),
            (
                "undeclared-operation",
                {"ucp_request": {"update": "required"}},
                "create",
                [],
                (False, False),
            ),
        ]

        for name, data, operation, required, expected in cases:
            with self.subTest(name=name):
                actual = preprocess_schemas.eval_prop_inclusion(
                    "field", data, operation, required
                )
                self.assertEqual(actual, expected)


class VariantGenerationTest(unittest.TestCase):
    """Tests request variant construction and output."""

    def test_rewrite_external_ref_preserves_fragment(self) -> None:
        """External refs target variants without losing their fragments."""
        schema = {
            "properties": {
                "child": {"$ref": "nested/child.json#/$defs/item"},
                "local": {"$ref": "#/$defs/local"},
            }
        }
        file_path = Path("/schemas/parent.json")
        child_path = str((file_path.parent / "nested" / "child.json").resolve())

        preprocess_schemas.rewrite_refs_to_variants(
            schema,
            "create",
            file_path,
            {child_path: {"create"}},
        )

        self.assertEqual(
            schema["properties"]["child"]["$ref"],
            "nested/child_create_request.json#/$defs/item",
        )
        self.assertEqual(
            schema["properties"]["local"]["$ref"],
            "#/$defs/local",
        )

    def test_object_variant_filters_fields_and_rewrites_refs(self) -> None:
        """Object variants filter fields and target child variants."""
        schema = {
            "$id": "https://ucp.dev/schemas/checkout.json",
            "title": "Checkout",
            "type": "object",
            "properties": {
                "id": {
                    "type": "string",
                    "ucp_request": {
                        "create": "omit",
                        "update": "required",
                    },
                },
                "currency": {
                    "type": "string",
                    "ucp_request": "required",
                },
                "note": {
                    "type": "string",
                    "ucp_request": "optional",
                },
                "server_only": {
                    "type": "string",
                    "ucp_request": "omit",
                },
                "child": {
                    "$ref": "child.json",
                    "ucp_request": "required",
                },
            },
            "required": ["id", "note"],
        }
        original = copy.deepcopy(schema)
        file_path = Path("/schemas/checkout.json")
        child_path = str((file_path.parent / "child.json").resolve())

        variant = preprocess_schemas._create_single_variant(
            schema,
            "create",
            "checkout",
            file_path,
            {child_path: {"create"}},
        )

        self.assertEqual(schema, original)
        self.assertEqual(variant["title"], "Checkout Create Request")
        self.assertEqual(
            variant["$id"],
            "https://ucp.dev/schemas/checkout_create_request.json",
        )
        self.assertEqual(
            set(variant["properties"]), {"currency", "note", "child"}
        )
        self.assertEqual(set(variant["required"]), {"currency", "child"})
        self.assertEqual(
            variant["properties"]["child"]["$ref"],
            "child_create_request.json",
        )
        for data in variant["properties"].values():
            self.assertNotIn("ucp_request", data)

    def test_array_variant_preserves_root_and_filters_nested_objects(
        self,
    ) -> None:
        """Array roots stay arrays while nested request fields are filtered."""
        schema = {
            "$id": "https://ucp.dev/schemas/totals.json",
            "title": "Totals",
            "type": "array",
            "items": {
                "allOf": [
                    {
                        "type": "object",
                        "properties": {
                            "amount": {"type": "integer"},
                            "label": {
                                "type": "string",
                                "ucp_request": {"create": "required"},
                            },
                            "lines": {
                                "type": "array",
                                "ucp_request": {"create": "omit"},
                            },
                        },
                        "required": ["amount"],
                    }
                ]
            },
        }

        variant = preprocess_schemas._create_single_variant(
            schema,
            "create",
            "totals",
            Path("/schemas/totals.json"),
            {},
        )

        self.assertEqual(variant["type"], "array")
        self.assertNotIn("properties", variant)
        self.assertNotIn("required", variant)
        item_schema = variant["items"]["allOf"][0]
        self.assertEqual(set(item_schema["properties"]), {"amount", "label"})
        self.assertEqual(set(item_schema["required"]), {"amount", "label"})
        self.assertEqual(variant["title"], "Totals Create Request")

    def test_generate_variants_writes_operation_specific_files(self) -> None:
        """Variant generation writes one filtered file per operation."""
        schema = {
            "title": "Product",
            "type": "object",
            "properties": {
                "id": {
                    "type": "string",
                    "ucp_request": {
                        "create": "omit",
                        "update": "required",
                    },
                }
            },
            "required": ["id"],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "product.json"
            with contextlib.redirect_stdout(io.StringIO()):
                preprocess_schemas.generate_variants(
                    source_path,
                    schema,
                    {"create", "update"},
                    {},
                )

            create_variant = preprocess_schemas.load_json(
                Path(temp_dir) / "product_create_request.json"
            )
            update_variant = preprocess_schemas.load_json(
                Path(temp_dir) / "product_update_request.json"
            )

        self.assertEqual(create_variant["properties"], {})
        self.assertEqual(create_variant["required"], [])
        self.assertEqual(set(update_variant["properties"]), {"id"})
        self.assertEqual(update_variant["required"], ["id"])


class PipelineDependencyTest(unittest.TestCase):
    """Tests metadata normalization and transitive variant dependencies."""

    def test_normalize_metadata_schemas_sets_root_union_and_ucp_refs(
        self,
    ) -> None:
        """Metadata schemas expose the root union and normalized references."""
        target_dir = Path("/schemas")
        ucp_path = str((target_dir / "ucp.json").resolve())
        checkout_path = str((target_dir / "checkout.json").resolve())
        request_path = str(
            (target_dir / "checkout_create_request.json").resolve()
        )
        schemas = {
            ucp_path: {
                "$defs": {
                    "version": {"type": "string"},
                    "entity": {"type": "object"},
                    "platform_schema": {"type": "object"},
                    "business_schema": {"type": "object"},
                    "response_checkout_schema": {"type": "object"},
                    "response_order_schema": {"type": "object"},
                    "response_cart_schema": {"type": "object"},
                    "response_catalog_schema": {"type": "object"},
                }
            },
            checkout_path: {
                "properties": {
                    "ucp": {"$ref": "ucp.json#/$defs/response_schema"}
                }
            },
            request_path: {
                "properties": {
                    "ucp": {"$ref": "ucp.json#/$defs/request_schema"}
                }
            },
        }

        preprocess_schemas.normalize_metadata_schemas(schemas, target_dir)

        self.assertEqual(
            schemas[ucp_path]["oneOf"],
            [
                {"$ref": "#/$defs/platform_schema"},
                {"$ref": "#/$defs/business_schema"},
                {"$ref": "#/$defs/response_checkout_schema"},
                {"$ref": "#/$defs/response_order_schema"},
                {"$ref": "#/$defs/response_cart_schema"},
                {"$ref": "#/$defs/response_catalog_schema"},
            ],
        )
        self.assertEqual(
            schemas[checkout_path]["properties"]["ucp"]["$ref"],
            "ucp.json",
        )
        self.assertEqual(
            schemas[request_path]["properties"]["ucp"]["$ref"],
            "ucp.json#/$defs/request_schema",
        )

    def test_variant_needs_propagate_transitively_and_respect_omit(
        self,
    ) -> None:
        """Variant dependencies propagate only through included properties."""
        parent_path = "/schemas/parent.json"
        child_path = "/schemas/child.json"
        grandchild_path = "/schemas/grandchild.json"
        schemas = {
            parent_path: {
                "properties": {
                    "child": {
                        "$ref": "child.json",
                        "ucp_request": {
                            "create": "required",
                            "update": "omit",
                        },
                    }
                }
            },
            child_path: {
                "properties": {"grandchild": {"$ref": "grandchild.json"}}
            },
            grandchild_path: {"properties": {}},
        }
        schema_refs = {
            parent_path: [("child", child_path)],
            child_path: [("grandchild", grandchild_path)],
            grandchild_path: [],
        }
        variant_needs = {parent_path: {"create", "update"}}

        preprocess_schemas.propagate_needs_transitive(
            variant_needs, schema_refs, schemas
        )

        self.assertEqual(variant_needs[child_path], {"create"})
        self.assertEqual(variant_needs[grandchild_path], {"create"})

    def test_main_preprocesses_schema_tree_end_to_end(self) -> None:
        """The full pipeline normalizes schemas and writes linked variants."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            preprocess_schemas.save_json(
                {
                    "$defs": {
                        "entity": {
                            "type": "object",
                            "properties": {"id": {"type": "string"}},
                            "required": ["id"],
                        }
                    }
                },
                root / "ucp.json",
            )
            preprocess_schemas.save_json(
                {
                    "$id": "https://ucp.dev/schemas/child.json",
                    "title": "Child",
                    "type": "object",
                    "properties": {
                        "value": {
                            "type": "string",
                            "ucp_request": {"create": "required"},
                        }
                    },
                },
                root / "child.json",
            )
            preprocess_schemas.save_json(
                {
                    "$id": "https://ucp.dev/schemas/parent.json",
                    "title": "Parent",
                    "allOf": [{"$ref": "ucp.json#/$defs/entity"}],
                    "properties": {
                        "child": {
                            "$ref": "child.json",
                            "ucp_request": {"create": "required"},
                        }
                    },
                },
                root / "parent.json",
            )

            with (
                mock.patch.object(
                    sys,
                    "argv",
                    ["preprocess_schemas.py", str(root)],
                ),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                preprocess_schemas.main()

            parent = preprocess_schemas.load_json(root / "parent.json")
            parent_variant = preprocess_schemas.load_json(
                root / "parent_create_request.json"
            )
            child_variant = preprocess_schemas.load_json(
                root / "child_create_request.json"
            )

        self.assertNotIn("allOf", parent)
        self.assertEqual(set(parent["properties"]), {"id", "child"})
        self.assertEqual(
            parent_variant["properties"]["child"]["$ref"],
            "child_create_request.json",
        )
        self.assertEqual(set(parent_variant["required"]), {"id", "child"})
        self.assertEqual(child_variant["required"], ["value"])

    def test_propagation_with_fragment(self) -> None:
        """Propagation should work even if the reference has a fragment."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            preprocess_schemas.save_json(
                {
                    "$defs": {
                        "entity": {
                            "type": "object",
                            "properties": {"id": {"type": "string"}},
                            "required": ["id"],
                        }
                    }
                },
                root / "ucp.json",
            )
            preprocess_schemas.save_json(
                {
                    "$id": "https://ucp.dev/schemas/child.json",
                    "title": "Child",
                    "type": "object",
                    "$defs": {
                        "item": {
                            "type": "object",
                            "properties": {
                                "grandchild": {"$ref": "grandchild.json"}
                            },
                        }
                    },
                    "properties": {"dummy": {"type": "string"}},
                },
                root / "child.json",
            )
            preprocess_schemas.save_json(
                {
                    "$id": "https://ucp.dev/schemas/grandchild.json",
                    "title": "Grandchild",
                    "type": "object",
                    "properties": {
                        "value": {
                            "type": "string",
                            "ucp_request": {"create": "required"},
                        }
                    },
                },
                root / "grandchild.json",
            )
            preprocess_schemas.save_json(
                {
                    "$id": "https://ucp.dev/schemas/parent.json",
                    "title": "Parent",
                    "allOf": [{"$ref": "ucp.json#/$defs/entity"}],
                    "properties": {
                        "child_item": {
                            "$ref": "child.json#/$defs/item",
                            "ucp_request": {"create": "required"},
                        }
                    },
                },
                root / "parent.json",
            )

            with (
                mock.patch.object(
                    sys,
                    "argv",
                    ["preprocess_schemas.py", str(root)],
                ),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                preprocess_schemas.main()

            self.assertTrue(
                (root / "child_create_request.json").exists(),
                "child_create_request.json was not generated",
            )
            self.assertTrue(
                (root / "grandchild_create_request.json").exists(),
                "grandchild_create_request.json was not generated",
            )

            parent_variant = preprocess_schemas.load_json(
                root / "parent_create_request.json"
            )
            self.assertEqual(
                parent_variant["properties"]["child_item"]["$ref"],
                "child_create_request.json#/$defs/item",
            )


class MetadataUnionTest(unittest.TestCase):
    """The UcpMetadata root union is derived from ucp.json $defs."""

    def test_includes_profiles_and_all_response_schemas(self) -> None:
        """Profiles and every response_*_schema belong to the union."""
        ucp = {
            "$defs": {
                "version": {"type": "string"},
                "version_constraint": {"type": "object"},
                "requires": {"type": "object"},
                "entity": {"type": "object"},
                "base": {"type": "object"},
                "success": {"type": "object"},
                "error": {"type": "object"},
                "platform_schema": {"type": "object"},
                "business_schema": {"type": "object"},
                "response_checkout_schema": {"type": "object"},
                "response_order_schema": {"type": "object"},
                "response_cart_schema": {"type": "object"},
                "response_catalog_schema": {"type": "object"},
            }
        }
        self.assertEqual(
            preprocess_schemas.metadata_union_members(ucp),
            [
                "platform_schema",
                "business_schema",
                "response_checkout_schema",
                "response_order_schema",
                "response_cart_schema",
                "response_catalog_schema",
            ],
        )

    def test_picks_up_new_response_types_automatically(self) -> None:
        """A response schema added upstream is included without code changes."""
        ucp = {
            "$defs": {
                "platform_schema": {"type": "object"},
                "business_schema": {"type": "object"},
                "response_invoice_schema": {"type": "object"},
            }
        }
        self.assertEqual(
            preprocess_schemas.metadata_union_members(ucp),
            ["platform_schema", "business_schema", "response_invoice_schema"],
        )

    def test_excludes_non_schema_defs(self) -> None:
        """Helper and shared defs never leak into the metadata union."""
        ucp = {
            "$defs": {
                "entity": {"type": "object"},
                "request_schema": {"type": "object"},
                "base": {"type": "object"},
            }
        }
        self.assertEqual(preprocess_schemas.metadata_union_members(ucp), [])

    def test_empty_defs_yields_empty_union(self) -> None:
        """No $defs means no union members."""
        self.assertEqual(preprocess_schemas.metadata_union_members({}), [])


@unittest.skipUnless(
    HAVE_SDK, "requires the installed package (pip install -e .)"
)
class DescriptionMinPropertiesTest(unittest.TestCase):
    """description.json declares minProperties: 1 at the schema root."""

    def test_empty_instance_rejected(self):
        with self.assertRaisesRegex(ValidationError, "[Aa]t least 1"):
            Description()

    def test_empty_mapping_rejected(self):
        with self.assertRaisesRegex(ValidationError, "[Aa]t least 1"):
            Description.model_validate({})

    def test_single_declared_field_accepted(self):
        self.assertEqual(Description(plain="hello").plain, "hello")

    def test_explicit_null_key_counts_as_present(self):
        # {"html": null} has one property per JSON Schema's key counting.
        Description.model_validate({"html": None})

    def test_extra_field_counts_as_present(self):
        # extra="allow": an unknown key is a present property.
        Description.model_validate({"x-vendor-note": "hi"})

    def test_all_fields_accepted(self):
        Description(plain="p", html="<p>p</p>", markdown="p")


@unittest.skipUnless(
    HAVE_SDK, "requires the installed package (pip install -e .)"
)
class SignalsPropertyNamesTest(unittest.TestCase):
    """signals.json declares propertyNames (reverse-domain keys).

    Signals has named ``properties`` AND ``additionalProperties: true``, so the
    generator emits ``class Signals(BaseModel)`` with ``extra="allow"`` and named
    fields; extra keys bypass the ``propertyNames`` pattern. The post-generation
    injector restores the check on every ``model_extra`` key while preserving
    well-formed reverse-domain extras (extra="allow" keeps them).
    """

    def _signals(self):
        from ucp_sdk.models.schemas.shopping.types.signals import Signals

        return Signals

    def test_malformed_extra_key_rejected(self):
        with self.assertRaisesRegex(ValidationError, "propertyNames"):
            self._signals().model_validate(
                {"dev.ucp.buyer_ip": "1.2.3.4", "bogus KEY!": "x"}
            )

    def test_trailing_newline_key_rejected(self):
        # A $-anchored pattern with re.match would let a trailing newline
        # slip through; the enforcement uses re.fullmatch to agree with
        # pydantic-core's key validation on the sibling dict-map path.
        with self.assertRaisesRegex(ValidationError, "propertyNames"):
            self._signals().model_validate({"com.example.k\n": "x"})

    def test_valid_reverse_domain_extra_accepted_and_preserved(self):
        signals = self._signals().model_validate(
            {"com.example.device_id": "abc123"}
        )
        # extra="allow" must still keep a well-formed extra key.
        self.assertEqual(
            signals.model_extra, {"com.example.device_id": "abc123"}
        )

    def test_known_named_fields_still_work(self):
        signals = self._signals().model_validate(
            {
                "dev.ucp.buyer_ip": "1.2.3.4",
                "dev.ucp.user_agent": "curl/8",
            }
        )
        self.assertEqual(signals.dev_ucp_buyer_ip, "1.2.3.4")
        self.assertEqual(signals.dev_ucp_user_agent, "curl/8")
        self.assertEqual(signals.model_extra, {})

    def test_request_variants_enforce_property_names(self):
        # The gap and its fix travel to the generated request variants too.
        from ucp_sdk.models.schemas.shopping.types.signals_complete_request import (
            SignalsCompleteRequest,
        )
        from ucp_sdk.models.schemas.shopping.types.signals_create_request import (
            SignalsCreateRequest,
        )
        from ucp_sdk.models.schemas.shopping.types.signals_update_request import (
            SignalsUpdateRequest,
        )

        for cls in (
            SignalsCreateRequest,
            SignalsUpdateRequest,
            SignalsCompleteRequest,
        ):
            with self.subTest(model=cls.__name__):
                with self.assertRaisesRegex(ValidationError, "propertyNames"):
                    cls.model_validate({"bogus KEY!": "x"})
                self.assertEqual(
                    cls.model_validate({"com.example.k": "v"}).model_extra,
                    {"com.example.k": "v"},
                )


class PropertyNamesInjectorTest(unittest.TestCase):
    """The propertyNames post-generation injector's own behavior."""

    PATTERN = "^[a-z][a-z0-9]*(?:\\.[a-z][a-z0-9_]*)+$"

    SCHEMA = {
        "title": "Signals",
        "type": "object",
        "propertyNames": {"pattern": PATTERN},
        "properties": {"dev.ucp.buyer_ip": {"type": "string"}},
        "additionalProperties": True,
    }

    # An object with propertyNames but no named properties is a dict-map
    # (key type already carries the pattern) — out of scope.
    DICT_MAP_SCHEMA = {
        "title": "Requires",
        "type": "object",
        "propertyNames": {"pattern": PATTERN},
        "additionalProperties": {"type": "string"},
    }

    MODULE = (
        "from __future__ import annotations\n"
        "\n"
        "from pydantic import BaseModel, ConfigDict, Field\n"
        "\n"
        "\n"
        "class Signals(BaseModel):\n"
        '    """Signals."""\n'
        "\n"
        "    model_config = ConfigDict(\n"
        '        extra="allow",\n'
        "    )\n"
        '    dev_ucp_buyer_ip: str | None = Field(None, alias="dev.ucp.buyer_ip")\n'
    )

    def test_scan_finds_only_extra_allow_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "signals.json").write_text(json.dumps(self.SCHEMA))
            (Path(tmp) / "requires.json").write_text(
                json.dumps(self.DICT_MAP_SCHEMA)
            )
            found = postprocess_models.find_property_names_patterns(Path(tmp))
        self.assertEqual(found, {"Signals": self.PATTERN})

    def test_scan_resolves_ref_pattern(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "reverse_domain_name.json").write_text(
                json.dumps({"type": "string", "pattern": self.PATTERN})
            )
            (Path(tmp) / "thing.json").write_text(
                json.dumps(
                    {
                        "title": "Thing",
                        "type": "object",
                        "propertyNames": {"$ref": "reverse_domain_name.json"},
                        "properties": {"a": {"type": "string"}},
                    }
                )
            )
            found = postprocess_models.find_property_names_patterns(Path(tmp))
        self.assertEqual(found, {"Thing": self.PATTERN})

    def test_injects_validator_and_imports(self):
        out = postprocess_models.inject_property_names(
            self.MODULE, "Signals", self.PATTERN
        )
        self.assertIn("model_validator", out)
        self.assertIn("import re", out)
        self.assertIn("propertyNames", out)

    def test_injection_is_idempotent(self):
        once = postprocess_models.inject_property_names(
            self.MODULE, "Signals", self.PATTERN
        )
        twice = postprocess_models.inject_property_names(
            once, "Signals", self.PATTERN
        )
        self.assertEqual(once, twice)

    @unittest.skipUnless(HAVE_SDK, "executing the module needs pydantic")
    def test_injected_validator_enforces_pattern(self):
        out = postprocess_models.inject_property_names(
            self.MODULE, "Signals", self.PATTERN
        )
        namespace: dict = {}
        exec(compile(out, "<injected>", "exec"), namespace)  # noqa: S102
        signals_cls = namespace["Signals"]
        with self.assertRaises(ValidationError):
            signals_cls.model_validate({"bogus KEY!": "x"})
        # fullmatch (not match) — a trailing newline must not slip through.
        with self.assertRaises(ValidationError):
            signals_cls.model_validate({"com.example.ok\n": "v"})
        signals_cls.model_validate({"com.example.ok": "v"})


class ConditionalRequiredInjectorTest(unittest.TestCase):
    """Simple JSON Schema if/then required constraints are restored."""

    MODULE = (
        "from __future__ import annotations\n"
        "\n"
        "from pydantic import BaseModel, ConfigDict\n"
        "\n"
        "\n"
        "class Response(BaseModel):\n"
        '    model_config = ConfigDict(extra="allow")\n'
        "    cursor: str | None = None\n"
        "    has_next_page: bool\n"
    )
    RULES = [
        {
            "discriminator": "has_next_page",
            "values": [True],
            "required": ["cursor"],
        }
    ]

    def test_schema_scan_maps_nested_definition_to_generated_class(self):
        schema = {
            "title": "Pagination",
            "type": "object",
            "$defs": {
                "response": {
                    "type": "object",
                    "properties": {
                        "cursor": {"type": "string"},
                        "has_next_page": {"type": "boolean"},
                    },
                    "if": {
                        "properties": {"has_next_page": {"const": True}},
                        "required": ["has_next_page"],
                    },
                    "then": {"required": ["cursor"]},
                }
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "pagination.json").write_text(json.dumps(schema))
            found = postprocess_models.find_conditional_required(Path(tmp))
        self.assertEqual(found, {"Response": self.RULES})

    def test_schema_scan_skips_else_branches(self):
        schema = {
            "title": "Response",
            "type": "object",
            "properties": {
                "cursor": {"type": "string"},
                "has_next_page": {"type": "boolean"},
            },
            "if": {
                "properties": {"has_next_page": {"const": True}},
                "required": ["has_next_page"],
            },
            "then": {"required": ["cursor"]},
            "else": {"required": ["other"]},
        }
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "response.json").write_text(json.dumps(schema))
            found = postprocess_models.find_conditional_required(Path(tmp))
        self.assertEqual(found, {})

    def test_injection_is_idempotent(self):
        once = postprocess_models.inject_conditional_required(
            self.MODULE, "Response", self.RULES
        )
        twice = postprocess_models.inject_conditional_required(
            once, "Response", self.RULES
        )
        self.assertEqual(once, twice)

    @unittest.skipUnless(HAVE_SDK, "executing the module needs pydantic")
    def test_injected_validator_enforces_conditional_required(self):
        out = postprocess_models.inject_conditional_required(
            self.MODULE, "Response", self.RULES
        )
        namespace: dict = {}
        exec(compile(out, "<injected>", "exec"), namespace)  # noqa: S102
        response = namespace["Response"]
        with self.assertRaises(ValidationError):
            response(has_next_page=True)
        response(has_next_page=True, cursor="next-page")
        response(has_next_page=False)


class ConditionalNumericBoundsInjectorTest(unittest.TestCase):
    """Simple JSON Schema if/then numeric bounds are restored."""

    MODULE = (
        "from __future__ import annotations\n"
        "\n"
        "from pydantic import BaseModel, ConfigDict\n"
        "\n"
        "\n"
        "class Total(BaseModel):\n"
        '    model_config = ConfigDict(extra="allow")\n'
        "    type: str\n"
        "    amount: int\n"
    )
    RULES = [
        {
            "discriminator": "type",
            "values": ["discount", "items_discount"],
            "field": "amount",
            "bound": "exclusiveMaximum",
            "value": 0,
        },
        {
            "discriminator": "type",
            "values": ["subtotal", "tax"],
            "field": "amount",
            "bound": "minimum",
            "value": 0,
        },
    ]

    def test_schema_scan_finds_allof_numeric_bounds(self):
        schema = {
            "title": "Total",
            "type": "object",
            "properties": {
                "type": {"type": "string"},
                "amount": {"type": "integer"},
            },
            "allOf": [
                {
                    "if": {
                        "properties": {
                            "type": {"enum": ["discount", "items_discount"]}
                        },
                        "required": ["type"],
                    },
                    "then": {"properties": {"amount": {"exclusiveMaximum": 0}}},
                },
                {
                    "if": {
                        "properties": {"type": {"enum": ["subtotal", "tax"]}},
                        "required": ["type"],
                    },
                    "then": {"properties": {"amount": {"minimum": 0}}},
                },
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "total.json").write_text(json.dumps(schema))
            found = postprocess_models.find_conditional_numeric_bounds(
                Path(tmp)
            )
        self.assertEqual(found, {"Total": self.RULES})

    def test_schema_scan_skips_else_and_multiple_target_fields(self):
        schema = {
            "title": "Total",
            "type": "object",
            "properties": {
                "type": {"type": "string"},
                "amount": {"type": "integer"},
                "other": {"type": "integer"},
            },
            "allOf": [
                {
                    "if": {
                        "properties": {"type": {"const": "discount"}},
                        "required": ["type"],
                    },
                    "then": {"properties": {"amount": {"maximum": 0}}},
                    "else": {"properties": {"amount": {"minimum": 0}}},
                },
                {
                    "if": {
                        "properties": {"type": {"const": "fee"}},
                        "required": ["type"],
                    },
                    "then": {
                        "properties": {
                            "amount": {"minimum": 0},
                            "other": {"minimum": 0},
                        }
                    },
                },
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "total.json").write_text(json.dumps(schema))
            found = postprocess_models.find_conditional_numeric_bounds(
                Path(tmp)
            )
        self.assertEqual(found, {})

    def test_injection_is_idempotent(self):
        once = postprocess_models.inject_conditional_numeric_bounds(
            self.MODULE, "Total", self.RULES
        )
        twice = postprocess_models.inject_conditional_numeric_bounds(
            once, "Total", self.RULES
        )
        self.assertEqual(once, twice)

    @unittest.skipUnless(HAVE_SDK, "executing the module needs pydantic")
    def test_injected_validator_enforces_numeric_bounds(self):
        out = postprocess_models.inject_conditional_numeric_bounds(
            self.MODULE, "Total", self.RULES
        )
        namespace: dict = {}
        exec(compile(out, "<injected>", "exec"), namespace)  # noqa: S102
        total_cls = namespace["Total"]
        with self.assertRaises(ValidationError):
            total_cls(type="discount", amount=1)
        with self.assertRaises(ValidationError):
            total_cls(type="subtotal", amount=-1)
        total_cls(type="discount", amount=-1)
        total_cls(type="subtotal", amount=0)
        total_cls(type="custom", amount=-1)

    @unittest.skipUnless(HAVE_SDK, "executing the model needs pydantic")
    def test_generated_total_enforces_schema_bounds(self):
        with self.assertRaises(ValidationError):
            Total(type="discount", amount=1)
        with self.assertRaises(ValidationError):
            Total(type="subtotal", amount=-1)
        Total(type="discount", amount=-1)
        Total(type="subtotal", amount=0)
        Total(type="custom", amount=-1)


class InjectorTest(unittest.TestCase):
    """The post-generation injector's own behavior."""

    SCHEMA = {
        "title": "Sample",
        "type": "object",
        "minProperties": 2,
        "properties": {"a": {"type": "string"}, "b": {"type": "string"}},
    }

    MODULE = (
        "from __future__ import annotations\n"
        "\n"
        "from pydantic import BaseModel, ConfigDict\n"
        "\n"
        "\n"
        "class Sample(BaseModel):\n"
        '    """A sample."""\n'
        "\n"
        "    model_config = ConfigDict(\n"
        '        extra="allow",\n'
        "    )\n"
        "    a: str | None = None\n"
        "    b: str | None = None\n"
    )

    def test_injects_validator_with_declared_minimum(self):
        out = postprocess_models.inject_min_properties(self.MODULE, "Sample", 2)
        self.assertIn("model_validator", out)
        self.assertIn("at least 2", out.lower())

    @unittest.skipUnless(HAVE_SDK, "executing the module needs pydantic")
    def test_injected_validator_enforces_count(self):
        out = postprocess_models.inject_min_properties(self.MODULE, "Sample", 2)
        namespace: dict = {}
        exec(compile(out, "<injected>", "exec"), namespace)  # noqa: S102
        sample_cls = namespace["Sample"]
        with self.assertRaises(ValidationError):
            sample_cls(a="only-one")
        sample_cls(a="one", b="two")

    def test_injection_is_idempotent(self):
        once = postprocess_models.inject_min_properties(
            self.MODULE, "Sample", 2
        )
        twice = postprocess_models.inject_min_properties(once, "Sample", 2)
        self.assertEqual(once, twice)

    def test_schema_scan_finds_root_constraints(self):
        with tempfile.TemporaryDirectory() as tmp:
            sub = Path(tmp) / "sub"
            sub.mkdir()
            (sub / "sample.json").write_text(json.dumps(self.SCHEMA))
            (sub / "plain.json").write_text(
                json.dumps(
                    {"title": "Plain", "type": "object", "properties": {}}
                )
            )
            found = postprocess_models.find_root_min_properties(Path(tmp))
        self.assertEqual(found, {"Sample": 2})


@unittest.skipUnless(
    HAVE_SDK, "requires the installed package (pip install -e .)"
)
class TotalsContainsTest(unittest.TestCase):
    """totals.json requires exactly one ``subtotal`` AND one ``total`` entry.

    Both rules live as two ``allOf`` ``contains`` branches; the generator drops
    them, leaving ``Totals`` a bare ``list[Total]``. The post-generation injector
    reads the pristine schema and restores BOTH bounds as an ``AfterValidator``
    on the alias — the same check reaching the generated request variants too.
    """

    SUBTOTAL = {"type": "subtotal", "amount": 100, "display_text": "Subtotal"}
    TOTAL = {"type": "total", "amount": 100, "display_text": "Total"}

    #: (name, array, expected-valid?) exercised against every totals model.
    def _cases(self):
        return [
            ("empty", [], False),
            ("two_total_no_subtotal", [self.TOTAL, self.TOTAL], False),
            ("two_subtotal_no_total", [self.SUBTOTAL, self.SUBTOTAL], False),
            ("subtotal_only", [self.SUBTOTAL], False),
            ("total_only", [self.TOTAL], False),
            ("valid_subtotal_and_total", [self.SUBTOTAL, self.TOTAL], True),
        ]

    def _assert_matrix(self, alias):
        adapter = TypeAdapter(alias)
        for name, array, valid in self._cases():
            with self.subTest(model=alias.__name__, case=name):
                if valid:
                    self.assertEqual(len(adapter.validate_python(array)), 2)
                else:
                    with self.assertRaises(ValidationError):
                        adapter.validate_python(array)

    def test_base_totals_enforces_both_bounds(self):
        self._assert_matrix(Totals)

    def test_create_request_variant_enforces_both_bounds(self):
        self._assert_matrix(TotalsCreateRequest)

    def test_update_request_variant_enforces_both_bounds(self):
        self._assert_matrix(TotalsUpdateRequest)

    def test_missing_total_names_the_total_rule(self):
        # A subtotal-only array must fail specifically on the total rule.
        with self.assertRaisesRegex(ValidationError, "total"):
            TypeAdapter(Totals).validate_python([self.SUBTOTAL])


class ArrayContainsInjectorTest(unittest.TestCase):
    """The array-contains injector's own behavior."""

    MODULE = (
        "from __future__ import annotations\n"
        "\n"
        "from typing import Annotated\n"
        "\n"
        "from pydantic import BaseModel, ConfigDict, Field\n"
        "from typing_extensions import TypeAliasType\n"
        "\n"
        "\n"
        "class Total(BaseModel):\n"
        '    model_config = ConfigDict(extra="allow")\n'
        "    type: str\n"
        "    amount: int\n"
        "\n"
        "\n"
        "Totals = TypeAliasType(\n"
        '    "Totals", Annotated[list[Total], Field(..., title="Totals")]\n'
        ")\n"
    )

    #: subtotal AND total, mirroring the real totals.json.
    GROUPS = [
        {"pairs": [("type", "subtotal")], "min": 1, "max": 1},
        {"pairs": [("type", "total")], "min": 1, "max": 1},
    ]

    def test_scan_reads_both_contains_from_allof_branches(self):
        # The pristine totals.json shape: two allOf contains branches.
        schema = {
            "title": "Totals",
            "type": "array",
            "allOf": [
                {
                    "contains": {"properties": {"type": {"const": "subtotal"}}},
                    "minContains": 1,
                    "maxContains": 1,
                },
                {
                    "contains": {"properties": {"type": {"const": "total"}}},
                    "minContains": 1,
                    "maxContains": 1,
                },
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "totals.json").write_text(json.dumps(schema))
            found = postprocess_models.find_array_contains_constraints(
                Path(tmp)
            )
        self.assertEqual(set(found), {"totals"})
        self.assertEqual(found["totals"]["title"], "Totals")
        self.assertEqual(
            [g["pairs"] for g in found["totals"]["groups"]],
            [[("type", "subtotal")], [("type", "total")]],
        )

    def test_scan_reads_root_level_single_contains(self):
        # A root-level (non-allOf) contains still yields one group.
        schema = {
            "title": "Totals",
            "type": "array",
            "items": {"type": "object"},
            "contains": {"properties": {"type": {"const": "subtotal"}}},
            "minContains": 1,
            "maxContains": 1,
        }
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "totals.json").write_text(json.dumps(schema))
            found = postprocess_models.find_array_contains_constraints(
                Path(tmp)
            )
        self.assertEqual(
            found["totals"]["groups"],
            [{"pairs": [("type", "subtotal")], "min": 1, "max": 1}],
        )

    def test_scan_ignores_non_array_and_predicateless_contains(self):
        with tempfile.TemporaryDirectory() as tmp:
            # An object schema (not an array) is out of scope.
            (Path(tmp) / "obj.json").write_text(
                json.dumps({"title": "Obj", "type": "object"})
            )
            # A contains with no derivable const predicate is skipped.
            (Path(tmp) / "arr.json").write_text(
                json.dumps(
                    {
                        "title": "Arr",
                        "type": "array",
                        "contains": {"required": ["type"]},
                    }
                )
            )
            with contextlib.redirect_stderr(io.StringIO()):
                found = postprocess_models.find_array_contains_constraints(
                    Path(tmp)
                )
        self.assertEqual(found, {})

    def test_injects_after_validator_and_import(self):
        out = postprocess_models.inject_array_contains(
            self.MODULE, "Totals", self.GROUPS
        )
        self.assertIn("AfterValidator(_enforce_contains_totals)", out)
        self.assertRegex(out, r"from pydantic import .*AfterValidator")
        # Both predicates are present in the injected function (pre-format
        # output uses repr() single quotes; ruff restyles them later).
        self.assertIn("== 'subtotal'", out)
        self.assertIn("== 'total'", out)

    def test_injection_is_idempotent(self):
        once = postprocess_models.inject_array_contains(
            self.MODULE, "Totals", self.GROUPS
        )
        twice = postprocess_models.inject_array_contains(
            once, "Totals", self.GROUPS
        )
        self.assertEqual(once, twice)

    @unittest.skipUnless(HAVE_SDK, "executing the module needs pydantic")
    def test_injected_validator_enforces_both_bounds(self):
        out = postprocess_models.inject_array_contains(
            self.MODULE, "Totals", self.GROUPS
        )
        namespace: dict = {}
        exec(compile(out, "<injected>", "exec"), namespace)  # noqa: S102
        adapter = TypeAdapter(namespace["Totals"])
        sub = {"type": "subtotal", "amount": 1}
        tot = {"type": "total", "amount": 1}
        for bad in ([], [sub], [tot], [sub, sub], [tot, tot]):
            with self.assertRaises(ValidationError):
                adapter.validate_python(bad)
        adapter.validate_python([sub, tot])


class UniqueItemsInjectorTest(unittest.TestCase):
    """The uniqueItems post-generation injector's own behavior."""

    SCHEMA_TREE = {
        "title": "First",
        "properties": {
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "uniqueItems": True,
            },
            "label": {"type": "array", "items": {"type": "string"}},
            "name": {"type": "string"},
            "nested": {
                "type": "object",
                "properties": {
                    "codes": {
                        "type": "array",
                        "items": {"type": "string"},
                        "uniqueItems": True,
                    }
                },
            },
        },
    }

    MODULE = (
        "from __future__ import annotations\n"
        "\n"
        "from pydantic import BaseModel, ConfigDict\n"
        "\n"
        "\n"
        "class First(BaseModel):\n"
        '    """First."""\n'
        "\n"
        "    model_config = ConfigDict(\n"
        '        extra="allow",\n'
        "    )\n"
        "    tags: list[str] | None = None\n"
        "    name: str | None = None\n"
        "\n"
        "\n"
        "class Second(BaseModel):\n"
        '    """Second."""\n'
        "\n"
        "    model_config = ConfigDict(\n"
        '        extra="allow",\n'
        "    )\n"
        "    tags: list[str] | None = None\n"
        "    count: list[int] | None = None\n"
    )

    def test_find_unique_items_fields_walks_nested_properties(self) -> None:
        """Root and nested array props with uniqueItems are collected."""
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "schema.json").write_text(json.dumps(self.SCHEMA_TREE))
            fields = postprocess_models.find_unique_items_fields(Path(tmp))
        self.assertEqual(fields, {"First": {"tags"}, "Nested": {"codes"}})

    def test_find_unique_items_fields_ignores_false_and_non_arrays(
        self,
    ) -> None:
        """uniqueItems: false and non-array props do not qualify."""
        schema = {
            "properties": {
                "a": {
                    "type": "array",
                    "items": {"type": "string"},
                    "uniqueItems": False,
                },
                "b": {"type": "string", "uniqueItems": True},
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "s.json").write_text(json.dumps(schema))
            fields = postprocess_models.find_unique_items_fields(Path(tmp))
        self.assertEqual(fields, {})

    def test_inject_targets_matching_list_fields_only(self) -> None:
        """Only the declaring class's matching list field gets a validator."""
        out = postprocess_models.inject_unique_items(
            self.MODULE, {"First": {"tags"}}
        )
        self.assertIn("field_validator", out)
        self.assertIn("_enforce_unique_items_tags", out)
        self.assertEqual(out.count("def _enforce_unique_items_tags("), 1)
        self.assertNotIn("_enforce_unique_items_name", out)
        self.assertNotIn("_enforce_unique_items_count", out)

    def test_inject_no_match_leaves_source_unchanged(self) -> None:
        """No matching list field means the module is untouched."""
        self.assertEqual(
            postprocess_models.inject_unique_items(
                self.MODULE, {"First": {"missing"}}
            ),
            self.MODULE,
        )

    def test_injection_is_idempotent(self) -> None:
        """Re-running the injector changes nothing."""
        unique_fields = {"First": {"tags"}}
        once = postprocess_models.inject_unique_items(
            self.MODULE, unique_fields
        )
        twice = postprocess_models.inject_unique_items(once, unique_fields)
        self.assertEqual(once, twice)

    @unittest.skipUnless(HAVE_SDK, "executing the module needs pydantic")
    def test_injected_validator_rejects_duplicates(self) -> None:
        """The injected field_validator enforces uniqueness at runtime."""
        out = postprocess_models.inject_unique_items(
            self.MODULE, {"First": {"tags"}}
        )
        namespace: dict = {}
        exec(compile(out, "<injected>", "exec"), namespace)  # noqa: S102
        first = namespace["First"]
        first(tags=["a", "b"])  # unique passes
        first()  # None passes
        with self.assertRaises(ValidationError):
            first(tags=["a", "a"])  # duplicate rejected


@unittest.skipUnless(
    HAVE_SDK, "requires the installed package (pip install -e .)"
)
class UniqueItemsSemanticTest(unittest.TestCase):
    """Committed models enforce uniqueItems on declared array fields."""

    def test_brands_rejects_duplicates(self) -> None:
        """card_payment_instrument brands rejects duplicate entries."""
        from ucp_sdk.models.schemas.shopping.types.card_payment_instrument import (
            Constraints,
        )

        with self.assertRaisesRegex(ValidationError, "[Uu]nique"):
            Constraints(brands=["visa", "visa"])

    def test_brands_accepts_unique_and_none(self) -> None:
        """Unique lists and missing values are accepted."""
        from ucp_sdk.models.schemas.shopping.types.card_payment_instrument import (
            Constraints,
        )

        self.assertEqual(
            Constraints(brands=["visa", "mc"]).brands, ["visa", "mc"]
        )
        self.assertIsNone(Constraints().brands)


class AdditionalPropertiesForbidFinderTest(unittest.TestCase):
    """additionalProperties:false objects map to generated class names."""

    def test_root_titled_object(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "error_response.json").write_text(
                json.dumps(
                    {
                        "title": "Error Response",
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {"messages": {"type": "array"}},
                    }
                ),
                encoding="utf-8",
            )
            names = postprocess_models.find_extra_forbid_class_names(Path(tmp))
        self.assertEqual(names, {"ErrorResponse"})

    def test_nested_untitled_object_uses_property_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "merchant_fulfillment_config.json").write_text(
                json.dumps(
                    {
                        "title": "Merchant Fulfillment Config",
                        "type": "object",
                        "properties": {
                            "allows_multi_destination": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {"shipping": {"type": "boolean"}},
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            names = postprocess_models.find_extra_forbid_class_names(Path(tmp))
        self.assertEqual(names, {"AllowsMultiDestination"})

    def test_loose_and_map_objects_are_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "open.json").write_text(
                json.dumps(
                    {
                        "title": "Open Object",
                        "type": "object",
                        "properties": {"a": {"type": "string"}},
                    }
                ),
                encoding="utf-8",
            )
            Path(tmp, "map.json").write_text(
                json.dumps(
                    {
                        "title": "Map Object",
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                        "properties": {"a": {"type": "string"}},
                    }
                ),
                encoding="utf-8",
            )
            names = postprocess_models.find_extra_forbid_class_names(Path(tmp))
        self.assertEqual(names, set())


class AdditionalPropertiesForbidInjectorTest(unittest.TestCase):
    """The injector flips only the target class's model_config to forbid."""

    SOURCE = '''\
class AllowsMultiDestination(BaseModel):
    """
    Permits multiple destinations per method type.
    """

    model_config = ConfigDict(
        extra="allow",
    )
    shipping: bool | None = None


class MerchantFulfillmentConfig(BaseModel):
    """
    Merchant's fulfillment configuration.
    """

    model_config = ConfigDict(
        extra="allow",
    )
    allows_multi_destination: AllowsMultiDestination | None = None
'''

    def test_flips_only_target_class(self) -> None:
        updated = postprocess_models.inject_extra_forbid(
            self.SOURCE, "AllowsMultiDestination"
        )
        # Target class body now forbids extra keys.
        self.assertIn('extra="forbid"', updated)
        # The sibling class in the same module keeps extra="allow".
        sibling = """class MerchantFulfillmentConfig(BaseModel):
    \"\"\"
    Merchant's fulfillment configuration.
    \"\"\"

    model_config = ConfigDict(
        extra="allow",
    )"""
        self.assertIn(sibling, updated)

    def test_idempotent_after_flip(self) -> None:
        once = postprocess_models.inject_extra_forbid(
            self.SOURCE, "AllowsMultiDestination"
        )
        twice = postprocess_models.inject_extra_forbid(
            once, "AllowsMultiDestination"
        )
        self.assertEqual(once, twice)

    def test_unknown_class_untouched(self) -> None:
        self.assertEqual(
            postprocess_models.inject_extra_forbid(self.SOURCE, "Nope"),
            self.SOURCE,
        )


@unittest.skipUnless(
    HAVE_SDK, "requires the installed package (pip install -e .)"
)
class AdditionalPropertiesForbidSemanticTest(unittest.TestCase):
    """Committed models reject unknown keys on additionalProperties:false."""

    def test_error_response_rejects_unknown_keys(self) -> None:
        from ucp_sdk.models.schemas.shopping.types.error_response import (
            ErrorResponse,
        )

        with self.assertRaises(ValidationError):
            ErrorResponse.model_validate(
                {
                    "ucp": {"version": "2026-04-08", "status": "error"},
                    "messages": [
                        {
                            "type": "error",
                            "code": "not_found",
                            "severity": "unrecoverable",
                            "content": "boom",
                        }
                    ],
                    "bogus": "x",
                }
            )

    def test_error_response_accepts_declared_fields(self) -> None:
        from ucp_sdk.models.schemas.shopping.types.error_response import (
            ErrorResponse,
        )

        obj = ErrorResponse.model_validate(
            {
                "ucp": {"version": "2026-04-08", "status": "error"},
                "messages": [
                    {
                        "type": "error",
                        "code": "not_found",
                        "severity": "unrecoverable",
                        "content": "boom",
                    }
                ],
            }
        )
        self.assertEqual(obj.messages[0].content, "boom")

    def test_allows_multi_destination_rejects_unknown_keys(self) -> None:
        from ucp_sdk.models.schemas.shopping.types.merchant_fulfillment_config import (
            AllowsMultiDestination,
        )

        with self.assertRaises(ValidationError):
            AllowsMultiDestination.model_validate(
                {"shipping": True, "bogus": "x"}
            )

    def test_sibling_config_keeps_extra_allow(self) -> None:
        from ucp_sdk.models.schemas.shopping.types.merchant_fulfillment_config import (
            MerchantFulfillmentConfig,
        )

        config = MerchantFulfillmentConfig.model_validate({"bogus": "x"})
        self.assertEqual(config.model_extra, {"bogus": "x"})


if __name__ == "__main__":
    unittest.main()
