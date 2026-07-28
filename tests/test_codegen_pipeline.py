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


if __name__ == "__main__":
    unittest.main()
