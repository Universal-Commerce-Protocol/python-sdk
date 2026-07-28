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

``contains`` / ``minContains`` / ``maxContains`` on an array schema is likewise
dropped by the generator: ``totals.json`` requires *exactly one* ``subtotal``
*and exactly one* ``total`` entry, but the generated ``Totals`` is a bare
``list[Total]`` alias, so an empty array (or one missing either required entry,
or with duplicates) validates in violation of the schema. An array root is
emitted as a ``TypeAliasType`` wrapping ``Annotated[list[...], ...]`` rather
than a ``BaseModel`` subclass, so ``model_validator`` cannot apply; this script
instead injects a module-level counting function, threaded into the alias
metadata as a ``pydantic.AfterValidator``. Every predicate is derived from
``contains.properties.<field>.const`` — nothing is hard-coded — and one function
enforces *all* of a schema's contains bounds.

The pristine (pre-preprocessing) schemas are read for this: ``totals.json``
carries its two containment rules as two ``allOf`` branches, and
``preprocess_schemas.py`` merges ``allOf`` into the root, where a JSON node can
hold only one ``contains`` — so the second (``total``) would be lost if the
preprocessed output were scanned. generate_models.sh snapshots the originals to
``ucp/raw_schemas`` before preprocessing for exactly this reason. The bound is
applied to the base model and to its generated request variants (linked by file
stem), and travels wherever the alias is reused as a field type.

Runs from generate_models.sh between generation and formatting; idempotent.
"""

import json
import re
import sys
from pathlib import Path

SCHEMA_DIR = Path("ucp/source/schemas")
# Pristine schemas snapshotted by generate_models.sh before preprocessing.
# Array contains bounds are read from here, not SCHEMA_DIR, because
# preprocessing merges allOf and can drop a second contains keyword.
RAW_SCHEMA_DIR = Path("ucp/raw_schemas")
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


def _ensure_pydantic_import(source, symbol):
    """Add ``symbol`` to the ``from pydantic import`` line if absent."""
    if re.search(
        rf"^from pydantic import .*\b{re.escape(symbol)}\b", source, re.M
    ):
        return source
    return re.sub(
        r"^(from pydantic import [^\n]+)$",
        lambda m: f"{m.group(1)}, {symbol}",
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
    return _ensure_pydantic_import(out, "model_validator")


def _extract_contains_groups(schema, path=None):
    """Collect every array ``contains`` group from a schema's root + allOf.

    Each group is ``{"pairs": [(field, const), ...], "min": int,
    "max": int | None}``, derived from ``contains.properties.<field>.const``
    with its ``minContains`` / ``maxContains`` bounds. A ``contains`` keyword
    may sit at the schema root or inside any ``allOf`` branch; each contributes
    a group, so "exactly one subtotal and one total" yields two. The predicate
    is read from the schema, never hard-coded.
    """
    nodes = [schema]
    if isinstance(schema.get("allOf"), list):
        nodes.extend(n for n in schema["allOf"] if isinstance(n, dict))
    groups = []
    for node in nodes:
        contains = node.get("contains")
        if not isinstance(contains, dict):
            continue
        props = contains.get("properties")
        pairs = []
        if isinstance(props, dict):
            for field, spec in props.items():
                if isinstance(spec, dict) and "const" in spec:
                    pairs.append((field, spec["const"]))
        if not pairs:
            if path is not None:
                sys.stderr.write(
                    f"  ! {path}: contains predicate has no "
                    "properties.*.const; cannot derive a check\n"
                )
            continue
        # JSON Schema: minContains defaults to 1 when contains is present.
        groups.append(
            {
                "pairs": pairs,
                "min": node.get("minContains", 1),
                "max": node.get("maxContains"),
            }
        )
    return groups


def find_array_contains_constraints(schema_dir):
    """Map file stem -> ``{"title": str, "groups": [...]}`` for array schemas.

    Keyed by file stem (not title) so a base schema can be linked to its
    generated request variants, whose stems extend it (``totals`` ->
    ``totals_create_request``). Scanned against the *pristine* schemas
    (``RAW_SCHEMA_DIR``); see the module docstring for why the preprocessed
    output must not be used here.
    """
    found = {}
    for path in sorted(Path(schema_dir).rglob("*.json")):
        try:
            schema = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(schema, dict):
            continue
        # ``contains`` only constrains arrays; skip anything else.
        if schema.get("type") != "array" and "items" not in schema:
            continue
        groups = _extract_contains_groups(schema, path)
        if not groups:
            continue
        title = schema.get("title")
        if not title:
            sys.stderr.write(
                f"  ! {path}: array contains constraint but no title; "
                "cannot map to a model\n"
            )
            continue
        found[path.stem] = {"title": title, "groups": groups}
    return found


def _alias_name(title):
    """Derive the generated alias name from a schema title (drop spaces)."""
    return "".join(title.split())


def _snake_name(name):
    """CamelCase alias -> snake_case suffix for a unique function name."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _predicate_expr(pairs):
    """Build a per-item boolean expression matching all (field, const) pairs.

    Items are ``Total`` instances after inner validation, but a mapping is
    handled too so the check is robust regardless of the item representation.
    """
    parts = []
    for field, const in pairs:
        parts.append(
            f"(_item.get({field!r}) if isinstance(_item, dict) "
            f"else getattr(_item, {field!r}, None)) == {const!r}"
        )
    return " and ".join(parts)


def _build_contains_function(func_name, groups):
    """Render the module-level ``AfterValidator`` counting function."""
    lines = [
        f"def {func_name}(value):",
        '    """JSON Schema contains/minContains/maxContains (see #49)."""',
    ]
    for index, group in enumerate(groups):
        count = "_matched" if len(groups) == 1 else f"_matched_{index}"
        desc = ", ".join(f"{f}=={c!r}" for f, c in group["pairs"])
        lines += [
            f"    {count} = sum(",
            "        1",
            "        for _item in value",
            f"        if {_predicate_expr(group['pairs'])}",
            "    )",
        ]
        minimum = group["min"]
        noun = "entry" if minimum == 1 else "entries"
        lines += [
            f"    if {count} < {minimum}:",
            "        raise ValueError(",
            f'            "Array must contain at least {minimum} {noun} "',
            f'            "matching {desc} (schema minContains={minimum})"',
            "        )",
        ]
        maximum = group["max"]
        if maximum is not None:
            noun = "entry" if maximum == 1 else "entries"
            lines += [
                f"    if {count} > {maximum}:",
                "        raise ValueError(",
                f'            "Array must contain at most {maximum} {noun} "',
                f'            "matching {desc} (schema maxContains={maximum})"',
                "        )",
            ]
    lines.append("    return value")
    return "\n".join(lines) + "\n"


def inject_array_contains(source, alias_name, groups):
    """Thread an ``AfterValidator`` into ``alias_name``'s alias metadata.

    Array roots are emitted as ``NAME = TypeAliasType("NAME", Annotated[...])``,
    not a ``BaseModel`` subclass, so the constraint is enforced by inserting
    ``AfterValidator(<fn>)`` into the ``Annotated[...]`` metadata and defining
    ``<fn>`` just above the assignment. Idempotent via the function name.
    """
    func_name = f"_enforce_contains_{_snake_name(alias_name)}"
    if f"def {func_name}(" in source:
        return source
    assign_re = re.compile(rf"^{re.escape(alias_name)} = TypeAliasType\(", re.M)
    match = assign_re.search(source)
    if not match:
        return source
    ann_start = source.find("Annotated[", match.end())
    if ann_start == -1:
        return source
    # Bracket-match to the ``]`` that closes ``Annotated[``.
    depth = 0
    close = None
    for pos in range(ann_start + len("Annotated"), len(source)):
        char = source[pos]
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                close = pos
                break
    if close is None:
        return source
    out = source[:close] + f", AfterValidator({func_name})" + source[close:]
    func_src = _build_contains_function(func_name, groups)
    insert_at = assign_re.search(out).start()
    out = out[:insert_at] + func_src + "\n\n" + out[insert_at:]
    return _ensure_pydantic_import(out, "AfterValidator")


def _patch_min_properties():
    """Inject minProperties validators; return (patched_count, exit_code)."""
    constraints = find_root_min_properties(SCHEMA_DIR)
    if not constraints:
        sys.stdout.write(
            "postprocess: no root-level minProperties constraints found\n"
        )
        return 0, 0
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
            return patched, 1
    return patched, 0


def _array_contains_targets():
    """Resolve ``title -> groups`` for every model needing a contains bound.

    The authoritative (complete) groups come from the pristine schemas. The
    preprocessed tree is consulted only to enumerate which models actually
    exist — the base plus its generated request variants — so each variant
    inherits its base schema's full set of containment rules. Variants are
    linked to their base by file stem (``totals_create_request`` -> ``totals``).
    """
    raw = find_array_contains_constraints(RAW_SCHEMA_DIR)
    if not raw:
        # Fallback keeps a standalone run working if the snapshot is absent,
        # though the pipeline always provides it (see module docstring).
        raw = find_array_contains_constraints(SCHEMA_DIR)
        if raw:
            sys.stderr.write(
                f"  ! {RAW_SCHEMA_DIR} missing; falling back to preprocessed "
                "schemas (multi-branch contains may be incomplete)\n"
            )
    if not raw:
        return {}
    raw_stems = sorted(raw, key=len, reverse=True)
    targets = {}
    # Enumerate base + variants from the preprocessed tree; attach raw groups.
    for stem, info in find_array_contains_constraints(SCHEMA_DIR).items():
        origin = next(
            (s for s in raw_stems if stem == s or stem.startswith(s + "_")),
            None,
        )
        if origin is not None:
            targets[info["title"]] = raw[origin]["groups"]
    # Defensive: cover each raw base title even if the preprocessed base lost
    # its contains entirely.
    for info in raw.values():
        targets.setdefault(info["title"], info["groups"])
    return targets


def _patch_array_contains():
    """Inject array-contains validators; return (patched_count, exit_code)."""
    targets = _array_contains_targets()
    if not targets:
        sys.stdout.write("postprocess: no array contains constraints found\n")
        return 0, 0
    patched = 0
    for title, groups in sorted(targets.items()):
        alias = _alias_name(title)
        hits = []
        for path in sorted(OUTPUT_DIR.rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            if not re.search(
                rf"^{re.escape(alias)} = TypeAliasType\(", source, re.M
            ):
                continue
            updated = inject_array_contains(source, alias, groups)
            if updated != source:
                path.write_text(updated, encoding="utf-8")
                patched += 1
            hits.append(path)
        preds = "; ".join(
            " & ".join(f"{f}=={c!r}" for f, c in g["pairs"]) for g in groups
        )
        label = ", ".join(str(h) for h in hits) or "NO GENERATED ALIAS FOUND"
        sys.stdout.write(f"  contains [{preds}] on '{title}' -> {label}\n")
        if not hits:
            sys.stderr.write(
                f"  ! '{title}' has no generated alias; "
                "constraint not enforced\n"
            )
            return patched, 1
    return patched, 0


def main():
    """Main entry point to scan schemas and patch generated models."""
    patched_mp, rc_mp = _patch_min_properties()
    patched_ac, rc_ac = _patch_array_contains()
    total = patched_mp + patched_ac
    sys.stdout.write(f"postprocess: {total} module(s) patched\n")
    return rc_mp or rc_ac


if __name__ == "__main__":
    sys.exit(main())
