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

Eight constraint families are handled:

* ``minProperties`` / ``maxProperties`` on an object schema WITH declared
  properties are dropped by the generator: every field is optional, so an
  empty instance (or, for ``maxProperties``, an over-full one) passes
  validation in violation of the schema. ``minProperties`` support (issue
  #49, PR #55) never grew a ``maxProperties`` counterpart, so
  ``location_serves.json``'s ``maxProperties: 1`` ("the Platform MUST
  supply exactly one target form") went unenforced even though its sibling
  ``minProperties: 1`` on the same schema was already caught. (Either bound
  on a free-form object property is already handled natively — the
  generator maps it to ``Field(min_length=..., max_length=...)`` on the
  dict field.) The script scans the preprocessed schemas for root-level
  ``minProperties``/``maxProperties`` constraints and injects a
  ``model_validator(mode="after")`` into the matching generated classes,
  one validator per bound so both can coexist on the same class. JSON
  Schema counts the keys present on the object, so the validator counts
  provided fields (``model_fields_set``) unioned with extra keys
  (``model_extra``) — an explicit null is a present key, and unknown keys on
  ``extra="allow"`` models count too.

* ``contains`` / ``minContains`` / ``maxContains`` on an array schema is likewise
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

* ``propertyNames`` on an object WITH named ``properties`` is not enforced. Such
  a schema is emitted as a ``BaseModel(extra="allow")`` with the named fields, so
  unknown (extra) keys are accepted without being checked against the declared
  key pattern (``signals.json`` requires reverse-domain keys, yet a malformed
  extra key validates). The script scans for objects that declare
  ``propertyNames`` AND carry named ``properties`` and injects a
  ``model_validator(mode="after")`` that matches every ``model_extra`` key against
  the pattern. The pattern is read from the source schema (inline or via ``$ref``
  to e.g. ``reverse_domain_name.json``), never duplicated here. An object with
  ``propertyNames`` but *no* named properties is emitted as a ``dict[KeyType, V]``
  whose key type already carries the pattern, so it is out of scope.

* ``uniqueItems`` on an array is dropped entirely by the generator, so a list
  field accepts duplicate entries in violation of the schema. The script
  collects the names of array properties declared with ``uniqueItems`` and
  injects a ``field_validator(mode="after")`` into each generated class that
  declares a matching list field.

* Simple conditional ``required`` constraints are dropped: pagination requires
  ``cursor`` when ``has_next_page`` is true, but the generated response model
  always treats it as optional. The script accepts only an unambiguous single
  required discriminator using ``const``/``enum`` and a ``then.required`` list,
  then injects a ``model_validator(mode="after")``. More complex conditions are
  skipped rather than approximated. These rules are commonly carried as
  ``allOf`` branches with no sibling ``properties`` of their own (every
  conditional rule in ``profile.json``'s ``jwk_public_key`` is this shape), so
  the scan validates them against the enclosing object's property set, the
  same threading ``find_conditional_bounds`` (below) already used. A branch's
  own documentation ``title`` (some carry one purely as rule prose, e.g. "EC
  keys carry crv, x, y") is never adopted as the enclosing class name; see
  ``_is_bare_conditional_branch``.

* Conditional numeric bounds, and conditional exact-value (``const``) pins,
  are dropped for the same reason: ``total.json`` requires a ``discount``
  amount to be negative and a ``tax`` amount to be non-negative via if/then
  branches, and ``unit.json`` pins ``scale`` to exactly 0 when ``unit`` is
  ``C62``, but the generated ``Total``/``Unit`` carry no validator, so a
  positive discount or a nonzero C62 scale both validate. These rules are
  carried as ``allOf`` branches, which have no sibling ``properties`` of
  their own, so the scan validates them against the enclosing object's
  property set. A rule whose fields were stripped by request-variant
  projection is inapplicable rather than malformed and is skipped silently.
  As with conditional required rules above, a branch's own documentation
  ``title`` is never adopted as the enclosing class name.

* ``additionalProperties: false`` on an object schema with named properties is
  normally overridden by the generator's ``--extra-fields=allow`` flag. The
  script detects schemas with ``additionalProperties: false`` and flips their
  generated ``model_config`` to ``extra="forbid"`` while preserving
  ``extra="allow"`` on sibling models in the same module.

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

_MAX_MARKER = "_enforce_max_properties"

_MAX_VALIDATOR_TEMPLATE = '''
    @model_validator(mode="after")
    def {marker}(self):
        """JSON Schema maxProperties: allow at most {maximum}
        provided {properties_noun}."""
        provided = self.model_fields_set | set(self.model_extra or {{}})
        if len(provided) > {maximum}:
            raise ValueError(
                "At most {maximum} {properties_noun} may be provided "
                "(schema maxProperties={maximum})"
            )
        return self
'''

_PROPNAMES_MARKER = "_enforce_property_names"

_PROPNAMES_VALIDATOR_TEMPLATE = '''
    @model_validator(mode="after")
    def {marker}(self):
        """JSON Schema propertyNames: every extra key must match the
        declared reverse-domain pattern (schema propertyNames)."""
        pattern = {pattern!r}
        for key in self.model_extra or {{}}:
            if re.fullmatch(pattern, key) is None:
                raise ValueError(
                    f"Property name {{key!r}} does not match the schema "
                    f"propertyNames pattern {{pattern}}"
                )
        return self
'''

_UNIQUE_MARKER = "_enforce_unique_items"

_CONDITIONAL_REQUIRED_MARKER = "_enforce_conditional_required"

_CONDITIONAL_REQUIRED_TEMPLATE = '''
    @model_validator(mode="after")
    def {marker}(self):
        """JSON Schema if/then: enforce conditionally required fields."""
        rules = {rules!r}
        for rule in rules:
            if getattr(self, rule["discriminator"], None) not in rule["values"]:
                continue
            for field in rule["required"]:
                if field not in self.model_fields_set:
                    raise ValueError(
                        f"Field {{field!r}} is required by a schema condition"
                    )
        return self
'''

_CONDITIONAL_BOUNDS_MARKER = "_enforce_conditional_bounds"

# Returned when a rule is well-formed but names fields absent from the class it
# would apply to — distinct from None, which means the shape is unsupported and
# warrants a warning.
_RULE_NOT_APPLICABLE = object()

# Keyword -> (comparison rendered in the message, python operator name). The
# operator is applied as "value <op> limit" and a true result is a violation.
# "const" pins the field to an exact value (e.g. unit.json: scale must be
# exactly 0 when unit is C62) rather than bounding a range; it reuses the
# same "value <op> limit -> violation" shape with not-equal as the operator.
_BOUND_KEYWORDS = {
    "minimum": (">=", "lt"),
    "maximum": ("<=", "gt"),
    "exclusiveMinimum": (">", "le"),
    "exclusiveMaximum": ("<", "ge"),
    "const": ("==", "ne"),
}

_CONDITIONAL_BOUNDS_TEMPLATE = '''
    @model_validator(mode="after")
    def {marker}(self):
        """JSON Schema if/then: enforce conditional numeric bounds."""
        rules = {rules!r}
        checks = {checks!r}
        for rule in rules:
            actual = getattr(self, rule["discriminator"], None)
            if actual not in rule["values"]:
                continue
            for field, bounds in rule["bounds"].items():
                value = getattr(self, field, None)
                if value is None:
                    continue
                for keyword, limit in bounds.items():
                    symbol, op_name = checks[keyword]
                    if getattr(operator, op_name)(value, limit):
                        raise ValueError(
                            f"Field {{field!r}} must be {{symbol}} {{limit}} "
                            f"when {{rule['discriminator']}} is {{actual!r}}"
                        )
        return self
'''

_UNIQUE_VALIDATOR_TEMPLATE = '''
    @field_validator("{field}", mode="after")
    def {marker}_{field}(cls, value):  # noqa: N805
        """JSON Schema uniqueItems: reject duplicate entries."""
        if value is None:
            return value
        seen = []
        for item in value:
            if item in seen:
                raise ValueError(
                    "Items must be unique (schema uniqueItems=true)"
                )
            seen.append(item)
        return value
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
        found[_alias_name(title)] = minimum
    return found


def find_root_max_properties(schema_dir):
    """Map schema title -> maxProperties for root-level object constraints.

    Symmetric twin of find_root_min_properties (see #49/#55, which added
    minProperties support but never a maxProperties counterpart):
    maxProperties on an object schema WITH declared properties is dropped by
    the generator the same way minProperties is, so
    location_serves.json's maxProperties: 1 ("the Platform MUST supply
    exactly one target form") was silently unenforced. As with the min
    side, maxProperties on a free-form object property (no named
    properties) is already handled natively by the generator
    (Field(max_length=...) on the dict field), so it is out of scope here.
    """
    found = {}
    for path in sorted(Path(schema_dir).rglob("*.json")):
        try:
            schema = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(schema, dict):
            continue
        maximum = schema.get("maxProperties")
        if not isinstance(maximum, int) or not schema.get("properties"):
            continue
        title = schema.get("title")
        if not title:
            sys.stderr.write(
                f"  ! {path}: root maxProperties but no title; "
                "cannot map to a class\n"
            )
            continue
        found[_alias_name(title)] = maximum
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


def _ensure_stdlib_import(source, statement):
    """Add a top-level ``import`` statement if absent.

    Inserted right after ``from __future__ import annotations`` so ruff's
    isort pass (run later in the pipeline) settles it into the stdlib group.
    """
    if re.search(rf"^{re.escape(statement)}$", source, re.M):
        return source
    return re.sub(
        r"^(from __future__ import annotations\n)",
        lambda m: f"{m.group(1)}\n{statement}\n",
        source,
        count=1,
        flags=re.M,
    )


def _resolve_property_names_pattern(prop_names, schema_path):
    """Return the key pattern a ``propertyNames`` node enforces, or ``None``.

    Reads an inline ``pattern`` directly, or follows a ``$ref`` to an external
    schema file's root ``pattern`` (e.g. ``reverse_domain_name.json``) so the
    pattern is never duplicated here — it always comes from the source schema.
    Local ``#/...`` pointer refs are not resolved and are skipped with a
    warning rather than guessed.
    """
    if not isinstance(prop_names, dict):
        return None
    inline = prop_names.get("pattern")
    if isinstance(inline, str):
        return inline
    ref = prop_names.get("$ref")
    if not isinstance(ref, str):
        return None
    if ref.startswith("#"):
        sys.stderr.write(
            f"  ! {schema_path}: propertyNames $ref '{ref}' is a local "
            "pointer; pattern not resolved\n"
        )
        return None
    file_part = ref.split("#", 1)[0]
    target = (Path(schema_path).parent / file_part).resolve()
    try:
        referenced = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        sys.stderr.write(
            f"  ! {schema_path}: propertyNames $ref '{ref}' could not be "
            "loaded; pattern not resolved\n"
        )
        return None
    pattern = (
        referenced.get("pattern") if isinstance(referenced, dict) else None
    )
    if not isinstance(pattern, str):
        sys.stderr.write(
            f"  ! {schema_path}: propertyNames $ref '{ref}' target has no "
            "root pattern; not resolved\n"
        )
        return None
    return pattern


def find_property_names_patterns(schema_dir):
    """Map generated class name -> propertyNames pattern for extra-allow models.

    The gap this targets: an object schema that declares ``propertyNames`` AND
    carries named ``properties`` is emitted by the generator as a
    ``BaseModel(extra="allow")`` with those named fields, so unknown (extra)
    keys are never pattern-checked. An object with ``propertyNames`` but *no*
    named ``properties`` is emitted as a ``dict[KeyType, V]`` map whose key type
    already carries the pattern (pydantic validates the keys), so it is out of
    scope. The class is defined mechanically: has ``propertyNames`` (resolvable
    to a pattern) AND non-empty ``properties`` AND a ``title`` to map to a class.
    Nested titled objects are walked too, so the rule is general, not per-file.
    """
    found = {}

    def walk(node, path_str):
        if not isinstance(node, dict):
            if isinstance(node, list):
                for item in node:
                    walk(item, path_str)
            return
        props = node.get("properties")
        if "propertyNames" in node and isinstance(props, dict) and props:
            pattern = _resolve_property_names_pattern(
                node["propertyNames"], path_str
            )
            title = node.get("title")
            if pattern is None:
                pass
            elif not title:
                sys.stderr.write(
                    f"  ! {path_str}: propertyNames on an extra-allow object "
                    "but no title; cannot map to a class\n"
                )
            else:
                # The injected validator uses re.fullmatch to mirror
                # pydantic-core / ECMA-262 (JSON Schema's regex dialect) key
                # semantics, which the sibling dict-map path already applies.
                # That is exact for the ^...$-anchored patterns UCP uses. An
                # unanchored pattern means JSON Schema unanchored-search
                # semantics, where fullmatch would over-restrict; warn so a
                # future schema does not silently get a stricter check.
                if not (pattern.startswith("^") and pattern.endswith("$")):
                    sys.stderr.write(
                        f"  ! {path_str}: propertyNames pattern {pattern!r} is "
                        "not ^/$-anchored; fullmatch enforcement may be "
                        "stricter than JSON Schema search semantics\n"
                    )
                found[_alias_name(title)] = pattern
        for value in node.values():
            walk(value, path_str)

    for path in sorted(Path(schema_dir).rglob("*.json")):
        try:
            schema = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        walk(schema, str(path))
    return found


def inject_property_names(source, class_name, pattern):
    """Inject the propertyNames key validator at the end of ``class_name``."""
    class_re = re.compile(rf"^class {re.escape(class_name)}\(", re.M)
    match = class_re.search(source)
    if not match:
        return source
    # The class body ends at the next top-level statement or EOF.
    tail = re.compile(r"^\S", re.M)
    end_match = tail.search(source, match.end())
    end = end_match.start() if end_match else len(source)
    # Scope the idempotency guard to this class's own body, so a second
    # target class in the same module is still patched.
    if f"def {_PROPNAMES_MARKER}(" in source[match.start() : end]:
        return source
    method = _PROPNAMES_VALIDATOR_TEMPLATE.format(
        marker=_PROPNAMES_MARKER, pattern=pattern
    )
    body = source[:end].rstrip("\n")
    rest = source[end:]
    out = body + "\n" + method + ("\n" + rest if rest else "")
    out = _ensure_pydantic_import(out, "model_validator")
    return _ensure_stdlib_import(out, "import re")


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


def inject_max_properties(source, class_name, maximum):
    """Inject the maxProperties validator at the end of ``class_name``.

    Symmetric twin of inject_min_properties; both validators can be
    injected into the same class (location_serves.json declares both
    minProperties: 1 and maxProperties: 1), each guarded by its own marker
    so neither injection clobbers the other or re-runs on a second pass.
    """
    if f"def {_MAX_MARKER}(" in source:
        return source
    class_re = re.compile(rf"^class {re.escape(class_name)}\(", re.M)
    match = class_re.search(source)
    if not match:
        return source
    # The class body ends at the next top-level statement or EOF.
    tail = re.compile(r"^\S", re.M)
    end_match = tail.search(source, match.end())
    end = end_match.start() if end_match else len(source)
    method = _MAX_VALIDATOR_TEMPLATE.format(
        marker=_MAX_MARKER,
        maximum=maximum,
        properties_noun="property" if maximum == 1 else "properties",
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
        item_condition = _extract_item_required_condition(schema)
        title = schema.get("title")
        if not title:
            sys.stderr.write(
                f"  ! {path}: array contains constraint but no title; "
                "cannot map to a model\n"
            )
            continue
        found[path.stem] = {
            "title": title,
            "groups": groups,
            "item_condition": item_condition,
        }
    return found


def _extract_item_required_condition(schema):
    """Read a simple array-item ``not.enum`` + ``then.required`` rule."""
    items = schema.get("items")
    if not isinstance(items, dict):
        return None
    nodes = [items]
    nodes.extend(
        node for node in items.get("allOf", []) if isinstance(node, dict)
    )
    for node in nodes:
        condition = node.get("if")
        consequence = node.get("then")
        if not isinstance(condition, dict) or not isinstance(consequence, dict):
            continue
        props = condition.get("properties")
        required = consequence.get("required")
        if not isinstance(props, dict) or len(props) != 1:
            continue
        field, predicate = next(iter(props.items()))
        if (
            not isinstance(predicate, dict)
            or set(node) != {"if", "then"}
            or set(condition) != {"properties", "required"}
            or set(consequence) != {"required"}
        ):
            continue
        excluded = predicate.get("not")
        values = excluded.get("enum") if isinstance(excluded, dict) else None
        if (
            condition.get("required") == [field]
            and set(predicate) == {"not"}
            and isinstance(excluded, dict)
            and set(excluded) == {"enum"}
            and isinstance(values, list)
            and values
            and all(isinstance(value, str) for value in values)
            and isinstance(required, list)
            and required
            and all(isinstance(name, str) for name in required)
        ):
            return {"field": field, "excluded": values, "required": required}
    return None


def _alias_name(title):
    """Derive the generated alias name from a schema title (drop spaces)."""
    return "".join(title.split())


def _is_bare_conditional_branch(node):
    """True when ``node`` is an if/then rule wrapper with no type of its own.

    UCP schemas sometimes give an allOf if/then branch its own human-readable
    ``title`` purely as rule documentation (e.g. profile.json's jwk_public_key:
    "EC keys carry crv, x, y", "P-256 pairs with ES256"). Such a branch has no
    ``properties`` of its own -- it constrains the *enclosing* object -- so its
    title never names a generated class. Adopting it as ``current_class_name``
    (the same code path real class-defining titles use) misattributes the
    rule to a nonexistent class instead of the enclosing one. A node that
    carries both an if/then rule AND its own ``properties`` is a genuine
    titled type that happens to declare an inline conditional, and keeps
    adopting its title as before.
    """
    return (
        isinstance(node.get("if"), dict)
        and isinstance(node.get("then"), dict)
        and not isinstance(node.get("properties"), dict)
    )


def _to_camel_case(string):
    """Convert a string (snake, kebab, space-separated) to CamelCase."""
    parts = re.split(r"[^a-zA-Z0-9]", string)
    return "".join(p.capitalize() for p in parts if p)


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


def _build_contains_function(func_name, groups, item_condition=None):
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
    if item_condition:
        field = item_condition["field"]
        lines += [
            f"    _excluded = {item_condition['excluded']!r}",
            "    for _item in value:",
            f"        _actual = (_item.get({field!r}) if isinstance(_item, dict) ",
            f"                   else getattr(_item, {field!r}, None))",
            "        if _actual in _excluded:",
            "            continue",
        ]
        for required in item_condition["required"]:
            lines += [
                f"        if isinstance(_item, dict) and {required!r} not in _item:",
                f'            raise ValueError("Field {required!r} is required for custom {field}")',
                f"        if not isinstance(_item, dict) and {required!r} not in _item.model_fields_set:",
                f'            raise ValueError("Field {required!r} is required for custom {field}")',
            ]
    lines.append("    return value")
    return "\n".join(lines) + "\n"


def inject_array_contains(source, alias_name, groups, item_condition=None):
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
    # Insert right after the last real token inside Annotated[...], not
    # blindly right before the closing bracket. When the annotation is
    # line-wrapped -- which ruff/black do once the item type reference is
    # long enough to push the line past the wrap width, e.g. a
    # request-variant $ref such as total_create_request.TotalCreateRequest
    # replacing the shorter total.Total -- there is already a trailing
    # comma just before the whitespace that precedes "]". Splicing before
    # that whitespace would leave the existing trailing comma and our own
    # leading comma separated by nothing but whitespace: two commas with no
    # expression between them, a SyntaxError (see #34/#35).
    scan = close - 1
    while scan >= 0 and source[scan] in " \t\n":
        scan -= 1
    if scan >= 0 and source[scan] == ",":
        insert_at = scan + 1
        addition = f" AfterValidator({func_name}),"
    else:
        insert_at = scan + 1
        addition = f", AfterValidator({func_name})"
    out = source[:insert_at] + addition + source[insert_at:]
    func_src = _build_contains_function(func_name, groups, item_condition)
    insert_at = assign_re.search(out).start()
    out = out[:insert_at] + func_src + "\n\n" + out[insert_at:]
    return _ensure_pydantic_import(out, "AfterValidator")


def find_conditional_required(schema_dir):
    """Map generated class names to simple if/then required rules."""
    rules_by_class = {}

    def describe(node, properties):
        if not isinstance(node, dict) or set(node) != {"if", "then"}:
            return None
        condition = node["if"]
        consequence = node["then"]
        if (
            not isinstance(condition, dict)
            or set(condition) != {"properties", "required"}
            or not isinstance(consequence, dict)
            or set(consequence) != {"required"}
        ):
            return None
        condition_props = condition["properties"]
        condition_required = condition["required"]
        consequence_required = consequence["required"]
        if (
            not isinstance(condition_props, dict)
            or len(condition_props) != 1
            or not isinstance(condition_required, list)
            or len(condition_required) != 1
            or not isinstance(consequence_required, list)
            or not consequence_required
        ):
            return None
        discriminator, predicate = next(iter(condition_props.items()))
        if condition_required != [discriminator] or not isinstance(
            predicate, dict
        ):
            return None
        if set(predicate) == {"const"}:
            values = [predicate["const"]]
        elif (
            set(predicate) == {"enum"}
            and isinstance(predicate["enum"], list)
            and predicate["enum"]
        ):
            values = predicate["enum"]
        else:
            return None
        if (
            discriminator not in properties
            or any(
                not isinstance(name, str) or name not in properties
                for name in consequence_required
            )
            or any(
                not isinstance(value, (str, int, float, bool))
                for value in values
            )
        ):
            return None
        return {
            "discriminator": discriminator,
            "values": values,
            "required": sorted(consequence_required),
        }

    def walk(node, current_class_name, path_str, enclosing_properties=None):
        if not isinstance(node, dict):
            return
        if isinstance(
            node.get("title"), str
        ) and not _is_bare_conditional_branch(node):
            current_class_name = _alias_name(node["title"])
        properties = node.get("properties")
        # An if/then pair carried as an allOf branch has no sibling
        # properties of its own (every JWK required-field rule is exactly
        # this shape): the object it constrains is the enclosing schema, so
        # its property set is what the rule must be validated against. This
        # mirrors find_conditional_bounds's existing enclosing_properties
        # threading.
        scope = (
            properties if isinstance(properties, dict) else enclosing_properties
        )
        then = node.get("then")
        is_required_rule = isinstance(then, dict) and "required" in then
        if isinstance(scope, dict) and is_required_rule:
            if "else" in node:
                rule = None
            else:
                rule = describe(
                    {key: node[key] for key in ("if", "then") if key in node},
                    scope,
                )
            if rule is None:
                sys.stderr.write(
                    f"  ! {path_str}: unsupported conditional required rule; skipped\n"
                )
            elif current_class_name is not None:
                rules_by_class.setdefault(current_class_name, []).append(rule)
        if isinstance(properties, dict):
            for name, prop in properties.items():
                walk(prop, _to_camel_case(name), path_str)
        defs = node.get("$defs")
        if isinstance(defs, dict):
            for def_name, def_node in defs.items():
                walk(def_node, _to_camel_case(def_name), path_str)
        for key in ("allOf", "anyOf", "oneOf"):
            if isinstance(node.get(key), list):
                for item in node[key]:
                    walk(item, current_class_name, path_str, scope)

    for path in sorted(Path(schema_dir).rglob("*.json")):
        try:
            schema = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(schema, dict):
            continue
        root_title = schema.get("title")
        initial_class = (
            _alias_name(root_title) if root_title else _to_camel_case(path.stem)
        )
        walk(schema, initial_class, str(path))
    return rules_by_class


def inject_conditional_required(source, class_name, rules):
    """Inject simple conditional-required checks into one generated class."""
    class_re = re.compile(rf"^class {re.escape(class_name)}\(", re.M)
    match = class_re.search(source)
    if not match:
        return source
    tail = re.compile(r"^\S", re.M)
    end_match = tail.search(source, match.end())
    end = end_match.start() if end_match else len(source)
    if f"def {_CONDITIONAL_REQUIRED_MARKER}(" in source[match.start() : end]:
        return source
    method = _CONDITIONAL_REQUIRED_TEMPLATE.format(
        marker=_CONDITIONAL_REQUIRED_MARKER,
        rules=rules,
    )
    body = source[:end].rstrip("\n")
    rest = source[end:]
    out = body + "\n" + method + ("\n" + rest if rest else "")
    return _ensure_pydantic_import(out, "model_validator")


def find_conditional_bounds(schema_dir):
    """Map generated class names to if/then numeric-bound rules.

    Complements find_conditional_required, which only handles a ``then`` that
    adds required fields. A ``then`` that instead narrows a numeric range is
    dropped by datamodel-code-generator, so the constraint would otherwise be
    absent from the generated model entirely.
    """
    rules_by_class = {}

    def describe(node, properties):
        if not isinstance(node, dict) or set(node) != {"if", "then"}:
            return None
        condition = node["if"]
        consequence = node["then"]
        if (
            not isinstance(condition, dict)
            or set(condition) != {"properties", "required"}
            or not isinstance(consequence, dict)
            or set(consequence) != {"properties"}
        ):
            return None
        condition_props = condition["properties"]
        condition_required = condition["required"]
        consequence_props = consequence["properties"]
        if (
            not isinstance(condition_props, dict)
            or len(condition_props) != 1
            or not isinstance(condition_required, list)
            or len(condition_required) != 1
            or not isinstance(consequence_props, dict)
            or not consequence_props
        ):
            return None
        discriminator, predicate = next(iter(condition_props.items()))
        if condition_required != [discriminator] or not isinstance(
            predicate, dict
        ):
            return None
        if set(predicate) == {"const"}:
            values = [predicate["const"]]
        elif (
            set(predicate) == {"enum"}
            and isinstance(predicate["enum"], list)
            and predicate["enum"]
        ):
            values = predicate["enum"]
        else:
            return None
        if any(
            not isinstance(value, (str, int, float, bool)) for value in values
        ):
            return None
        bounds = {}
        for name, constraint in consequence_props.items():
            if (
                not isinstance(name, str)
                or not isinstance(constraint, dict)
                or not constraint
                or set(constraint) - set(_BOUND_KEYWORDS)
            ):
                return None
            # "const" pins an exact value and may legitimately be a string
            # (unit.json's C62 pin is an int; JWK's algorithm pins are
            # strings), so it is checked against the same scalar types
            # already accepted for the discriminator's own const/enum
            # values above. The numeric bound keywords keep their existing,
            # narrower int/float (non-bool) requirement.
            for keyword, limit in constraint.items():
                if keyword == "const":
                    if not isinstance(limit, (str, int, float, bool)):
                        return None
                elif not isinstance(limit, (int, float)) or isinstance(
                    limit, bool
                ):
                    return None
            bounds[name] = dict(constraint)
        # A request variant strips the fields a platform must not send, so a
        # rule naming one is inapplicable to that class rather than malformed.
        if discriminator not in properties or any(
            name not in properties for name in bounds
        ):
            return _RULE_NOT_APPLICABLE
        return {
            "discriminator": discriminator,
            "values": values,
            "bounds": bounds,
        }

    def walk(node, current_class_name, path_str, enclosing_properties=None):
        if not isinstance(node, dict):
            return
        if isinstance(
            node.get("title"), str
        ) and not _is_bare_conditional_branch(node):
            current_class_name = _alias_name(node["title"])
        properties = node.get("properties")
        # An if/then pair carried as an allOf branch has no sibling properties:
        # the object it constrains is the enclosing schema, so its property set
        # is what the rule must be validated against.
        scope = (
            properties if isinstance(properties, dict) else enclosing_properties
        )
        then = node.get("then")
        is_bounds_rule = (
            isinstance(then, dict)
            and "properties" in then
            and "required" not in then
        )
        if isinstance(scope, dict) and is_bounds_rule:
            rule = (
                None
                if "else" in node
                else describe(
                    {key: node[key] for key in ("if", "then") if key in node},
                    scope,
                )
            )
            if rule is None:
                sys.stderr.write(
                    f"  ! {path_str}: unsupported conditional bounds rule; skipped\n"
                )
            elif rule is not _RULE_NOT_APPLICABLE and current_class_name:
                rules_by_class.setdefault(current_class_name, []).append(rule)
        if isinstance(properties, dict):
            for name, prop in properties.items():
                walk(prop, _to_camel_case(name), path_str)
        defs = node.get("$defs")
        if isinstance(defs, dict):
            for def_name, def_node in defs.items():
                walk(def_node, _to_camel_case(def_name), path_str)
        for key in ("allOf", "anyOf", "oneOf"):
            if isinstance(node.get(key), list):
                for item in node[key]:
                    walk(item, current_class_name, path_str, scope)

    for path in sorted(Path(schema_dir).rglob("*.json")):
        try:
            schema = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(schema, dict):
            continue
        root_title = schema.get("title")
        initial_class = (
            _alias_name(root_title) if root_title else _to_camel_case(path.stem)
        )
        walk(schema, initial_class, str(path))
    return rules_by_class


def inject_conditional_bounds(source, class_name, rules):
    """Inject conditional numeric-bound checks into one generated class."""
    class_re = re.compile(rf"^class {re.escape(class_name)}\(", re.M)
    match = class_re.search(source)
    if not match:
        return source
    tail = re.compile(r"^\S", re.M)
    end_match = tail.search(source, match.end())
    end = end_match.start() if end_match else len(source)
    if f"def {_CONDITIONAL_BOUNDS_MARKER}(" in source[match.start() : end]:
        return source
    method = _CONDITIONAL_BOUNDS_TEMPLATE.format(
        marker=_CONDITIONAL_BOUNDS_MARKER,
        rules=rules,
        checks=_BOUND_KEYWORDS,
    )
    body = source[:end].rstrip("\n")
    rest = source[end:]
    out = body + "\n" + method + ("\n" + rest if rest else "")
    out = _ensure_stdlib_import(out, "import operator")
    return _ensure_pydantic_import(out, "model_validator")


def find_unique_items_fields(schema_dir):
    """Map generated class names to fields carrying ``uniqueItems``.

    A schema node needs a title so its constraint can be associated with a
    generated class. Untitled nodes are resolved using their property path.
    """
    fields_by_class = {}

    def walk(node, current_class_name, path_str):
        if not isinstance(node, dict):
            return

        if isinstance(node.get("title"), str):
            current_class_name = _alias_name(node["title"])

        props = node.get("properties")
        if isinstance(props, dict):
            for name, prop in props.items():
                if not isinstance(prop, dict):
                    continue

                if prop.get("uniqueItems") is True and (
                    prop.get("type") == "array" or "items" in prop
                ):
                    if current_class_name is None:
                        sys.stderr.write(
                            f"  ! {path_str}: uniqueItems field '{name}' "
                            "belongs to an untitled object; cannot map to a class\n"
                        )
                        continue
                    fields_by_class.setdefault(current_class_name, set()).add(
                        name
                    )

                # Recurse into properties
                next_class_name = (
                    _to_camel_case(name) if current_class_name else None
                )
                walk(prop, next_class_name, path_str)

        # Recurse into $defs
        defs = node.get("$defs")
        if isinstance(defs, dict):
            for def_name, def_node in defs.items():
                walk(def_node, _to_camel_case(def_name), path_str)

        # Recurse into combinators (allOf, anyOf, oneOf)
        for key in ("allOf", "anyOf", "oneOf"):
            if isinstance(node.get(key), list):
                for item in node[key]:
                    walk(item, current_class_name, path_str)

    for path in sorted(Path(schema_dir).rglob("*.json")):
        try:
            schema = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(schema, dict):
            continue

        root_title = schema.get("title")
        initial_class = (
            _alias_name(root_title) if root_title else _to_camel_case(path.stem)
        )
        walk(schema, initial_class, str(path))

    return fields_by_class


def inject_unique_items(source, unique_fields_by_class):
    """Inject uniqueness validators for list fields declared ``uniqueItems``.

    A validator is added only when both the generated class name and list
    field name match the scoped schema constraints.
    """
    if not unique_fields_by_class:
        return source
    class_re = re.compile(r"^class (\w+)\(", re.M)
    matches = list(class_re.finditer(source))
    if not matches:
        return source
    new_source = source
    patched = False
    # Process from the last class to the first so earlier insert offsets
    # (computed against the original source) stay valid as text is appended.
    for match in reversed(matches):
        unique_fields = unique_fields_by_class.get(match.group(1), set())
        if not unique_fields:
            continue
        body_start = match.end()
        tail = re.compile(r"^\S", re.M)
        end_match = tail.search(source, body_start)
        body_end = end_match.start() if end_match else len(source)
        body = source[body_start:body_end]
        targets = []
        for field_match in re.finditer(
            r"^    (\w+): [^\n]*\blist\[", body, re.M
        ):
            field = field_match.group(1)
            marker = f"def {_UNIQUE_MARKER}_{field}("
            if field in unique_fields and marker not in body:
                targets.append(field)
        if not targets:
            continue
        methods = "".join(
            _UNIQUE_VALIDATOR_TEMPLATE.format(
                marker=_UNIQUE_MARKER, field=field
            )
            for field in targets
        )
        prefix = new_source[:body_end].rstrip("\n")
        suffix = new_source[body_end:]
        new_source = prefix + methods + ("\n" + suffix if suffix else "")
        patched = True
    if patched:
        new_source = _ensure_pydantic_import(new_source, "field_validator")
    return new_source


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


def _patch_max_properties():
    """Inject maxProperties validators; return (patched_count, exit_code)."""
    constraints = find_root_max_properties(SCHEMA_DIR)
    if not constraints:
        sys.stdout.write(
            "postprocess: no root-level maxProperties constraints found\n"
        )
        return 0, 0
    patched = 0
    for title, maximum in sorted(constraints.items()):
        hits = []
        for path in sorted(OUTPUT_DIR.rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            if not re.search(rf"^class {re.escape(title)}\(", source, re.M):
                continue
            updated = inject_max_properties(source, title, maximum)
            if updated != source:
                path.write_text(updated, encoding="utf-8")
                patched += 1
            hits.append(path)
        label = ", ".join(str(h) for h in hits) or "NO GENERATED CLASS FOUND"
        sys.stdout.write(f"  maxProperties={maximum} on '{title}' -> {label}\n")
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
            targets[info["title"]] = {
                "groups": raw[origin]["groups"],
                "item_condition": raw[origin]["item_condition"],
            }
    # Defensive: cover each raw base title even if the preprocessed base lost
    # its contains entirely.
    for info in raw.values():
        targets.setdefault(
            info["title"],
            {
                "groups": info["groups"],
                "item_condition": info["item_condition"],
            },
        )
    return targets


def _patch_property_names():
    """Inject propertyNames validators; return (patched_count, exit_code)."""
    patterns = find_property_names_patterns(SCHEMA_DIR)
    if not patterns:
        sys.stdout.write(
            "postprocess: no propertyNames constraints on extra-allow "
            "models found\n"
        )
        return 0, 0
    patched = 0
    for class_name, pattern in sorted(patterns.items()):
        hits = []
        for path in sorted(OUTPUT_DIR.rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            if not re.search(
                rf"^class {re.escape(class_name)}\(", source, re.M
            ):
                continue
            updated = inject_property_names(source, class_name, pattern)
            if updated != source:
                path.write_text(updated, encoding="utf-8")
                patched += 1
            hits.append(path)
        label = ", ".join(str(h) for h in hits) or "NO GENERATED CLASS FOUND"
        sys.stdout.write(
            f"  propertyNames {pattern!r} on '{class_name}' -> {label}\n"
        )
        if not hits:
            sys.stderr.write(
                f"  ! '{class_name}' has no generated class; "
                "constraint not enforced\n"
            )
            return patched, 1
    return patched, 0


def _patch_array_contains():
    """Inject array-contains validators; return (patched_count, exit_code)."""
    targets = _array_contains_targets()
    if not targets:
        sys.stdout.write("postprocess: no array contains constraints found\n")
        return 0, 0
    patched = 0
    for title, constraint in sorted(targets.items()):
        groups = constraint["groups"]
        alias = _alias_name(title)
        hits = []
        for path in sorted(OUTPUT_DIR.rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            if not re.search(
                rf"^{re.escape(alias)} = TypeAliasType\(", source, re.M
            ):
                continue
            updated = inject_array_contains(
                source, alias, groups, constraint["item_condition"]
            )
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


def _patch_conditional_required():
    """Inject conditional-required validators; return counts and status."""
    rules_by_class = find_conditional_required(SCHEMA_DIR)
    if not rules_by_class:
        sys.stdout.write(
            "postprocess: no simple conditional required rules found\n"
        )
        return 0, 0
    patched = 0
    for class_name, rules in sorted(rules_by_class.items()):
        hits = []
        for path in sorted(OUTPUT_DIR.rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            if not re.search(
                rf"^class {re.escape(class_name)}\(", source, re.M
            ):
                continue
            updated = inject_conditional_required(source, class_name, rules)
            if updated != source:
                path.write_text(updated, encoding="utf-8")
                patched += 1
            hits.append(path)
        label = (
            ", ".join(str(path) for path in hits) or "NO GENERATED CLASS FOUND"
        )
        sys.stdout.write(
            f"  conditional required on '{class_name}' -> {label}\n"
        )
        if not hits:
            return patched, 1
    return patched, 0


def _patch_conditional_bounds():
    """Inject conditional numeric-bound validators; return counts and status."""
    rules_by_class = find_conditional_bounds(SCHEMA_DIR)
    if not rules_by_class:
        sys.stdout.write("postprocess: no conditional bounds rules found\n")
        return 0, 0
    patched = 0
    for class_name, rules in sorted(rules_by_class.items()):
        hits = []
        for path in sorted(OUTPUT_DIR.rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            if not re.search(
                rf"^class {re.escape(class_name)}\(", source, re.M
            ):
                continue
            updated = inject_conditional_bounds(source, class_name, rules)
            if updated != source:
                path.write_text(updated, encoding="utf-8")
                patched += 1
            hits.append(path)
        label = (
            ", ".join(str(path) for path in hits) or "NO GENERATED CLASS FOUND"
        )
        sys.stdout.write(f"  conditional bounds on '{class_name}' -> {label}\n")
        if not hits:
            return patched, 1
    return patched, 0


def _patch_unique_items():
    """Inject uniqueItems validators; return (patched_count, exit_code)."""
    unique_fields_by_class = find_unique_items_fields(SCHEMA_DIR)
    if not unique_fields_by_class:
        sys.stdout.write("postprocess: no uniqueItems constraints found\n")
        return 0, 0
    unique_patched = 0
    touched = []
    for path in sorted(OUTPUT_DIR.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        updated = inject_unique_items(source, unique_fields_by_class)
        if updated != source:
            path.write_text(updated, encoding="utf-8")
            unique_patched += 1
            touched.append(path)
    labels = sorted(
        f"{class_name}.{field}"
        for class_name, fields in unique_fields_by_class.items()
        for field in fields
    )
    sys.stdout.write(
        f"  uniqueItems fields {labels} -> "
        f"{unique_patched} module(s) patched"
        f" ({', '.join(str(t) for t in touched) or 'none'})\n"
    )
    return unique_patched, 0


def find_extra_forbid_class_names(schema_dir):
    """Map generated class names for objects that forbid unknown keys.

    The gap this targets: an object schema that declares
    ``additionalProperties: false`` AND carries named ``properties`` is still
    emitted by the generator as ``BaseModel(extra="allow")`` (generation runs
    with ``--extra-fields=allow``), so unknown keys are silently retained in
    ``model_extra`` instead of being rejected. The rule is mechanical: an
    object node with ``additionalProperties is False`` and non-empty named
    ``properties`` maps to its generated class name via its ``title`` (root
    objects) or its property path (untitled nested objects, e.g.
    ``allows_multi_destination`` -> ``AllowsMultiDestination``).
    """
    found = set()

    def visit(node, class_name):
        if not isinstance(node, dict):
            if isinstance(node, list):
                for item in node:
                    visit(item, class_name)
            return
        effective = (
            _alias_name(node["title"]) if node.get("title") else class_name
        )
        if (
            node.get("additionalProperties") is False
            and isinstance(node.get("properties"), dict)
            and node["properties"]
        ):
            found.add(effective)
        for name, child in (node.get("properties") or {}).items():
            visit(child, _to_camel_case(name))

    for path in sorted(Path(schema_dir).rglob("*.json")):
        try:
            schema = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(schema, dict):
            continue
        root_name = (
            _alias_name(schema["title"])
            if schema.get("title")
            else _to_camel_case(path.stem)
        )
        visit(schema, root_name)
    return found


def inject_extra_forbid(source, class_name):
    """Flip the target class's ``extra="allow"`` config to ``extra="forbid"``.

    Only the named class's own ``model_config`` is changed (its body, from the
    ``class`` statement to the next top-level ``class``/``def``), so sibling
    classes in the same module keep ``extra="allow"``. The source is returned
    unchanged when the class is absent or already ``extra="forbid"``.
    """
    head = re.search(
        rf"^class {re.escape(class_name)}\(BaseModel\):", source, re.M
    )
    if not head:
        return source
    rest = source[head.end() :]
    next_top = re.search(r"^(?=class |def )", rest, re.M)
    body_end = len(rest) if next_top is None else next_top.start()
    body = rest[:body_end]
    if 'extra="allow"' not in body:
        return source
    new_body = body.replace('extra="allow"', 'extra="forbid"', 1)
    return source[: head.end()] + new_body + rest[body_end:]


def _patch_extra_forbid():
    """Inject extra="forbid" on models whose schema forbids unknown keys."""
    class_names = find_extra_forbid_class_names(SCHEMA_DIR)
    if not class_names:
        sys.stdout.write(
            "postprocess: no additionalProperties:false models found\n"
        )
        return 0, 0
    patched = 0
    for class_name in sorted(class_names):
        hits = []
        for path in sorted(OUTPUT_DIR.rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            if not re.search(
                rf"^class {re.escape(class_name)}\(", source, re.M
            ):
                continue
            updated = inject_extra_forbid(source, class_name)
            if updated != source:
                path.write_text(updated, encoding="utf-8")
                patched += 1
            hits.append(path)
        label = ", ".join(str(h) for h in hits) or "NO GENERATED CLASS FOUND"
        sys.stdout.write(f"  extra=forbid on '{class_name}' -> {label}\n")
        if not hits:
            sys.stderr.write(
                f"  ! '{class_name}' has no generated class; "
                "constraint not enforced\n"
            )
            return patched, 1
    return patched, 0


def main():
    """Main entry point to scan schemas and patch generated models."""
    patched_mp, rc_mp = _patch_min_properties()
    patched_xp, rc_xp = _patch_max_properties()
    patched_pn, rc_pn = _patch_property_names()
    patched_ac, rc_ac = _patch_array_contains()
    patched_cr, rc_cr = _patch_conditional_required()
    patched_cb, rc_cb = _patch_conditional_bounds()
    patched_ui, rc_ui = _patch_unique_items()
    patched_ef, rc_ef = _patch_extra_forbid()
    total = (
        patched_mp
        + patched_xp
        + patched_pn
        + patched_ac
        + patched_cr
        + patched_cb
        + patched_ui
        + patched_ef
    )
    sys.stdout.write(f"postprocess: {total} module(s) patched\n")
    return rc_mp or rc_xp or rc_pn or rc_ac or rc_cr or rc_cb or rc_ui or rc_ef


if __name__ == "__main__":
    sys.exit(main())
