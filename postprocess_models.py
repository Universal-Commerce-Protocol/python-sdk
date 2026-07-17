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

"""Post-generation fixes for constraints datamodel-code-generator ignores.

``minProperties`` on an object schema WITH declared properties is dropped by
the generator (issue #49): every field is optional, so an empty instance
passes validation in violation of the schema. (``minProperties`` on a
free-form object property is already handled natively — the generator maps it
to ``Field(min_length=...)`` on the dict field.)

This script scans the preprocessed schemas for root-level ``minProperties``
constraints and injects a ``model_validator(mode="after")`` into the matching
generated classes. JSON Schema counts the keys present on the object, so the
validator counts provided fields (``model_fields_set``) unioned with extra
keys (``model_extra``) — an explicit null is a present key, and unknown keys
on ``extra="allow"`` models count too.

Runs from generate_models.sh between generation and formatting; idempotent.
"""

import json
import re
import sys
from pathlib import Path

SCHEMA_DIR = Path("ucp/source/schemas")
OUTPUT_DIR = Path("src/ucp_sdk/models/schemas")

_MARKER = "_enforce_min_properties"

_VALIDATOR_TEMPLATE = '''
    @model_validator(mode="after")
    def {marker}(self):
        """JSON Schema minProperties: require at least {minimum}
        provided {properties_noun}."""
        provided = self.model_fields_set | set(self.model_extra or {{}})
        if len(provided) < {minimum}:
            raise ValueError(
                "At least {minimum} {properties_noun} must be provided "
                "(schema minProperties={minimum})"
            )
        return self
'''


def find_root_min_properties(schema_dir):
    """Map schema title -> minProperties for root-level object constraints."""
    found = {}
    for path in sorted(Path(schema_dir).rglob("*.json")):
        try:
            schema = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(schema, dict):
            continue
        minimum = schema.get("minProperties")
        if not minimum or not schema.get("properties"):
            continue
        title = schema.get("title")
        if not title:
            sys.stderr.write(
                f"  ! {path}: root minProperties but no title; "
                "cannot map to a class\n"
            )
            continue
        found[title] = minimum
    return found


def _ensure_validator_import(source):
    """Add model_validator to the existing pydantic import if missing."""
    if re.search(r"^from pydantic import .*\bmodel_validator\b", source, re.M):
        return source
    return re.sub(
        r"^(from pydantic import [^\n]+)$",
        lambda m: f"{m.group(1)}, model_validator",
        source,
        count=1,
        flags=re.M,
    )


def inject_min_properties(source, class_name, minimum):
    """Inject the minProperties validator at the end of ``class_name``."""
    if f"def {_MARKER}(" in source:
        return source
    class_re = re.compile(rf"^class {re.escape(class_name)}\(", re.M)
    match = class_re.search(source)
    if not match:
        return source
    # The class body ends at the next top-level statement or EOF.
    tail = re.compile(r"^\S", re.M)
    end_match = tail.search(source, match.end())
    end = end_match.start() if end_match else len(source)
    method = _VALIDATOR_TEMPLATE.format(
        marker=_MARKER,
        minimum=minimum,
        properties_noun="property" if minimum == 1 else "properties",
    )
    body = source[:end].rstrip("\n")
    rest = source[end:]
    out = body + "\n" + method + ("\n" + rest if rest else "")
    return _ensure_validator_import(out)


def main():
    """Main entry point to scan schemas and patch generated models."""
    constraints = find_root_min_properties(SCHEMA_DIR)
    if not constraints:
        sys.stdout.write(
            "postprocess: no root-level minProperties constraints found\n"
        )
        return 0
    patched = 0
    for title, minimum in sorted(constraints.items()):
        hits = []
        for path in sorted(OUTPUT_DIR.rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            if not re.search(rf"^class {re.escape(title)}\(", source, re.M):
                continue
            updated = inject_min_properties(source, title, minimum)
            if updated != source:
                path.write_text(updated, encoding="utf-8")
                patched += 1
            hits.append(path)
        label = ", ".join(str(h) for h in hits) or "NO GENERATED CLASS FOUND"
        sys.stdout.write(f"  minProperties={minimum} on '{title}' -> {label}\n")
        if not hits:
            sys.stderr.write(
                f"  ! '{title}' has no generated class; "
                "constraint not enforced\n"
            )
            return 1
    sys.stdout.write(f"postprocess: {patched} module(s) patched\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
