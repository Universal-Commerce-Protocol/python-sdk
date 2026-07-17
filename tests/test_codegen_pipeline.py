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
    from pydantic import ValidationError

    from ucp_sdk.models.schemas.shopping.types.description import Description

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

    def test_composition_variant_rewrites_refs(self) -> None:
        """Composition variants (oneOf/anyOf/allOf) rewrite refs to variants."""
        schema = {
            "$id": "https://ucp.dev/schemas/poly.json",
            "title": "Poly",
            "oneOf": [{"$ref": "child_a.json"}, {"$ref": "child_b.json"}],
            "allOf": [{"$ref": "parent.json"}],
            "anyOf": [{"$ref": "other.json"}],
        }
        file_path = Path("/schemas/poly.json")
        child_a_path = str((file_path.parent / "child_a.json").resolve())
        child_b_path = str((file_path.parent / "child_b.json").resolve())
        parent_path = str((file_path.parent / "parent.json").resolve())
        other_path = str((file_path.parent / "other.json").resolve())

        variant_needs = {
            child_a_path: {"create"},
            child_b_path: {"create"},
            parent_path: {"create"},
            other_path: {"create"},
        }

        variant = preprocess_schemas._create_single_variant(
            schema,
            "create",
            "poly",
            file_path,
            variant_needs,
        )

        self.assertEqual(
            variant["oneOf"][0]["$ref"], "child_a_create_request.json"
        )
        self.assertEqual(
            variant["oneOf"][1]["$ref"], "child_b_create_request.json"
        )
        self.assertEqual(
            variant["allOf"][0]["$ref"], "parent_create_request.json"
        )
        self.assertEqual(
            variant["anyOf"][0]["$ref"], "other_create_request.json"
        )

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
            ucp_path: {"$defs": {}},
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

    def test_variant_needs_propagate_through_composition_keywords(self) -> None:
        """Variant dependencies propagate unconditionally through oneOf/anyOf/allOf/items."""
        parent_path = "/schemas/parent.json"
        child_path = "/schemas/child.json"
        schemas = {
            parent_path: {"oneOf": [{"$ref": "child.json"}]},
            child_path: {"properties": {}},
        }
        schema_refs = {
            parent_path: [("oneOf", child_path)],
            child_path: [],
        }
        variant_needs = {parent_path: {"create", "update"}}

        preprocess_schemas.propagate_needs_transitive(
            variant_needs, schema_refs, schemas
        )

        self.assertEqual(variant_needs[child_path], {"create", "update"})

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


if __name__ == "__main__":
    unittest.main()
