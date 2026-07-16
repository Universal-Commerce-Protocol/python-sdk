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

"""minProperties enforcement on generated models (issue #49).

JSON Schema's ``minProperties`` counts the keys present on the object, so the
generated model must reject an instance with fewer provided properties —
including the no-argument case — while accepting any combination of declared
fields (explicit nulls are present keys) and extra fields (``extra="allow"``
models accept unknown keys, which count too).

The injector tests are dependency-free; the ``Description`` semantic tests
need the package importable (``pip install -e .``) and skip otherwise.
"""

import json
import tempfile
import unittest
from pathlib import Path

import postprocess_models

try:
    from pydantic import ValidationError

    from ucp_sdk.models.schemas.shopping.types.description import Description

    HAVE_SDK = True
except ImportError:  # pragma: no cover - exercised only without install
    HAVE_SDK = False


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
