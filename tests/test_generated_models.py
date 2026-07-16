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

"""Tests for properties of the committed generated models."""

import ast
import unittest
from pathlib import Path


class GeneratedModelReuseTest(unittest.TestCase):
    """Tests that code generation reuses structurally identical aliases."""

    def test_capability_extends_aliases_reuse_canonical_definitions(
        self,
    ) -> None:
        """Numbered aliases stay importable while sharing their definitions."""
        source_path = (
            Path(__file__).parents[1]
            / "src/ucp_sdk/models/schemas/capability.py"
        )
        tree = ast.parse(source_path.read_text())
        expected_targets = {
            "Extends2": "Extends",
            "Extends4": "Extends",
            "Extends6": "Extends",
            "Extends3Item": "Extends1Item",
            "Extends5Item": "Extends1Item",
            "Extends7Item": "Extends1Item",
        }

        alias_values = {}
        for node in tree.body:
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            target = node.targets[0]
            if not isinstance(target, ast.Name):
                continue
            if not isinstance(node.value, ast.Call):
                continue
            if not isinstance(node.value.func, ast.Name):
                continue
            if node.value.func.id != "TypeAliasType":
                continue
            alias_values[target.id] = node.value.args[1]

        for alias, canonical in expected_targets.items():
            with self.subTest(alias=alias):
                value = alias_values[alias]
                self.assertIsInstance(value, ast.Name)
                self.assertEqual(value.id, canonical)


if __name__ == "__main__":
    unittest.main()
