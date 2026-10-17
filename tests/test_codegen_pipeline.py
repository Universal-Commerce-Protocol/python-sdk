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

"""Tests for the schema codegen and postprocessing pipeline."""

import ast
import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import postprocess_models

from pydantic import TypeAdapter, ValidationError
from ucp_sdk.models import (
    Description,
    Totals,
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


class SignalsPropertyNamesTest(unittest.TestCase):
    """signals.json declares propertyNames (reverse-domain keys).

    Signals has named ``properties`` AND ``additionalProperties: true``, so the
    generator emits ``class Signals(BaseModel)`` with ``extra="allow"`` and named
    fields; extra keys bypass the ``propertyNames`` pattern. The post-generation
    injector restores the check on every ``model_extra`` key while preserving
    well-formed reverse-domain extras (extra="allow" keeps them).
    """

    def _signals(self):
        from ucp_sdk.models import Signals

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
        # Request models share the Signals type and enforce propertyNames on signals.
        from ucp_sdk.models import (
            CatalogLookupRequest,
            CatalogSearchRequest,
        )

        with self.assertRaisesRegex(ValidationError, "propertyNames"):
            CatalogLookupRequest.model_validate(
                {"ids": ["prod_1"], "signals": {"bogus KEY!": "x"}}
            )
        with self.assertRaisesRegex(ValidationError, "propertyNames"):
            CatalogSearchRequest.model_validate(
                {"signals": {"bogus KEY!": "x"}}
            )
        req = CatalogSearchRequest.model_validate(
            {"signals": {"com.example.k": "v"}}
        )
        self.assertEqual(req.signals.model_extra, {"com.example.k": "v"})


class IdentityLinkingRoleSchemaTest(unittest.TestCase):
    """identity_linking.json keeps its role schemas instead of Any aliases.

    The dotted 'dev.ucp.common.identity_linking' def is a capability role
    container. Flattening must split it into two generatable defs so the
    business role keeps the upstream contract: 'config.scopes' required with
    OAuth scope-token keys.
    """

    def _business(self):
        from ucp_sdk.models import IdentityLinkingBusinessSchema

        return IdentityLinkingBusinessSchema

    def _base(self):
        return {
            "version": "2026-08-25",
            "schema": "https://ucp.dev/2026-08-25/schemas/common/identity_linking",
        }

    def test_platform_role_schema_exists(self):
        from ucp_sdk.models import IdentityLinkingPlatformSchema

        IdentityLinkingPlatformSchema(
            version="2026-08-25",
            **{
                "schema": "https://ucp.dev/2026-08-25/schemas/common/identity_linking"
            },
            spec="https://ucp.dev/specification/common/identity-linking",
        )

    def test_business_config_with_scopes_accepted(self):
        obj = self._business().model_validate(
            {
                **self._base(),
                "config": {"scopes": {"dev.ucp.shopping.order:read": {}}},
            }
        )
        self.assertEqual(
            list(obj.config.scopes), ["dev.ucp.shopping.order:read"]
        )
        self.assertIsNone(
            obj.config.scopes["dev.ucp.shopping.order:read"].description
        )

    def test_missing_config_rejected(self):
        with self.assertRaisesRegex(ValidationError, "config"):
            self._business().model_validate(self._base())

    def test_config_without_scopes_rejected(self):
        with self.assertRaisesRegex(ValidationError, "scopes"):
            self._business().model_validate({**self._base(), "config": {}})

    def test_malformed_scope_key_rejected(self):
        with self.assertRaisesRegex(ValidationError, "pattern"):
            self._business().model_validate(
                {**self._base(), "config": {"scopes": {"BAD": {}}}}
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

    def test_schema_scan_threads_enclosing_scope_into_titled_allof_branch(
        self,
    ):
        """An allOf branch with neither its own `properties` nor a type
        title of its own is invisible today: the scan only looks at
        `node.get("properties")` on the branch itself, so a branch that
        relies on the enclosing object's properties (JWK's five if/then
        rules, none of which repeat `properties`) is silently dropped with
        no warning. And when the branch DOES carry a human-readable
        documentation `title` (JWK's branches are each titled, e.g. "EC
        keys carry crv, x, y"), the current code adopts that title as the
        class name via `_alias_name`, misattributing the rule to a
        nonexistent class instead of the enclosing `JwkPublicKey`. This
        mirrors profile.json's jwk_public_key def exactly.
        """
        schema = {
            "$defs": {
                "jwk_public_key": {
                    "type": "object",
                    "required": ["kid", "kty"],
                    "properties": {
                        "kid": {"type": "string"},
                        "kty": {"type": "string"},
                        "crv": {"type": "string"},
                        "x": {"type": "string"},
                        "y": {"type": "string"},
                    },
                    "allOf": [
                        {
                            "title": "EC keys carry crv, x, y",
                            "if": {
                                "properties": {"kty": {"const": "EC"}},
                                "required": ["kty"],
                            },
                            "then": {"required": ["crv", "x", "y"]},
                        },
                        {
                            "title": "OKP keys carry crv, x",
                            "if": {
                                "properties": {"kty": {"const": "OKP"}},
                                "required": ["kty"],
                            },
                            "then": {"required": ["crv", "x"]},
                        },
                    ],
                }
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "profile.json").write_text(json.dumps(schema))
            found = postprocess_models.find_conditional_required(Path(tmp))
        self.assertEqual(
            found,
            {
                "JwkPublicKey": [
                    {
                        "discriminator": "kty",
                        "values": ["EC"],
                        "required": ["crv", "x", "y"],
                    },
                    {
                        "discriminator": "kty",
                        "values": ["OKP"],
                        "required": ["crv", "x"],
                    },
                ]
            },
        )

    def test_injection_is_idempotent(self):
        once = postprocess_models.inject_conditional_required(
            self.MODULE, "Response", self.RULES
        )
        twice = postprocess_models.inject_conditional_required(
            once, "Response", self.RULES
        )
        self.assertEqual(once, twice)

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


class ConditionalBoundsInjectorTest(unittest.TestCase):
    """JSON Schema if/then numeric bounds are restored."""

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
            "values": ["discount"],
            "bounds": {"amount": {"exclusiveMaximum": 0}},
        },
        {
            "discriminator": "type",
            "values": ["tax"],
            "bounds": {"amount": {"minimum": 0}},
        },
    ]

    def _schema(self):
        return {
            "title": "Total",
            "type": "object",
            "properties": {
                "type": {"type": "string"},
                "amount": {"type": "integer"},
            },
            "allOf": [
                {
                    "if": {
                        "properties": {"type": {"enum": ["discount"]}},
                        "required": ["type"],
                    },
                    "then": {"properties": {"amount": {"exclusiveMaximum": 0}}},
                },
                {
                    "if": {
                        "properties": {"type": {"enum": ["tax"]}},
                        "required": ["type"],
                    },
                    "then": {"properties": {"amount": {"minimum": 0}}},
                },
            ],
        }

    def test_schema_scan_reads_rules_carried_as_allof_branches(self):
        """An if/then branch constrains the enclosing object's properties."""
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "total.json").write_text(json.dumps(self._schema()))
            found = postprocess_models.find_conditional_bounds(Path(tmp))
        self.assertEqual(found, {"Total": self.RULES})

    def test_schema_scan_skips_rules_whose_fields_were_stripped(self):
        """A request variant drops the fields, so the rule cannot apply."""
        schema = self._schema()
        schema["properties"] = {}
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "total_create_request.json").write_text(
                json.dumps(schema)
            )
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                found = postprocess_models.find_conditional_bounds(Path(tmp))
        self.assertEqual(found, {})
        self.assertNotIn("unsupported", stderr.getvalue())

    def test_schema_scan_warns_on_unsupported_shape(self):
        schema = self._schema()
        schema["allOf"][0]["then"]["properties"]["amount"] = {"pattern": "^x$"}
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "total.json").write_text(json.dumps(schema))
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                found = postprocess_models.find_conditional_bounds(Path(tmp))
        # The malformed branch is dropped; the well-formed sibling survives.
        self.assertEqual(found, {"Total": [self.RULES[1]]})
        self.assertIn("unsupported", stderr.getvalue())

    def test_schema_scan_recognizes_const_pinning(self):
        """A `then.properties.<field>.const` pin is dropped today: describe()
        only accepts the four numeric bound keywords in _BOUND_KEYWORDS, so
        `set(constraint) - set(_BOUND_KEYWORDS)` is non-empty for a bare
        `{"const": ...}` constraint and the whole rule returns None. This
        mirrors unit.json exactly: when unit is C62, scale must be exactly
        0. The branch carries no title of its own (unlike the JWK case
        below), isolating bug (b) from bug (c).
        """
        schema = {
            "title": "Unit",
            "type": "object",
            "properties": {
                "unit": {"type": "string"},
                "scale": {"type": "integer"},
            },
            "allOf": [
                {
                    "if": {
                        "properties": {"unit": {"const": "C62"}},
                        "required": ["unit"],
                    },
                    "then": {"properties": {"scale": {"const": 0}}},
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "unit.json").write_text(json.dumps(schema))
            found = postprocess_models.find_conditional_bounds(Path(tmp))
        self.assertEqual(
            found,
            {
                "Unit": [
                    {
                        "discriminator": "unit",
                        "values": ["C62"],
                        "bounds": {"scale": {"const": 0}},
                    }
                ]
            },
        )

    def test_schema_scan_recognizes_const_pinning_in_titled_allof_branch(
        self,
    ):
        """profile.json's JWK curve/algorithm pairing rules combine both
        gaps at once: `then.properties.alg.const` (bug b, see above) inside
        a branch that carries its own documentation `title` (bug c, see
        ConditionalRequiredInjectorTest) which must not overwrite the
        enclosing `JwkPublicKey` class name.
        """
        schema = {
            "$defs": {
                "jwk_public_key": {
                    "type": "object",
                    "required": ["kid", "kty"],
                    "properties": {
                        "kid": {"type": "string"},
                        "kty": {"type": "string"},
                        "crv": {"type": "string"},
                        "alg": {"type": "string"},
                    },
                    "allOf": [
                        {
                            "title": "P-256 pairs with ES256",
                            "if": {
                                "properties": {"crv": {"const": "P-256"}},
                                "required": ["crv"],
                            },
                            "then": {"properties": {"alg": {"const": "ES256"}}},
                        }
                    ],
                }
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "profile.json").write_text(json.dumps(schema))
            found = postprocess_models.find_conditional_bounds(Path(tmp))
        self.assertEqual(
            found,
            {
                "JwkPublicKey": [
                    {
                        "discriminator": "crv",
                        "values": ["P-256"],
                        "bounds": {"alg": {"const": "ES256"}},
                    }
                ]
            },
        )

    def test_injection_is_idempotent(self):
        once = postprocess_models.inject_conditional_bounds(
            self.MODULE, "Total", self.RULES
        )
        twice = postprocess_models.inject_conditional_bounds(
            once, "Total", self.RULES
        )
        self.assertEqual(once, twice)

    def test_injected_validator_enforces_const_pinning(self):
        module = (
            "from __future__ import annotations\n"
            "\n"
            "from pydantic import BaseModel, ConfigDict\n"
            "\n"
            "\n"
            "class Unit(BaseModel):\n"
            '    model_config = ConfigDict(extra="allow")\n'
            "    unit: str\n"
            "    scale: int | None = 0\n"
        )
        rules = [
            {
                "discriminator": "unit",
                "values": ["C62"],
                "bounds": {"scale": {"const": 0}},
            }
        ]
        out = postprocess_models.inject_conditional_bounds(
            module, "Unit", rules
        )
        namespace: dict = {}
        exec(compile(out, "<injected>", "exec"), namespace)  # noqa: S102
        unit = namespace["Unit"]
        with self.assertRaises(ValidationError):
            unit(unit="C62", scale=5)
        unit(unit="C62", scale=0)
        unit(unit="C62")
        # A unit outside the pinned vocabulary is unconstrained.
        unit(unit="KGM", scale=3)

    def test_injected_validator_enforces_conditional_bounds(self):
        out = postprocess_models.inject_conditional_bounds(
            self.MODULE, "Total", self.RULES
        )
        namespace: dict = {}
        exec(compile(out, "<injected>", "exec"), namespace)  # noqa: S102
        total = namespace["Total"]
        with self.assertRaises(ValidationError):
            total(type="discount", amount=500)
        with self.assertRaises(ValidationError):
            total(type="tax", amount=-1)
        total(type="discount", amount=-500)
        total(type="tax", amount=0)
        # A type carrying no rule is unconstrained (the vocabulary is open).
        total(type="total", amount=-5)


class ConditionalArrayRetypingInjectorTest(unittest.TestCase):
    """A discriminator retyping an array property's items to a different
    referenced schema file is a third if/then shape, distinct from
    conditional-required and conditional-bounds above. This mirrors
    fulfillment_method.json: the base `destinations` property is typed via
    `items.$ref` to fulfillment_destination.json, but a `shipping` method's
    destinations should really be shipping_destination.json items (postal
    address fields, `type` const `shipping_address`) and a `pickup`
    method's should really be location_destination.json items (`type`
    const `business_location`). The generator drops both retyping branches
    entirely -- no scanner in this module ever looked for this shape.
    """

    MODULE = (
        "from __future__ import annotations\n"
        "\n"
        "from pydantic import BaseModel, ConfigDict\n"
        "\n"
        "from . import destination\n"
        "\n"
        "\n"
        "class Method(BaseModel):\n"
        '    model_config = ConfigDict(extra="allow")\n'
        "    type: str\n"
        "    destinations: list[destination.Destination] | None = None\n"
    )
    RULES = [
        {
            "discriminator": "type",
            "values": ["shipping"],
            "field": "destinations",
            "required": ["id", "type"],
            "consts": {"type": "shipping_address"},
        },
        {
            "discriminator": "type",
            "values": ["pickup"],
            "field": "destinations",
            "required": ["type"],
            "consts": {"type": "business_location"},
        },
    ]

    def _schema(self):
        return {
            "title": "Method",
            "type": "object",
            "properties": {
                "type": {"type": "string"},
                "destinations": {
                    "type": "array",
                    "items": {"$ref": "destination.json"},
                },
            },
            "allOf": [
                {
                    "if": {
                        "properties": {"type": {"const": "shipping"}},
                        "required": ["type"],
                    },
                    "then": {
                        "properties": {
                            "destinations": {
                                "type": "array",
                                "items": {"$ref": "shipping_destination.json"},
                            }
                        }
                    },
                },
                {
                    "if": {
                        "properties": {"type": {"const": "pickup"}},
                        "required": ["type"],
                    },
                    "then": {
                        "properties": {
                            "destinations": {
                                "type": "array",
                                "items": {"$ref": "location_destination.json"},
                            }
                        }
                    },
                },
            ],
        }

    def _write_schema_tree(self, tmp):
        Path(tmp, "method.json").write_text(json.dumps(self._schema()))
        Path(tmp, "destination.json").write_text(
            json.dumps(
                {
                    "title": "Destination",
                    "type": "object",
                    "required": ["id", "type"],
                    "properties": {
                        "id": {"type": "string"},
                        "type": {"type": "string"},
                    },
                }
            )
        )
        Path(tmp, "shipping_destination.json").write_text(
            json.dumps(
                {
                    "title": "Shipping Destination",
                    "type": "object",
                    "required": ["id", "type"],
                    "properties": {
                        "id": {"type": "string"},
                        "type": {"type": "string", "const": "shipping_address"},
                    },
                    "allOf": [{"$ref": "postal_address.json"}],
                }
            )
        )
        Path(tmp, "location_destination.json").write_text(
            json.dumps(
                {
                    "title": "Business Location Destination",
                    "type": "object",
                    "required": ["type"],
                    "properties": {
                        "type": {"type": "string", "const": "business_location"}
                    },
                    "allOf": [{"$ref": "location_summary.json"}],
                }
            )
        )

    def test_schema_scan_reads_both_retyping_branches(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_schema_tree(tmp)
            found = postprocess_models.find_conditional_array_retyping(
                Path(tmp)
            )
        self.assertEqual(found, {"Method": self.RULES})

    def test_schema_scan_ignores_branch_matching_the_base_ref(self):
        # A then.properties.<field>.items.$ref identical to the base ref is
        # not a retype -- nothing to approximate. The sibling pickup branch
        # (still a genuine retype) is unaffected.
        schema = self._schema()
        schema["allOf"][0]["then"]["properties"]["destinations"]["items"][
            "$ref"
        ] = "destination.json"
        with tempfile.TemporaryDirectory() as tmp:
            self._write_schema_tree(tmp)
            Path(tmp, "method.json").write_text(json.dumps(schema))
            found = postprocess_models.find_conditional_array_retyping(
                Path(tmp)
            )
        self.assertEqual(found, {"Method": [self.RULES[1]]})

    def test_schema_scan_skips_rule_whose_field_was_stripped(self):
        # A request variant that omits `destinations` entirely (as
        # fulfillment_method_create_request.json does) makes the rule
        # inapplicable, not malformed -- no warning, and no rule recorded
        # for the variant's own class.
        schema = self._schema()
        schema["title"] = "Method Create Request"
        del schema["properties"]["destinations"]
        with tempfile.TemporaryDirectory() as tmp:
            self._write_schema_tree(tmp)
            Path(tmp, "method_create_request.json").write_text(
                json.dumps(schema)
            )
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                found = postprocess_models.find_conditional_array_retyping(
                    Path(tmp)
                )
        self.assertNotIn("MethodCreateRequest", found)
        self.assertEqual(found, {"Method": self.RULES})
        self.assertNotIn("unsupported", stderr.getvalue())

    def test_schema_scan_warns_when_retyped_ref_cannot_be_loaded(self):
        schema = self._schema()
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "method.json").write_text(json.dumps(schema))
            Path(tmp, "destination.json").write_text(
                json.dumps({"title": "Destination", "type": "object"})
            )
            # shipping_destination.json / location_destination.json are
            # deliberately absent.
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                found = postprocess_models.find_conditional_array_retyping(
                    Path(tmp)
                )
        self.assertEqual(found, {})
        self.assertIn("could not be loaded", stderr.getvalue())

    def test_injection_is_idempotent(self):
        once = postprocess_models.inject_conditional_array_retyping(
            self.MODULE, "Method", self.RULES
        )
        twice = postprocess_models.inject_conditional_array_retyping(
            once, "Method", self.RULES
        )
        self.assertEqual(once, twice)

    def test_injected_validator_enforces_retyping(self):
        module = (
            "from __future__ import annotations\n"
            "\n"
            "from pydantic import BaseModel, ConfigDict\n"
            "\n"
            "\n"
            "class Destination(BaseModel):\n"
            '    model_config = ConfigDict(extra="allow")\n'
            "    type: str\n"
            "    id: str\n"
            "\n"
            "\n"
            "class Method(BaseModel):\n"
            '    model_config = ConfigDict(extra="allow")\n'
            "    type: str\n"
            "    destinations: list[Destination] | None = None\n"
        )
        out = postprocess_models.inject_conditional_array_retyping(
            module, "Method", self.RULES
        )
        namespace: dict = {}
        exec(compile(out, "<injected>", "exec"), namespace)  # noqa: S102
        # Forward reference (from __future__ import annotations): Method's
        # "destinations: list[Destination]" annotation resolves once both
        # classes exist in the exec'd namespace.
        namespace["Method"].model_rebuild(_types_namespace=namespace)
        method_cls = namespace["Method"]
        destination_cls = namespace["Destination"]
        with self.assertRaises(ValidationError):
            method_cls(
                type="shipping",
                destinations=[
                    destination_cls(type="business_location", id="d1")
                ],
            )
        with self.assertRaises(ValidationError):
            method_cls(
                type="pickup",
                destinations=[
                    destination_cls(type="shipping_address", id="d1")
                ],
            )
        method_cls(
            type="shipping",
            destinations=[destination_cls(type="shipping_address", id="d1")],
        )
        method_cls(
            type="pickup",
            destinations=[destination_cls(type="business_location", id="d1")],
        )
        # A type carrying no rule is unconstrained (open vocabulary).
        method_cls(
            type="courier",
            destinations=[destination_cls(type="anything", id="d1")],
        )
        # No destinations at all is unconstrained regardless of type.
        method_cls(type="shipping")

    def test_patch_skips_reified_type_alias_union(self):
        union_source = (
            "from __future__ import annotations\n\n"
            "from typing import Annotated\n"
            "from typing_extensions import TypeAliasType\n\n"
            'Method = TypeAliasType("Method", Annotated[str, None])\n'
        )
        with tempfile.TemporaryDirectory() as tmp:
            schema_dir = Path(tmp) / "schemas"
            out_dir = Path(tmp) / "out"
            schema_dir.mkdir()
            out_dir.mkdir()
            self._write_schema_tree(schema_dir)
            out_file = out_dir / "__init__.py"
            out_file.write_text(union_source, encoding="utf-8")
            old_schema_dir = postprocess_models.SCHEMA_DIR
            old_out_dir = postprocess_models.OUTPUT_DIR
            try:
                postprocess_models.SCHEMA_DIR = schema_dir
                postprocess_models.OUTPUT_DIR = out_dir
                with contextlib.redirect_stdout(io.StringIO()):
                    patched, rc = (
                        postprocess_models._patch_conditional_array_retyping()
                    )
            finally:
                postprocess_models.SCHEMA_DIR = old_schema_dir
                postprocess_models.OUTPUT_DIR = old_out_dir
            self.assertEqual(patched, 0)
            self.assertEqual(rc, 0)
            self.assertEqual(out_file.read_text(encoding="utf-8"), union_source)


class OpenUnionNotEnumInjectorTest(unittest.TestCase):
    """Tests for open-union <Union>Base not.enum discriminator enforcement."""

    SOURCE = (
        "from __future__ import annotations\n\n"
        "from typing import Annotated, Literal\n"
        "from pydantic import BaseModel, ConfigDict, Field\n"
        "from typing_extensions import TypeAliasType\n\n\n"
        "class ShippingDestination(BaseModel):\n"
        '    model_config = ConfigDict(extra="allow")\n'
        '    type: Literal["shipping_address"]\n'
        "    id: str\n\n\n"
        "class LocationDestination(BaseModel):\n"
        '    model_config = ConfigDict(extra="allow")\n'
        '    type: Literal["business_location"]\n'
        "    id: str\n\n\n"
        "class FulfillmentDestinationBase(BaseModel):\n"
        '    model_config = ConfigDict(extra="allow")\n'
        "    type: str\n"
        "    id: str\n\n\n"
        "FulfillmentDestination = TypeAliasType(\n"
        '    "FulfillmentDestination",\n'
        "    Annotated[\n"
        "        LocationDestination | ShippingDestination | FulfillmentDestinationBase,\n"
        '        Field(..., title="FulfillmentDestination"),\n'
        "    ],\n"
        ")\n"
    )

    def test_discovers_open_union_base_exclusions(self):
        exclusions = postprocess_models._find_open_union_base_exclusions(
            self.SOURCE
        )
        self.assertEqual(
            exclusions,
            {
                "FulfillmentDestinationBase": {
                    "field": "type",
                    "excluded": ["business_location", "shipping_address"],
                }
            },
        )

    def test_injection_is_idempotent(self):
        once = postprocess_models.inject_open_union_not_enum(
            self.SOURCE,
            "FulfillmentDestinationBase",
            "type",
            ["business_location", "shipping_address"],
        )
        twice = postprocess_models.inject_open_union_not_enum(
            once,
            "FulfillmentDestinationBase",
            "type",
            ["business_location", "shipping_address"],
        )
        self.assertEqual(once, twice)
        self.assertIn("def _enforce_not_enum(", once)


class DependentRequiredInjectorTest(unittest.TestCase):
    """The dependentRequired post-generation injector's behavior."""

    SCHEMA = {
        "title": "Time Interval",
        "type": "object",
        "properties": {
            "opens": {"type": "string"},
            "closes": {"type": "string"},
        },
        "dependentRequired": {
            "opens": ["closes"],
            "closes": ["opens"],
        },
    }

    MODULE = (
        "from __future__ import annotations\n"
        "\n"
        "from pydantic import BaseModel, ConfigDict\n"
        "\n"
        "\n"
        "class TimeInterval(BaseModel):\n"
        '    model_config = ConfigDict(extra="allow")\n'
        "    opens: str | None = None\n"
        "    closes: str | None = None\n"
    )

    def test_schema_scan_finds_root_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "time_interval.json").write_text(
                json.dumps(self.SCHEMA), encoding="utf-8"
            )
            found = postprocess_models.find_root_dependent_required(Path(tmp))
        self.assertEqual(
            found,
            {
                "TimeInterval": {
                    "opens": ["closes"],
                    "closes": ["opens"],
                }
            },
        )

    def test_schema_scan_filters_rules_outside_declared_properties(self):
        schema = copy.deepcopy(self.SCHEMA)
        schema["dependentRequired"]["opens"] = ["timezone"]
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "time_interval.json").write_text(
                json.dumps(schema), encoding="utf-8"
            )
            found = postprocess_models.find_root_dependent_required(Path(tmp))
        self.assertEqual(found, {"TimeInterval": {"closes": ["opens"]}})

    def test_injection_is_idempotent(self):
        rules = {"opens": ["closes"], "closes": ["opens"]}
        once = postprocess_models.inject_dependent_required(
            self.MODULE, "TimeInterval", rules
        )
        twice = postprocess_models.inject_dependent_required(
            once, "TimeInterval", rules
        )
        self.assertEqual(once, twice)

    def test_injection_skips_rules_for_projected_out_fields(self):
        projected = self.MODULE.replace("    closes: str | None = None\n", "")
        out = postprocess_models.inject_dependent_required(
            projected,
            "TimeInterval",
            {"opens": ["closes"], "closes": ["opens"]},
        )
        self.assertEqual(out, projected)

    def test_injected_validator_uses_property_presence(self):
        rules = {"opens": ["closes"], "closes": ["opens"]}
        out = postprocess_models.inject_dependent_required(
            self.MODULE, "TimeInterval", rules
        )
        namespace: dict = {}
        exec(compile(out, "<injected>", "exec"), namespace)  # noqa: S102
        interval = namespace["TimeInterval"]

        interval()
        interval(opens="09:00", closes="17:00")
        interval(opens=None, closes=None)
        with self.assertRaisesRegex(ValidationError, "dependentRequired"):
            interval(opens="09:00")
        with self.assertRaisesRegex(ValidationError, "dependentRequired"):
            interval(closes="17:00")
        with self.assertRaisesRegex(ValidationError, "dependentRequired"):
            interval(opens=None)


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


class MaxPropertiesInjectorTest(unittest.TestCase):
    """maxProperties is the symmetric twin of minProperties (see #49/#55),
    but only minProperties was ever scanned: find_root_min_properties reads
    schema.get("minProperties") and there is no find_root_max_properties at
    all, so location_serves.json's maxProperties: 1 -- "the Platform MUST
    supply exactly one target form" -- is silently dropped. This mirrors
    InjectorTest above one for one, for the max side.
    """

    SCHEMA = {
        "title": "Sample",
        "type": "object",
        "maxProperties": 1,
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

    def test_injects_validator_with_declared_maximum(self):
        out = postprocess_models.inject_max_properties(self.MODULE, "Sample", 1)
        self.assertIn("model_validator", out)
        self.assertIn("at most 1", out.lower())

    def test_injected_validator_enforces_count(self):
        out = postprocess_models.inject_max_properties(self.MODULE, "Sample", 1)
        namespace: dict = {}
        exec(compile(out, "<injected>", "exec"), namespace)  # noqa: S102
        sample_cls = namespace["Sample"]
        with self.assertRaises(ValidationError):
            sample_cls(a="one", b="two")
        sample_cls(a="only-one")
        sample_cls()

    def test_injection_is_idempotent(self):
        once = postprocess_models.inject_max_properties(
            self.MODULE, "Sample", 1
        )
        twice = postprocess_models.inject_max_properties(once, "Sample", 1)
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
            found = postprocess_models.find_root_max_properties(Path(tmp))
        self.assertEqual(found, {"Sample": 1})

    def test_schema_scan_ignores_object_without_declared_properties(self):
        # Mirrors find_root_min_properties: maxProperties on a free-form
        # object property (no named properties) is already handled natively
        # by the generator (Field(max_length=...) on the dict field), so a
        # bare maxProperties with no properties is out of scope here.
        schema = {
            "title": "OpenMap",
            "type": "object",
            "maxProperties": 3,
        }
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "open_map.json").write_text(json.dumps(schema))
            found = postprocess_models.find_root_max_properties(Path(tmp))
        self.assertEqual(found, {})

    def test_both_bounds_coexist_on_the_same_class(self):
        """location_serves.json declares both minProperties: 1 AND
        maxProperties: 1 on the same object; both validators must be
        injectable into the same class without clobbering each other."""
        module = postprocess_models.inject_min_properties(
            self.MODULE, "Sample", 1
        )
        module = postprocess_models.inject_max_properties(module, "Sample", 1)
        self.assertIn("_enforce_min_properties", module)
        self.assertIn("_enforce_max_properties", module)
        namespace: dict = {}
        exec(compile(module, "<injected>", "exec"), namespace)  # noqa: S102
        sample_cls = namespace["Sample"]
        with self.assertRaises(ValidationError):
            sample_cls()
        with self.assertRaises(ValidationError):
            sample_cls(a="one", b="two")
        sample_cls(a="only-one")


class LocationServesMaxPropertiesSemanticTest(unittest.TestCase):
    """location_serves.json: "The Platform MUST supply exactly one target
    form" -- minProperties: 1 AND maxProperties: 1 together. Only the
    minimum was ever enforced (see MaxPropertiesInjectorTest above), so a
    map naming both point and address currently validates in violation of
    the schema.
    """

    def _location_serves(self):
        from ucp_sdk.models import LocationServes

        return LocationServes

    def _geo(self):
        from ucp_sdk.models import Geo

        return Geo

    def _address(self):
        from ucp_sdk.models import LocationServesAddressCountry

        return LocationServesAddressCountry

    def test_both_point_and_address_rejected(self):
        with self.assertRaises(ValidationError):
            self._location_serves()(
                point=self._geo()(latitude=1.0, longitude=2.0),
                address=self._address()(address_country="US"),
            )

    def test_point_only_accepted(self):
        location = self._location_serves()(
            point=self._geo()(latitude=1.0, longitude=2.0)
        )
        self.assertIsNotNone(location.point)

    def test_address_only_accepted(self):
        location = self._location_serves()(
            address=self._address()(address_country="US")
        )
        self.assertIsNotNone(location.address)

    def test_empty_still_rejected_by_the_existing_minimum(self):
        # Unaffected by this fix; confirms minProperties: 1 still holds.
        with self.assertRaises(ValidationError):
            self._location_serves()()

    def test_extension_key_alongside_point_rejected(self):
        # extra="allow": an extension form key still counts toward the
        # maxProperties=1 total per JSON Schema's key-counting semantics.
        with self.assertRaises(ValidationError):
            self._location_serves().model_validate(
                {
                    "point": {"latitude": 1.0, "longitude": 2.0},
                    "dev.example.custom_target": {"foo": "bar"},
                }
            )


class TotalsContainsTest(unittest.TestCase):
    """totals.json requires exactly one ``subtotal`` AND one ``total`` entry.

    Both rules live as two ``allOf`` ``contains`` branches; the generator drops
    them, leaving ``Totals`` a bare ``list[TotalsItem]``. The post-generation
    injector reads the pristine schema and restores BOTH bounds as an
    ``AfterValidator`` on the alias.
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

    def test_phantom_request_slices_not_emitted(self):
        import ucp_sdk.models as models_mod

        for name in (
            "TotalCreateRequest",
            "TotalUpdateRequest",
            "TotalsCreateRequest",
            "TotalsUpdateRequest",
            "OrderCreateRequest",
            "OrderUpdateRequest",
        ):
            self.assertFalse(
                hasattr(models_mod, name),
                f"{name} should not be emitted for response-only/unannotated schemas",
            )

    def test_totals_item_enforces_conditional_bounds(self):
        base = [self.SUBTOTAL, self.TOTAL]
        adapter = TypeAdapter(Totals)
        with self.assertRaises(ValidationError):
            adapter.validate_python(base + [{"type": "discount", "amount": 50}])
        with self.assertRaises(ValidationError):
            adapter.validate_python(base + [{"type": "tax", "amount": -10}])
        adapter.validate_python(base + [{"type": "discount", "amount": -50}])
        adapter.validate_python(base + [{"type": "tax", "amount": 10}])

    def test_custom_type_requires_display_text(self):
        base = [self.SUBTOTAL, self.TOTAL]
        adapter = TypeAdapter(Totals)
        with self.assertRaisesRegex(ValidationError, "display_text"):
            adapter.validate_python(base + [{"type": "surcharge", "amount": 5}])
        adapter.validate_python(base + [{"type": "tax", "amount": 5}])
        adapter.validate_python(
            base
            + [
                {
                    "type": "surcharge",
                    "amount": 5,
                    "display_text": "Surcharge",
                }
            ]
        )

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

    #: The same alias, but formatted the way ruff/black renders it once the
    #: item type name is long enough to force line-wrapping (e.g. once a
    #: request-variant $ref like ``total_create_request.TotalCreateRequest``
    #: replaces the short ``Total`` reference). The trailing comma after
    #: ``Field(...)`` before the closing ``]`` is the shape that matters here.
    MODULE_LINE_WRAPPED = (
        "from __future__ import annotations\n"
        "\n"
        "from typing import Annotated\n"
        "\n"
        "from pydantic import Field\n"
        "from typing_extensions import TypeAliasType\n"
        "\n"
        "from . import total_create_request\n"
        "\n"
        "\n"
        "TotalsCreateRequest = TypeAliasType(\n"
        '    "TotalsCreateRequest",\n'
        "    Annotated[\n"
        "        list[total_create_request.TotalCreateRequest],\n"
        '        Field(..., title="Totals Create Request"),\n'
        "    ],\n"
        ")\n"
    )

    #: subtotal AND total, mirroring the real totals.json.
    GROUPS = [
        {"pairs": [("type", "subtotal")], "min": 1, "max": 1},
        {"pairs": [("type", "total")], "min": 1, "max": 1},
    ]

    ITEM_CONDITION = {
        "field": "type",
        "excluded": ["subtotal", "total"],
        "required": ["display_text"],
    }

    def test_scan_reads_both_contains_from_allof_branches(self):
        # The pristine totals.json shape: two allOf contains branches.
        schema = {
            "title": "Totals",
            "type": "array",
            "items": {
                "allOf": [
                    {
                        "if": {
                            "properties": {
                                "type": {"not": {"enum": ["subtotal", "total"]}}
                            },
                            "required": ["type"],
                        },
                        "then": {"required": ["display_text"]},
                    }
                ]
            },
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
        self.assertEqual(
            found["totals"]["item_condition"],
            self.ITEM_CONDITION,
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

    def test_injects_cleanly_when_annotated_is_line_wrapped(self):
        """A line-wrapped Annotated[...] with a trailing comma before the
        closing bracket must still parse (see #34/#35: a longer item-type
        reference, such as a request-variant $ref, pushes the formatter to
        wrap the annotation onto multiple lines with a trailing comma; a
        naive "insert before the closing bracket" splice then lands after
        that comma and produces "Field(...),\\n, AfterValidator(...)]" -
        two commas with nothing between them, a SyntaxError).
        """
        out = postprocess_models.inject_array_contains(
            self.MODULE_LINE_WRAPPED, "TotalsCreateRequest", self.GROUPS
        )

        # The regression: this must be syntactically valid Python.
        ast.parse(out)

        self.assertIn(
            "AfterValidator(_enforce_contains_totals_create_request)", out
        )
        # No orphaned comma left behind by the splice.
        self.assertNotRegex(out, r",\s*,")

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

    def test_injected_validator_requires_custom_display_text(self):
        out = postprocess_models.inject_array_contains(
            self.MODULE, "Totals", self.GROUPS, self.ITEM_CONDITION
        )
        namespace: dict = {}
        exec(compile(out, "<injected>", "exec"), namespace)  # noqa: S102
        adapter = TypeAdapter(namespace["Totals"])
        base = [
            {"type": "subtotal", "amount": 1},
            {"type": "total", "amount": 1},
        ]
        with self.assertRaisesRegex(ValidationError, "display_text"):
            adapter.validate_python(base + [{"type": "surcharge", "amount": 1}])
        adapter.validate_python(
            base
            + [
                {
                    "type": "surcharge",
                    "amount": 1,
                    "display_text": "Surcharge",
                }
            ]
        )


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


class UniqueItemsSemanticTest(unittest.TestCase):
    """Committed models enforce uniqueItems on declared array fields."""

    def test_context_eligibility_rejects_duplicates(self) -> None:
        """Context.eligibility rejects duplicate reverse-domain entries."""
        from ucp_sdk.models import Context

        with self.assertRaisesRegex(ValidationError, "[Uu]nique"):
            Context(
                eligibility=[
                    "com.example.loyalty_gold",
                    "com.example.loyalty_gold",
                ]
            )

    def test_context_eligibility_accepts_unique_and_none(self) -> None:
        """Unique lists and missing values are accepted."""
        from ucp_sdk.models import Context

        ctx = Context(
            eligibility=["com.example.loyalty_gold", "org.school.student"]
        )
        self.assertEqual(
            ctx.eligibility,
            ["com.example.loyalty_gold", "org.school.student"],
        )
        self.assertIsNone(Context().eligibility)

    def test_constraint_expression_required_rejects_duplicates(self) -> None:
        """ConstraintExpression.required rejects duplicate property names."""
        from ucp_sdk.models import ConstraintExpression

        with self.assertRaisesRegex(ValidationError, "[Uu]nique"):
            ConstraintExpression(required=["id", "id"])


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


class AdditionalPropertiesForbidSemanticTest(unittest.TestCase):
    """Committed models reject unknown keys on additionalProperties:false."""

    def test_error_response_rejects_unknown_keys(self) -> None:
        from ucp_sdk.models import ErrorResponse

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
        from ucp_sdk.models import ErrorResponse

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


class EntityVersionValidationSemanticTest(unittest.TestCase):
    """Committed entity-derived models enforce version pattern validation."""

    def test_capability_base_accepts_valid_version(self) -> None:
        from ucp_sdk.models import CapabilityBase

        model = CapabilityBase.model_validate(
            {"version": "2026-04-08", "id": "test"}
        )
        self.assertEqual(model.version, "2026-04-08")

    def test_capability_base_rejects_invalid_version(self) -> None:
        from ucp_sdk.models import CapabilityBase

        with self.assertRaises(ValidationError):
            CapabilityBase.model_validate(
                {"version": "not-a-version", "id": "test"}
            )

    def test_service_base_rejects_invalid_version(self) -> None:
        from ucp_sdk.models import ServiceBase

        with self.assertRaises(ValidationError):
            ServiceBase.model_validate({"version": "invalid-format"})

    def test_payment_handler_base_rejects_invalid_version(self) -> None:
        from ucp_sdk.models import PaymentHandlerBase

        with self.assertRaises(ValidationError):
            PaymentHandlerBase.model_validate({"version": {"not": "a version"}})


class JwkConditionalRulesSemanticTest(unittest.TestCase):
    """profile.json's jwk_public_key carries five if/then rules, all five
    dropped by the generator today: two conditional-required rules (an EC
    key needs crv/x/y, an OKP key needs crv/x) and three conditional
    const-pin rules pairing a curve with its algorithm (P-256/ES256,
    P-384/ES384, Ed25519/EdDSA). Security-adjacent: a profile publishing an
    EC key with no curve, or an algorithm that does not match its curve,
    currently passes SDK validation and would only fail (or silently
    misverify) downstream at signature-verification time.
    """

    def _jwk(self):
        from ucp_sdk.models import JwkPublicKey

        return JwkPublicKey

    def test_ec_key_without_curve_and_coordinates_rejected(self):
        with self.assertRaises(ValidationError):
            self._jwk()(kid="k1", kty="EC")

    def test_ec_key_with_curve_and_coordinates_accepted(self):
        key = self._jwk()(
            kid="k1", kty="EC", crv="P-256", x="AA", y="BB", alg="ES256"
        )
        self.assertEqual(key.crv, "P-256")

    def test_okp_key_without_curve_rejected(self):
        with self.assertRaises(ValidationError):
            self._jwk()(kid="k2", kty="OKP")

    def test_okp_key_with_curve_accepted(self):
        key = self._jwk()(kid="k2", kty="OKP", crv="Ed25519", x="AA")
        self.assertEqual(key.crv, "Ed25519")

    def test_p256_with_mismatched_algorithm_rejected(self):
        with self.assertRaises(ValidationError):
            self._jwk()(
                kid="k3", kty="EC", crv="P-256", x="AA", y="BB", alg="EdDSA"
            )

    def test_p256_with_matching_algorithm_accepted(self):
        self._jwk()(
            kid="k3", kty="EC", crv="P-256", x="AA", y="BB", alg="ES256"
        )

    def test_p384_with_mismatched_algorithm_rejected(self):
        with self.assertRaises(ValidationError):
            self._jwk()(
                kid="k4", kty="EC", crv="P-384", x="AA", y="BB", alg="ES256"
            )

    def test_p384_with_matching_algorithm_accepted(self):
        self._jwk()(
            kid="k4", kty="EC", crv="P-384", x="AA", y="BB", alg="ES384"
        )

    def test_ed25519_with_mismatched_algorithm_rejected(self):
        with self.assertRaises(ValidationError):
            self._jwk()(kid="k5", kty="OKP", crv="Ed25519", x="AA", alg="ES256")

    def test_ed25519_with_matching_algorithm_accepted(self):
        self._jwk()(kid="k5", kty="OKP", crv="Ed25519", x="AA", alg="EdDSA")

    def test_algorithm_omitted_is_unconstrained(self):
        # alg is optional; verifiers derive it from crv when absent.
        self._jwk()(kid="k6", kty="EC", crv="P-256", x="AA", y="BB")

    def test_unrecognized_curve_is_unconstrained(self):
        # The crv/kty/alg vocabularies are open (see the schema
        # description); a curve outside the three well-known pairings
        # carries no algorithm rule.
        self._jwk()(
            kid="k7", kty="EC", crv="secp256k1", x="AA", y="BB", alg="ES256K"
        )


class UnitScaleSemanticTest(unittest.TestCase):
    """unit.json: when unit is C62, scale (if present) MUST be 0."""

    def _unit(self):
        from ucp_sdk.models import Unit

        return Unit

    def test_c62_with_nonzero_scale_rejected(self):
        with self.assertRaises(ValidationError):
            self._unit()(unit="C62", scale=5, display_text="pieces")

    def test_c62_with_zero_scale_accepted(self):
        unit = self._unit()(unit="C62", scale=0, display_text="pieces")
        self.assertEqual(unit.scale, 0)

    def test_c62_with_scale_omitted_defaults_to_zero(self):
        # scale defaults to 0, which already satisfies the C62 pin.
        unit = self._unit()(unit="C62", display_text="pieces")
        self.assertEqual(unit.scale, 0)

    def test_other_unit_with_nonzero_scale_accepted(self):
        # The pin is C62-specific; any other unit is unconstrained.
        unit = self._unit()(unit="GRM", scale=3, display_text="grams")
        self.assertEqual(unit.scale, 3)


class TimeIntervalDependentRequiredSemanticTest(unittest.TestCase):
    """TimeInterval requires opens and closes to be provided together."""

    def _interval(self):
        from ucp_sdk.models import TimeInterval

        return TimeInterval

    def _exception_hour(self):
        from ucp_sdk.models import ExceptionHour

        return ExceptionHour

    def test_single_opening_time_rejected(self):
        with self.assertRaisesRegex(ValidationError, "dependentRequired"):
            self._interval()(opens="09:00")

    def test_single_closing_time_rejected(self):
        with self.assertRaisesRegex(ValidationError, "dependentRequired"):
            self._interval()(closes="17:00")

    def test_empty_and_complete_intervals_accepted(self):
        self._interval()()
        interval = self._interval()(opens="09:00", closes="17:00")
        self.assertEqual((interval.opens, interval.closes), ("09:00", "17:00"))

    def test_explicit_null_still_counts_as_present(self):
        interval = self._interval()(opens=None, closes=None)
        self.assertEqual(interval.model_fields_set, {"opens", "closes"})
        with self.assertRaisesRegex(ValidationError, "dependentRequired"):
            self._interval()(opens=None)

    def test_inherited_interval_rule_is_enforced(self):
        with self.assertRaisesRegex(ValidationError, "dependentRequired"):
            self._exception_hour()(
                valid_from="2026-01-01",
                valid_through="2026-01-02",
                opens="09:00",
            )
        self._exception_hour()(
            valid_from="2026-01-01",
            valid_through="2026-01-02",
            opens="09:00",
            closes="17:00",
        )


class FulfillmentMethodDestinationRetypingSemanticTest(unittest.TestCase):
    """fulfillment_method.json retypes `destinations` per `type`: a
    `shipping` method's destinations are shipping_destination.json items
    (`type` const `shipping_address`), a `pickup` method's are
    location_destination.json items (`type` const `business_location`).
    `ucp-schema generate-types` reifies FulfillmentMethod into ShippingMethod,
    PickupMethod, and FulfillmentMethodBase (and directional request variants
    ShippingMethodCreateRequest, PickupMethodCreateRequest, etc.).
    """

    def _validate_method(self, data):
        from ucp_sdk.models import FulfillmentMethod

        return TypeAdapter(FulfillmentMethod).validate_python(data)

    def test_shipping_method_with_business_location_destination_rejected(
        self,
    ):
        with self.assertRaises(ValidationError):
            self._validate_method(
                {
                    "id": "m1",
                    "type": "shipping",
                    "line_item_ids": ["li1"],
                    "destinations": [{"type": "business_location", "id": "d1"}],
                }
            )

    def test_pickup_method_with_shipping_address_destination_rejected(self):
        with self.assertRaises(ValidationError):
            self._validate_method(
                {
                    "id": "m2",
                    "type": "pickup",
                    "line_item_ids": ["li1"],
                    "destinations": [{"type": "shipping_address", "id": "d1"}],
                }
            )

    def test_shipping_method_with_shipping_address_destination_accepted(
        self,
    ):
        from ucp_sdk.models import ShippingDestination, ShippingMethod

        method = self._validate_method(
            {
                "id": "m1",
                "type": "shipping",
                "line_item_ids": ["li1"],
                "destinations": [{"type": "shipping_address", "id": "d1"}],
            }
        )
        self.assertIsInstance(method, ShippingMethod)
        self.assertIsInstance(method.destinations[0], ShippingDestination)
        self.assertEqual(method.destinations[0].type, "shipping_address")

    def test_pickup_method_with_business_location_destination_accepted(self):
        from ucp_sdk.models import LocationDestination, PickupMethod

        method = self._validate_method(
            {
                "id": "m2",
                "type": "pickup",
                "line_item_ids": ["li1"],
                "destinations": [
                    {
                        "type": "business_location",
                        "id": "d1",
                        "name": "Store 1",
                    }
                ],
            }
        )
        self.assertIsInstance(method, PickupMethod)
        self.assertIsInstance(method.destinations[0], LocationDestination)
        self.assertEqual(method.destinations[0].type, "business_location")

    def test_method_type_outside_the_pinned_vocabulary_is_unconstrained(
        self,
    ):
        from ucp_sdk.models import (
            FulfillmentDestinationBase,
            FulfillmentMethodBase,
        )

        method = self._validate_method(
            {
                "id": "m3",
                "type": "curbside",
                "line_item_ids": ["li1"],
                "destinations": [{"type": "anything", "id": "d1"}],
            }
        )
        self.assertIsInstance(method, FulfillmentMethodBase)
        self.assertIsInstance(
            method.destinations[0], FulfillmentDestinationBase
        )

    def test_method_without_destinations_is_unconstrained(self):
        self._validate_method(
            {"id": "m4", "type": "shipping", "line_item_ids": ["li1"]}
        )

    def test_directional_create_request_variants_reify_destinations(self):
        from ucp_sdk.models import (
            FulfillmentMethodCreateRequest,
            PickupMethodCreateRequest,
            ShippingMethodCreateRequest,
        )

        adapter = TypeAdapter(FulfillmentMethodCreateRequest)
        shipping_req = adapter.validate_python(
            {
                "type": "shipping",
                "destinations": [
                    {"postal_code": "94043", "address_country": "US"}
                ],
            }
        )
        self.assertIsInstance(shipping_req, ShippingMethodCreateRequest)
        self.assertEqual(shipping_req.destinations[0].postal_code, "94043")

        pickup_req = adapter.validate_python(
            {"type": "pickup", "selected_destination_id": "loc_1"}
        )
        self.assertIsInstance(pickup_req, PickupMethodCreateRequest)
        self.assertNotIn("destinations", PickupMethodCreateRequest.model_fields)

    def test_directional_update_request_variants_require_type_discriminator(
        self,
    ):
        from ucp_sdk.models import (
            FulfillmentMethodUpdateRequest,
            FulfillmentMethodUpdateRequestBase,
            PickupMethodUpdateRequest,
            ShippingMethodUpdateRequest,
        )

        self.assertTrue(
            ShippingMethodUpdateRequest.model_fields["type"].is_required()
        )
        self.assertTrue(
            PickupMethodUpdateRequest.model_fields["type"].is_required()
        )
        with self.assertRaises(ValidationError):
            ShippingMethodUpdateRequest.model_validate(
                {"id": "m1", "line_item_ids": ["li_1"]}
            )
        with self.assertRaises(ValidationError):
            PickupMethodUpdateRequest.model_validate(
                {"id": "m2", "line_item_ids": ["li_1"]}
            )
        ship = ShippingMethodUpdateRequest.model_validate(
            {"id": "m1", "type": "shipping", "line_item_ids": ["li_1"]}
        )
        self.assertEqual(ship.type, "shipping")

        adapter = TypeAdapter(FulfillmentMethodUpdateRequest)
        typeless = adapter.validate_python(
            {
                "id": "m1",
                "line_item_ids": ["li_1"],
                "selected_destination_id": "d_1",
            }
        )
        self.assertIsInstance(typeless, FulfillmentMethodUpdateRequestBase)
        self.assertIsNone(typeless.type)

        with self.assertRaisesRegex(ValidationError, "dependentRequired"):
            adapter.validate_python(
                {
                    "id": "m1",
                    "line_item_ids": ["li_1"],
                    "destinations": [{"id": "d_1", "postal_code": "94043"}],
                }
            )


class SemanticModelNamingAndConstraintsTest(unittest.TestCase):
    """Tests for semantic inline/union model names and restored schema constraints."""

    def test_semantic_names_present_and_synthetic_wrapper_absent(self):
        import ucp_sdk.models as models_mod

        self.assertFalse(hasattr(models_mod, "UcpSchemaTypes"))
        self.assertFalse(hasattr(models_mod, "UCPSchemaTypes"))
        for expected in (
            "TotalsItem",
            "ValueConstraintEnum",
            "ValueConstraintConst",
            "ServiceBusinessSchemaRest",
            "ServiceBusinessSchemaMcp",
            "ServiceBusinessSchemaA2a",
            "ServiceBusinessSchemaEmbedded",
            "LocationServesAddressCountry",
            "LocationServesAddressRegion",
            "LocationServesAddressPostalCode",
            "UnitPriceMeasure",
            "UnitPriceReference",
        ):
            self.assertTrue(
                hasattr(models_mod, expected),
                f"Expected semantic model {expected} in ucp_sdk.models",
            )

    def test_value_constraint_and_oauth2_provider_enforce_unique_items(self):
        from ucp_sdk.models import (
            Oauth2Provider,
            Provider,
            TotalsItem,
            ValueConstraintConst,
            ValueConstraintEnum,
        )

        with self.assertRaisesRegex(ValidationError, "[Uu]nique"):
            ValueConstraintEnum(enum=["a", "a"])
        with self.assertRaisesRegex(ValidationError, "[Uu]nique"):
            ValueConstraintConst(const="x", enum=["a", "a"])
        ValueConstraintEnum(enum=["a", "b"])

        with self.assertRaisesRegex(ValidationError, "[Uu]nique"):
            Oauth2Provider(
                type="oauth2",
                auth_url="https://auth.example.com",
                required_claims=["email", "email"],
            )
        Oauth2Provider(
            type="oauth2",
            auth_url="https://auth.example.com",
            required_claims=["email", "sub"],
        )
        with self.assertRaises(ValidationError):
            TypeAdapter(Provider).validate_python({"type": "oauth2"})

        with self.assertRaisesRegex(ValidationError, "display_text"):
            TotalsItem(type="surcharge", amount=50)
        TotalsItem(type="surcharge", amount=50, display_text="Surcharge")

    def test_location_hours_and_exception_hours_require_timezone(self):
        from ucp_sdk.models import Location, LookupLocation

        for cls, extra in (
            (Location, {}),
            (LookupLocation, {"inputs": [{"id": "loc_1"}]}),
        ):
            with self.subTest(cls=cls.__name__):
                with self.assertRaisesRegex(
                    ValidationError, "dependentRequired"
                ):
                    cls.model_validate(
                        {
                            "id": "loc_1",
                            "name": "Store",
                            **extra,
                            "hours": [
                                {
                                    "day": "monday",
                                    "opens": "09:00",
                                    "closes": "17:00",
                                }
                            ],
                        }
                    )
                with self.assertRaisesRegex(
                    ValidationError, "dependentRequired"
                ):
                    cls.model_validate(
                        {
                            "id": "loc_1",
                            "name": "Store",
                            **extra,
                            "exception_hours": [
                                {
                                    "valid_from": "2026-01-01",
                                    "valid_through": "2026-01-01",
                                }
                            ],
                        }
                    )
                loc = cls.model_validate(
                    {
                        "id": "loc_1",
                        "name": "Store",
                        **extra,
                        "timezone": "America/New_York",
                        "hours": [
                            {
                                "day": "monday",
                                "opens": "09:00",
                                "closes": "17:00",
                            }
                        ],
                    }
                )
                self.assertEqual(loc.timezone, "America/New_York")

    def test_unit_price_measure_and_reference_enforce_c62_scale_zero(self):
        from ucp_sdk.models import (
            Measure,
            QuantityUnit,
            UnitPriceMeasure,
            UnitPriceReference,
        )

        for cls in (Measure, UnitPriceMeasure, UnitPriceReference):
            with self.subTest(cls=cls.__name__):
                with self.assertRaises(ValidationError):
                    cls(unit="C62", scale=2, display_text="each", value=1.0)
                obj = cls(unit="C62", scale=0, display_text="each", value=1.0)
                self.assertEqual(obj.scale, 0)

        with self.assertRaises(ValidationError):
            QuantityUnit(unit="C62", scale=2, display_text="each")
        qu = QuantityUnit(unit="C62", scale=0, display_text="each")
        self.assertEqual(qu.scale, 0)

    def test_jwk_public_key_rejects_forbidden_private_key_material(self):
        from ucp_sdk.models import JwkPublicKey

        for forbidden_key in ("d", "p", "q", "dp", "dq", "qi", "oth", "k"):
            with (
                self.subTest(forbidden_key=forbidden_key),
                self.assertRaisesRegex(ValidationError, "forbidden"),
            ):
                JwkPublicKey.model_validate(
                    {
                        "kid": "k1",
                        "kty": "OKP",
                        "crv": "Ed25519",
                        "x": "AA",
                        forbidden_key: "secret",
                    }
                )


class DatetimePatternInjectorTest(unittest.TestCase):
    """The date-time pattern post-generation rewrite's own behavior."""

    OFFSET = "(?:[Zz]|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])$"

    # Shaped like raw generator output for date-time strings with a pattern.
    MODULE = (
        "from __future__ import annotations\n"
        "\n"
        "from pydantic import AwareDatetime, BaseModel, ConfigDict, Field\n"
        "\n"
        "\n"
        "class Window(BaseModel):\n"
        '    """A window."""\n'
        "\n"
        "    model_config = ConfigDict(\n"
        '        extra="allow",\n'
        "    )\n"
        "    opens_at: AwareDatetime = Field(\n"
        f'        ..., pattern="{OFFSET}"\n'
        "    )\n"
        '    """When it opens."""\n'
        '    closes_at: AwareDatetime | None = Field(None, pattern="Z$")\n'
        '    starts_at: AwareDatetime = Field(..., alias="startsAt", pattern="Z$")\n'
        '    code: str = Field(..., pattern="^[A-Z]+$")\n'
    )

    ALIAS_MODULE = (
        "from __future__ import annotations\n"
        "\n"
        "from typing import Annotated\n"
        "\n"
        "from pydantic import AwareDatetime, Field\n"
        "from typing_extensions import TypeAliasType\n"
        "\n"
        "Stamp = TypeAliasType(\n"
        '    "Stamp", Annotated[AwareDatetime, Field(..., pattern="Z$")]\n'
        ")\n"
    )

    def test_finds_only_datetime_fields(self) -> None:
        """A pattern on a str field is valid pydantic and is left alone."""
        fields, unsupported = postprocess_models.find_datetime_pattern_fields(
            self.MODULE
        )
        self.assertEqual(
            fields,
            [
                ("Window", "opens_at", self.OFFSET),
                ("Window", "closes_at", "Z$"),
                ("Window", "starts_at", "Z$"),
            ],
        )
        self.assertEqual(unsupported, [])

    def test_rewrite_removes_pattern_and_injects_validators(self) -> None:
        out = postprocess_models.inject_datetime_patterns(self.MODULE)
        self.assertIn("    opens_at: AwareDatetime\n", out)
        self.assertIn("    closes_at: AwareDatetime | None = None\n", out)
        self.assertIn(
            "    starts_at: AwareDatetime = Field(..., alias='startsAt')\n",
            out,
        )
        self.assertIn('    code: str = Field(..., pattern="^[A-Z]+$")\n', out)
        self.assertIn('    """When it opens."""\n', out)
        for field in ("opens_at", "closes_at", "starts_at"):
            self.assertEqual(
                out.count(f"def _enforce_datetime_pattern_{field}("), 1
            )
        self.assertNotIn("_enforce_datetime_pattern_code", out)
        self.assertIn("field_validator", out)
        self.assertIn("\nimport re\n", out)
        self.assertEqual(
            postprocess_models.find_datetime_pattern_fields(out), ([], [])
        )

    def test_injection_is_idempotent(self) -> None:
        once = postprocess_models.inject_datetime_patterns(self.MODULE)
        twice = postprocess_models.inject_datetime_patterns(once)
        self.assertEqual(once, twice)

    def test_type_alias_pattern_is_reported_not_rewritten(self) -> None:
        self.assertEqual(
            postprocess_models.find_datetime_pattern_fields(self.ALIAS_MODULE),
            ([], [9]),
        )
        self.assertEqual(
            postprocess_models.inject_datetime_patterns(self.ALIAS_MODULE),
            self.ALIAS_MODULE,
        )

    def test_patch_rewrites_tree_and_fails_on_type_alias(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            module = Path(tmp) / "window.py"
            module.write_text(self.MODULE, encoding="utf-8")
            with (
                mock.patch.object(postprocess_models, "OUTPUT_DIR", Path(tmp)),
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(
                    postprocess_models._patch_datetime_patterns(), (1, 0)
                )
                self.assertEqual(
                    postprocess_models.find_datetime_pattern_fields(
                        module.read_text(encoding="utf-8")
                    ),
                    ([], []),
                )
                (Path(tmp) / "stamp.py").write_text(
                    self.ALIAS_MODULE, encoding="utf-8"
                )
                self.assertEqual(
                    postprocess_models._patch_datetime_patterns(), (0, 1)
                )

    def test_injected_validator_enforces_pattern(self) -> None:
        out = postprocess_models.inject_datetime_patterns(self.MODULE)
        namespace: dict = {}
        exec(compile(out, "<injected>", "exec"), namespace)  # noqa: S102
        window = namespace["Window"]
        window.model_rebuild(_types_namespace=namespace)
        common = {"startsAt": "2026-05-18T09:00:00Z", "code": "A"}
        for opens_at in ("2026-05-18T17:00:00Z", "2026-05-18T17:00:00-08:00"):
            window.model_validate({**common, "opens_at": opens_at})
        window.model_validate(
            {**common, "opens_at": "2026-05-18T17:00:00Z", "closes_at": None}
        )
        for opens_at in ("2026-05-18T17:00:00", "2026-05-18T17:00:00+0530"):
            with self.assertRaises(ValidationError):
                window.model_validate({**common, "opens_at": opens_at})


class LocationFilterOpenAtSemanticTest(unittest.TestCase):
    """location_filter.json's hours.open_at is a date-time whose pattern
    requires a `Z` or `+hh:mm`/`-hh:mm` offset. The generator put that
    pattern on the AwareDatetime field itself, so validating any Location
    Search or Lookup request carrying it raised TypeError ("Unable to apply
    constraint 'pattern'") instead of ValidationError.
    """

    def _requests(self):
        from ucp_sdk.models.schemas import (
            LocationLookupRequest,
            LocationSearchRequest,
        )

        return (
            (LocationSearchRequest, {}),
            (LocationLookupRequest, {"ids": ["loc_1"]}),
        )

    def _validate(self, model, body, open_at):
        return model.model_validate(
            {**body, "filters": {"hours": {"open_at": open_at}}}
        )

    def test_utc_and_numeric_offsets_accepted(self):
        for model, body in self._requests():
            for open_at, offset in (
                ("2026-05-18T17:00:00Z", 0),
                ("2026-05-18T17:00:00+05:30", 19800),
            ):
                with self.subTest(model=model.__name__, open_at=open_at):
                    request = self._validate(model, body, open_at)
                    self.assertEqual(
                        request.filters.hours.open_at.utcoffset().total_seconds(),
                        offset,
                    )

    def test_naive_datetime_rejected(self):
        for model, body in self._requests():
            with (
                self.subTest(model=model.__name__),
                self.assertRaises(ValidationError),
            ):
                self._validate(model, body, "2026-05-18T17:00:00")

    def test_offsets_outside_the_pattern_rejected(self):
        # AwareDatetime alone accepts both of these; the pattern does not.
        for model, body in self._requests():
            for open_at in ("2026-05-18T17:00:00+0530", "1747587600"):
                with (
                    self.subTest(model=model.__name__, open_at=open_at),
                    self.assertRaises(ValidationError),
                ):
                    self._validate(model, body, open_at)

    def test_no_committed_datetime_field_carries_a_pattern(self):
        import ucp_sdk.models.schemas as schemas

        for path in sorted(Path(schemas.__file__).parent.rglob("*.py")):
            with self.subTest(path=path.name):
                self.assertEqual(
                    postprocess_models.find_datetime_pattern_fields(
                        path.read_text(encoding="utf-8")
                    ),
                    ([], []),
                )


if __name__ == "__main__":
    unittest.main()
