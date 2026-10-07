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

Eleven constraint families are handled:

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

  The source schemas are read directly for this: ``totals.json`` carries its two
  containment rules as two ``allOf`` branches, whereas ``ucp-schema generate-types``
  strips validation-only ``contains`` ``allOf`` branches when synthesizing the
  flat ``$defs`` bundle. The bound is applied to the base model and to its
  generated request variants, and travels wherever the alias is reused as a field
  type.

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

* A discriminator retyping an array PROPERTY's items to a schema file
  different from the property's own base ``$ref`` is dropped entirely, a
  third if/then shape distinct from the required-fields and numeric-bounds
  families above. ``fulfillment_method.json``'s ``destinations`` stays typed
  to the base ``FulfillmentDestination`` regardless of ``type``, even though
  a `shipping` method's destinations are really ``ShippingDestination``
  (postal address fields, `type` const `shipping_address`) and a `pickup`
  method's are really ``LocationDestination`` (`type` const
  `business_location`) — so a `shipping` method can currently list a
  destination typed `business_location` and it validates. Pydantic has no
  clean way to retype a field's item type from a source-text splice, so
  this is enforced with a runtime check instead of a static type change:
  each item is checked against the referenced schema's own (root-level,
  post-merge) required keys and const-pinned properties — an approximation,
  not a full re-derivation of the retyped type (a schema the retyped file
  itself ``allOf``-references, e.g. ``postal_address.json``, is not
  inspected).

* ``dependentRequired`` on an object is dropped entirely by the generator. For
  example, ``time_interval.json`` allows an empty fragment but requires ``opens``
  and ``closes`` to appear together; the generated ``TimeInterval`` instead
  accepts either field alone. The script scans root object schemas for valid
  dependent-field maps and injects a ``model_validator(mode="after")`` that uses
  property presence (``model_fields_set`` plus extra keys), not value truthiness.
  A rule naming a field absent from a projected generated class is skipped rather
  than approximated.

* ``additionalProperties: false`` on an object schema with named properties is
  normally overridden by the generator's ``--extra-fields=allow`` flag. The
  script detects schemas with ``additionalProperties: false`` and flips their
  generated ``model_config`` to ``extra="forbid"`` while preserving
  ``extra="allow"`` on sibling models in the same module.

* ``pattern`` on a ``format: date-time`` string is not dropped but emitted as
  ``Field(pattern=...)`` on the ``AwareDatetime`` field, where pydantic
  applies it to the parsed datetime rather than the string: validating
  ``location_filter.json``'s ``hours.open_at`` raised a raw ``TypeError``
  ("Unable to apply constraint 'pattern'") instead of a ``ValidationError``.
  ``AwareDatetime`` alone is not equivalent to the pattern (it also accepts
  ``+0530`` offsets and epoch-second strings), so the script removes the
  ``pattern`` argument and injects a ``field_validator(mode="before")`` that
  matches the raw string against the same pattern. The fields are found in
  the generated source, not the schemas: dropping the pattern before
  generation would let ``--reuse-model`` merge the class with a pattern-less
  twin, and the validator would then reach both. A pattern inside an
  ``Annotated`` type alias (e.g. date-time array items) is not rewritten; it
  is reported and fails the run.

Runs from generate_models.sh between generation and formatting; idempotent.
"""

import ast
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

_DEPENDENT_REQUIRED_MARKER = "_enforce_dependent_required"

_DEPENDENT_REQUIRED_TEMPLATE = '''
    @model_validator(mode="after")
    def {marker}(self):
        """JSON Schema dependentRequired: enforce dependent fields."""
        rules = {rules!r}
        provided = self.model_fields_set | set(self.model_extra or {{}})
        for field, required_fields in rules.items():
            if field not in provided:
                continue
            for required in required_fields:
                if required not in provided:
                    raise ValueError(
                        f"Field {{required!r}} is required when {{field!r}} "
                        "is provided (schema dependentRequired)"
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

_RETYPE_MARKER = "_enforce_conditional_item_retyping"

_RETYPE_TEMPLATE = '''
    @model_validator(mode="after")
    def {marker}(self):
        """JSON Schema if/then: approximate a discriminator's array-item
        retyping to a different referenced schema, via that schema's own
        required keys and const-pinned fields."""
        rules = {rules!r}
        for rule in rules:
            actual = getattr(self, rule["discriminator"], None)
            if actual not in rule["values"]:
                continue
            for _item in getattr(self, rule["field"], None) or []:
                _provided = (
                    set(_item.keys())
                    if isinstance(_item, dict)
                    else _item.model_fields_set | set(_item.model_extra or {{}})
                )
                for _required in rule["required"]:
                    if _required not in _provided:
                        raise ValueError(
                            f"Field {{_required!r}} is required for "
                            f"{{rule['field']}} items when "
                            f"{{rule['discriminator']}} is {{actual!r}}"
                        )
                for _const_field, _const_value in rule["consts"].items():
                    _actual_value = (
                        _item.get(_const_field)
                        if isinstance(_item, dict)
                        else getattr(_item, _const_field, None)
                    )
                    if _actual_value != _const_value:
                        raise ValueError(
                            f"Field {{_const_field!r}} must equal "
                            f"{{_const_value!r}} for {{rule['field']}} items "
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

_DATETIME_PATTERN_MARKER = "_enforce_datetime_pattern"

# Annotations the generator emits for ``format: date-time`` strings.
_DATETIME_TYPES = {"AwareDatetime", "NaiveDatetime", "datetime"}

# re.search, not fullmatch: JSON Schema patterns are unanchored, and the
# date-time pattern UCP uses is anchored only at the end.
_DATETIME_PATTERN_TEMPLATE = '''
    @field_validator("{field}", mode="before")
    def {marker}_{field}(cls, value):  # noqa: N805
        """JSON Schema pattern: match the raw date-time string, since
        pydantic cannot apply a regex to the parsed datetime."""
        pattern = {pattern!r}
        if isinstance(value, str) and re.search(pattern, value) is None:
            raise ValueError(
                f"{{value!r}} does not match the schema pattern {{pattern}}"
            )
        return value
'''


def _iter_schema_files(schema_dir):
    """Return a list of JSON schema paths from a file or directory."""
    path = Path(schema_dir)
    if path.is_file():
        return [path]
    return sorted(path.rglob("*.json"))


def _iter_root_and_def_objects(schema):
    """Yield root and top-level $defs schema dicts."""
    if not isinstance(schema, dict):
        return
    yield schema
    defs = schema.get("$defs")
    if isinstance(defs, dict):
        for def_node in defs.values():
            if isinstance(def_node, dict):
                yield def_node


def find_root_min_properties(schema_dir):
    """Map schema title -> minProperties for root-level object constraints."""
    found = {}
    for path in _iter_schema_files(schema_dir):
        try:
            schema = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for obj in _iter_root_and_def_objects(schema):
            minimum = obj.get("minProperties")
            if not minimum or not obj.get("properties"):
                continue
            title = obj.get("title")
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
    for path in _iter_schema_files(schema_dir):
        try:
            schema = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for obj in _iter_root_and_def_objects(schema):
            maximum = obj.get("maxProperties")
            if not isinstance(maximum, int) or not obj.get("properties"):
                continue
            title = obj.get("title")
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


def _resolve_property_names_pattern(prop_names, schema_path, root_schema=None):
    """Return the key pattern a ``propertyNames`` node enforces, or ``None``.

    Reads an inline ``pattern`` directly, or follows a ``$ref`` to an internal
    ``#/$defs/<Name>`` entry or external schema file's root ``pattern`` (e.g.
    ``reverse_domain_name.json``) so the pattern is never duplicated here.
    """
    if not isinstance(prop_names, dict):
        return None
    inline = prop_names.get("pattern")
    if isinstance(inline, str):
        return inline
    ref = prop_names.get("$ref")
    if not isinstance(ref, str):
        return None
    if ref.startswith("#/$defs/") and isinstance(root_schema, dict):
        def_key = ref.removeprefix("#/$defs/")
        referenced = (root_schema.get("$defs") or {}).get(def_key)
        if isinstance(referenced, dict) and isinstance(
            referenced.get("pattern"), str
        ):
            return referenced["pattern"]
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

    def walk(node, path_str, root_schema):
        if not isinstance(node, dict):
            if isinstance(node, list):
                for item in node:
                    walk(item, path_str, root_schema)
            return
        props = node.get("properties")
        if "propertyNames" in node and isinstance(props, dict) and props:
            pattern = _resolve_property_names_pattern(
                node["propertyNames"], path_str, root_schema
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
                if not (pattern.startswith("^") and pattern.endswith("$")):
                    sys.stderr.write(
                        f"  ! {path_str}: propertyNames pattern {pattern!r} is "
                        "not ^/$-anchored; fullmatch enforcement may be "
                        "stricter than JSON Schema search semantics\n"
                    )
                found[_alias_name(title)] = pattern
        for value in node.values():
            walk(value, path_str, root_schema)

    for path in _iter_schema_files(schema_dir):
        try:
            schema = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        walk(schema, str(path), schema)
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
    class_re = re.compile(rf"^class {re.escape(class_name)}\(", re.M)
    match = class_re.search(source)
    if not match:
        return source
    # The class body ends at the next top-level statement or EOF.
    tail = re.compile(r"^\S", re.M)
    end_match = tail.search(source, match.end())
    end = end_match.start() if end_match else len(source)
    if f"def {_MARKER}(" in source[match.start() : end]:
        return source
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
    class_re = re.compile(rf"^class {re.escape(class_name)}\(", re.M)
    match = class_re.search(source)
    if not match:
        return source
    # The class body ends at the next top-level statement or EOF.
    tail = re.compile(r"^\S", re.M)
    end_match = tail.search(source, match.end())
    end = end_match.start() if end_match else len(source)
    if f"def {_MAX_MARKER}(" in source[match.start() : end]:
        return source
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
    for path in _iter_schema_files(schema_dir):
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
    """Convert a string (snake, kebab, space-separated, or PascalCase) to CamelCase."""
    parts = re.split(r"[^a-zA-Z0-9]", string)
    return "".join(p[0].upper() + p[1:] for p in parts if p)


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
            f"        _actual = (_item.get({field!r}) if isinstance(_item, dict)",
            f"                   else getattr(_item, {field!r}, None))",
            "        if _actual in _excluded:",
            "            continue",
        ]
        for required in item_condition["required"]:
            lines += [
                f"        if isinstance(_item, dict) and {required!r} not in _item:",
                f'            raise ValueError("Field {required!r} is required for custom {field}")',
                f"        if not isinstance(_item, dict) and {required!r} not in (_item.model_fields_set | set(_item.model_extra or {{}})):",
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
    if item_condition:
        out = _inject_item_condition_on_item_class(
            out, f"{alias_name}Item", item_condition
        )
    return _ensure_pydantic_import(out, "AfterValidator")


_ITEM_COND_MARKER = "_enforce_item_required_condition"

_ITEM_COND_TEMPLATE = '''
    @model_validator(mode="after")
    def {marker}(self):
        """JSON Schema if/then: require {required!r} when {field!r} is custom."""
        if getattr(self, {field!r}, None) not in {excluded!r}:
            _present = self.model_fields_set | set(self.model_extra or {{}})
            for _req in {required!r}:
                if _req not in _present:
                    raise ValueError(
                        f"Field {{_req!r}} is required for custom {field}"
                    )
        return self
'''


def _inject_item_condition_on_item_class(source, item_class_name, cond):
    """Inject item_condition model_validator onto ``item_class_name`` if present."""
    span = _class_body_span(source, item_class_name)
    if span is None:
        return source
    if f"def {_ITEM_COND_MARKER}(" in source[span[0] : span[1]]:
        return source
    method = _ITEM_COND_TEMPLATE.format(
        marker=_ITEM_COND_MARKER,
        field=cond["field"],
        excluded=cond["excluded"],
        required=cond["required"],
    )
    body = source[: span[1]].rstrip("\n")
    rest = source[span[1] :]
    out = body + "\n" + method + ("\n" + rest if rest else "")
    return _ensure_pydantic_import(out, "model_validator")


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
        is_required_rule = (
            isinstance(then, dict)
            and "required" in then
            and "properties" not in then
        )
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

    for path in _iter_schema_files(schema_dir):
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
        then_props = then.get("properties") if isinstance(then, dict) else None
        is_retyping_only = (
            isinstance(then_props, dict)
            and bool(then_props)
            and all(_is_ref_array(v) for v in then_props.values())
        )
        is_bounds_rule = (
            isinstance(then, dict)
            and "properties" in then
            and "required" not in then
            and not is_retyping_only
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
        if isinstance(node.get("items"), dict):
            walk(node["items"], current_class_name, path_str)
        defs = node.get("$defs")
        if isinstance(defs, dict):
            for def_name, def_node in defs.items():
                walk(def_node, _to_camel_case(def_name), path_str)
        for key in ("allOf", "anyOf", "oneOf"):
            if isinstance(node.get(key), list):
                for item in node[key]:
                    walk(item, current_class_name, path_str, scope)

    for path in _iter_schema_files(schema_dir):
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


def _resolve_referenced_shape(ref, schema_path):
    """Load ``ref`` (relative to ``schema_path``) and return its own
    (root-level, post-merge) required keys and const-pinned properties.

    Returns ``None`` if the file cannot be loaded. Deliberately shallow: it
    reads only the referenced schema's own ``required``/``properties``, not
    anything it in turn ``allOf``-references (e.g. shipping_destination.json
    ``allOf``-refs postal_address.json, whose fields are not inspected) --
    an approximation, not a full re-derivation of the retyped shape.
    """
    file_part = ref.split("#", 1)[0]
    if not file_part:
        return None
    target_path = (Path(schema_path).parent / file_part).resolve()
    try:
        referenced = json.loads(target_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        sys.stderr.write(
            f"  ! {schema_path}: retyped $ref {ref!r} could not be loaded; "
            "rule skipped\n"
        )
        return None
    if not isinstance(referenced, dict):
        return None
    required = sorted(
        name for name in referenced.get("required", []) if isinstance(name, str)
    )
    consts = {
        name: prop["const"]
        for name, prop in (referenced.get("properties") or {}).items()
        if isinstance(prop, dict) and "const" in prop
    }
    return {"required": required, "consts": consts}


def _is_ref_array(node):
    """True when ``node`` is an array property typed via ``items.$ref``."""
    return (
        isinstance(node, dict)
        and node.get("type") == "array"
        and isinstance(node.get("items"), dict)
        and isinstance(node["items"].get("$ref"), str)
    )


def _describe_retyping_branch(branch, properties, schema_path):
    """Describe one allOf if/then branch that retypes an array property's
    items to a schema file different from the property's own base ``$ref``.

    Mechanical and narrow by design, mirroring the other conditional
    scanners in this module: a single-key ``const``/``enum`` discriminator
    naming a property present on the enclosing object, and a ``then`` that
    narrows exactly one array property (also present on the enclosing
    object) to a different ``items.$ref``. Anything else -- multiple
    discriminators, a non-array or non-$ref field, a `then` naming a field
    absent from the enclosing object (a request variant that omits it, as
    fulfillment_method_create_request.json does for ``destinations``) --
    returns ``None`` silently: those are either a different rule shape
    (left to find_conditional_required/find_conditional_bounds, which scan
    the same branches) or legitimately inapplicable, not malformed.
    """
    if not isinstance(branch, dict) or set(branch) != {"if", "then"}:
        return None
    condition = branch["if"]
    consequence = branch["then"]
    if (
        not isinstance(condition, dict)
        or set(condition) != {"properties", "required"}
        or not isinstance(consequence, dict)
        or set(consequence) != {"properties"}
    ):
        return None
    condition_props = condition["properties"]
    condition_required = condition["required"]
    if (
        not isinstance(condition_props, dict)
        or len(condition_props) != 1
        or not isinstance(condition_required, list)
        or len(condition_required) != 1
    ):
        return None
    discriminator, predicate = next(iter(condition_props.items()))
    if condition_required != [discriminator] or not isinstance(predicate, dict):
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
    if discriminator not in properties or any(
        not isinstance(value, (str, int, float, bool)) for value in values
    ):
        return None
    consequence_props = consequence["properties"]
    if not isinstance(consequence_props, dict) or len(consequence_props) != 1:
        return None
    field, field_schema = next(iter(consequence_props.items()))
    if field not in properties or not _is_ref_array(field_schema):
        return None
    base_field_schema = properties[field]
    if not _is_ref_array(base_field_schema):
        return None
    base_ref = base_field_schema["items"]["$ref"]
    new_ref = field_schema["items"]["$ref"]
    if new_ref == base_ref:
        return None
    target = _resolve_referenced_shape(new_ref, schema_path)
    if target is None:
        return None
    return {
        "discriminator": discriminator,
        "values": values,
        "field": field,
        "required": target["required"],
        "consts": target["consts"],
    }


def find_conditional_array_retyping(schema_dir):
    """Map generated class names to array-item retyping rules.

    Complements find_conditional_required/find_conditional_bounds, which
    only handle a ``then`` that adds required fields or narrows a numeric
    range. A ``then`` that instead retypes an array PROPERTY's items to a
    schema file different from the property's own base ``$ref`` is a third
    shape the generator drops entirely: fulfillment_method.json's
    ``destinations`` stays typed to the base FulfillmentDestination
    regardless of ``type``, even though a `shipping` method's destinations
    are really ShippingDestination (postal address fields, `type` const
    `shipping_address`) and a `pickup` method's are really
    LocationDestination (`type` const `business_location`). Pydantic has no
    clean way to retype a field's item type from a source-text splice, so
    this is enforced with a runtime check instead of a static type change:
    each item is checked against the referenced schema's own required keys
    and const-pinned fields (see _resolve_referenced_shape), an
    approximation rather than a full re-derivation of the retyped type.
    """
    rules_by_class = {}

    def walk(node, current_class_name, schema_path):
        if not isinstance(node, dict):
            return
        if isinstance(node.get("title"), str):
            current_class_name = _alias_name(node["title"])
        properties = node.get("properties")
        allof = node.get("allOf")
        if isinstance(properties, dict) and isinstance(allof, list):
            for branch in allof:
                rule = _describe_retyping_branch(
                    branch, properties, schema_path
                )
                if rule is not None and current_class_name is not None:
                    rules_by_class.setdefault(current_class_name, []).append(
                        rule
                    )
        if isinstance(properties, dict):
            for name, prop in properties.items():
                walk(prop, _to_camel_case(name), schema_path)
        defs = node.get("$defs")
        if isinstance(defs, dict):
            for def_name, def_node in defs.items():
                walk(def_node, _to_camel_case(def_name), schema_path)

    for path in _iter_schema_files(schema_dir):
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
        walk(schema, initial_class, path)
    return rules_by_class


def inject_conditional_array_retyping(source, class_name, rules):
    """Inject array-item retyping checks into one generated class."""
    class_re = re.compile(rf"^class {re.escape(class_name)}\(", re.M)
    match = class_re.search(source)
    if not match:
        return source
    tail = re.compile(r"^\S", re.M)
    end_match = tail.search(source, match.end())
    end = end_match.start() if end_match else len(source)
    if f"def {_RETYPE_MARKER}(" in source[match.start() : end]:
        return source
    method = _RETYPE_TEMPLATE.format(marker=_RETYPE_MARKER, rules=rules)
    body = source[:end].rstrip("\n")
    rest = source[end:]
    out = body + "\n" + method + ("\n" + rest if rest else "")
    return _ensure_pydantic_import(out, "model_validator")


def find_root_dependent_required(schema_dir):
    """Map generated class names to root-level dependentRequired rules.

    Only complete rules over declared properties are returned. Request variants
    can project either side out; those rules are inapplicable to that generated
    class and are skipped by ``inject_dependent_required``.
    """
    found = {}
    for path in _iter_schema_files(schema_dir):
        try:
            schema = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(schema, dict):
            continue
        for obj in _iter_root_and_def_objects(schema):
            properties = obj.get("properties")
            rules = obj.get("dependentRequired")
            title = obj.get("title")
            if not isinstance(properties, dict) or not isinstance(rules, dict):
                continue
            normalized = {}
            malformed = False
            for field, required in rules.items():
                if (
                    not isinstance(field, str)
                    or not isinstance(required, list)
                    or not required
                    or not all(isinstance(name, str) for name in required)
                ):
                    malformed = True
                    break
                # Request variants may project either side out. Such a rule no
                # longer applies to that generated class and is skipped silently.
                if field in properties and all(
                    name in properties for name in required
                ):
                    normalized[field] = required
            if malformed:
                sys.stderr.write(
                    f"  ! {path}: unsupported dependentRequired rule; skipped\n"
                )
                continue
            if not normalized:
                continue
            if not isinstance(title, str) or not title:
                sys.stderr.write(
                    f"  ! {path}: root dependentRequired but no title; "
                    "cannot map to a class\n"
                )
                continue
            found[_alias_name(title)] = normalized
    return found


def inject_dependent_required(source, class_name, rules):
    """Inject root dependentRequired checks into one generated class."""
    class_re = re.compile(rf"^class {re.escape(class_name)}\(", re.M)
    match = class_re.search(source)
    if not match:
        return source
    tail = re.compile(r"^\S", re.M)
    end_match = tail.search(source, match.end())
    end = end_match.start() if end_match else len(source)
    class_body = source[match.start() : end]
    if f"def {_DEPENDENT_REQUIRED_MARKER}(" in class_body:
        return source
    declared = {
        field.group(1)
        for field in re.finditer(r"^    (\w+): [^\n]+", class_body, re.M)
    }
    allow_extra_trigger = (
        class_name.endswith("Base") and 'extra="allow"' in class_body
    )
    applicable = {
        field: required
        for field, required in rules.items()
        if (field in declared or allow_extra_trigger)
        and all(name in declared for name in required)
    }
    if not applicable:
        return source
    method = _DEPENDENT_REQUIRED_TEMPLATE.format(
        marker=_DEPENDENT_REQUIRED_MARKER,
        rules=applicable,
    )
    body = source[:end].rstrip("\n")
    rest = source[end:]
    out = body + "\n" + method + ("\n" + rest if rest else "")
    return _ensure_pydantic_import(out, "model_validator")


def find_unique_items_fields(schema_dir):
    """Map generated class names to fields carrying ``uniqueItems``.

    A schema node needs a title so its constraint can be associated with a
    generated class. Untitled nodes are resolved using their property path.
    """
    fields_by_class = {}

    def walk(node, current_class_name, path_str, is_def=False):
        if not isinstance(node, dict):
            return

        if not is_def and isinstance(node.get("title"), str):
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
                walk(def_node, _to_camel_case(def_name), path_str, is_def=True)

        # Recurse into combinators (allOf, anyOf, oneOf)
        for key in ("allOf", "anyOf", "oneOf"):
            if isinstance(node.get(key), list):
                for item in node[key]:
                    walk(item, current_class_name, path_str)

        # Recurse into conditional then branches
        if isinstance(node.get("then"), dict):
            walk(node["then"], current_class_name, path_str)

    for path in _iter_schema_files(schema_dir):
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


def _expand_unique_fields_across_type_aliases(source, unique_fields_by_class):
    """Propagate uniqueItems field sets from TypeAliasType unions to their member classes."""
    expanded = {k: set(v) for k, v in unique_fields_by_class.items()}
    alias_re = re.compile(
        r'^(\w+) = TypeAliasType\(\s*"\w+",\s*(?:Annotated\[\s*)?([^,\])]+)',
        re.M | re.S,
    )
    for match in alias_re.finditer(source):
        alias_name = match.group(1)
        fields = expanded.get(alias_name)
        if not fields:
            continue
        for member in match.group(2).split("|"):
            member_name = member.strip()
            if member_name:
                expanded.setdefault(member_name, set()).update(fields)
    return expanded


def inject_unique_items(source, unique_fields_by_class):
    """Inject uniqueness validators for list fields declared ``uniqueItems``.

    A validator is added only when both the generated class name and list
    field name match the scoped schema constraints.
    """
    if not unique_fields_by_class:
        return source
    expanded_fields = _expand_unique_fields_across_type_aliases(
        source, unique_fields_by_class
    )
    class_re = re.compile(r"^class (\w+)\(", re.M)
    matches = list(class_re.finditer(source))
    if not matches:
        return source
    new_source = source
    patched = False
    # Process from the last class to the first so earlier insert offsets
    # (computed against the original source) stay valid as text is appended.
    for match in reversed(matches):
        unique_fields = expanded_fields.get(match.group(1), set())
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


_NOT_ENUM_MARKER = "_enforce_not_enum"

_NOT_ENUM_TEMPLATE = '''
    @model_validator(mode="after")
    def {marker}(self):
        """JSON Schema not.enum: reject known variant discriminator values on open-union fallback."""
        if getattr(self, {field!r}, None) in {excluded!r}:
            raise ValueError(
                f"Field {field!r} must not be one of {excluded!r} "
                "(schema not.enum)"
            )
        return self
'''


def _class_body_span(source, class_name):
    """Return (start, end) of ``class_name`` in ``source``, or ``None``."""
    match = re.search(rf"^class {re.escape(class_name)}\(", source, re.M)
    if match is None:
        return None
    tail = re.compile(r"^\S", re.M)
    end_match = tail.search(source, match.end())
    end = end_match.start() if end_match else len(source)
    return match.start(), end


def _declared_fields(class_body):
    """Return the set of field names declared on a class body."""
    return {
        m.group(1) for m in re.finditer(r"^    (\w+): [^\n]+", class_body, re.M)
    }


def _array_contains_targets():
    """Resolve ``title -> groups`` for every model needing a contains bound.

    The authoritative (complete) groups come from the pristine schemas.
    """
    raw = (
        find_array_contains_constraints(RAW_SCHEMA_DIR)
        if RAW_SCHEMA_DIR.exists()
        else {}
    )
    if not raw:
        raw = find_array_contains_constraints(SCHEMA_DIR)
    if not raw:
        return {}
    raw_stems = sorted(raw, key=len, reverse=True)
    targets = {}
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
    py_sources = [
        path.read_text(encoding="utf-8")
        for path in sorted(OUTPUT_DIR.rglob("*.py"))
    ]
    for info in raw.values():
        base_title = info["title"]
        base_alias = _alias_name(base_title)
        targets.setdefault(
            base_title,
            {
                "groups": info["groups"],
                "item_condition": info["item_condition"],
            },
        )
        for suffix in ("CreateRequest", "UpdateRequest", "CompleteRequest"):
            variant_alias = f"{base_alias}{suffix}"
            pattern = rf"^{re.escape(variant_alias)} = TypeAliasType\("
            if any(re.search(pattern, src, re.M) for src in py_sources):
                targets.setdefault(
                    variant_alias,
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
    py_sources = [
        path.read_text(encoding="utf-8")
        for path in sorted(OUTPUT_DIR.rglob("*.py"))
    ]
    for class_name, pattern in list(patterns.items()):
        for suffix in ("CreateRequest", "UpdateRequest", "CompleteRequest"):
            variant = f"{class_name}{suffix}"
            if any(
                re.search(rf"^class {re.escape(variant)}\(", src, re.M)
                for src in py_sources
            ):
                patterns.setdefault(variant, pattern)
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


def _resolve_conditional_required_targets(source, class_name, rules):
    """Return matching class names in ``source`` for ``class_name``."""
    if re.search(rf"^class {re.escape(class_name)}\(", source, re.M):
        return [class_name]
    needed = {rule["discriminator"] for rule in rules} | {
        field for rule in rules for field in rule["required"]
    }
    candidates = []
    for match in re.finditer(
        rf"^class (\w+{re.escape(class_name)})\(", source, re.M
    ):
        candidate = match.group(1)
        span = _class_body_span(source, candidate)
        if span is None:
            continue
        if needed <= _declared_fields(source[span[0] : span[1]]):
            candidates.append(candidate)
    return candidates


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
            targets = _resolve_conditional_required_targets(
                source, class_name, rules
            )
            if not targets:
                continue
            updated = source
            for target in targets:
                updated = inject_conditional_required(updated, target, rules)
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


def _resolve_conditional_bounds_targets(source, class_name, rules):
    """Return matching class names (including numeric suffixes and request slices)."""
    pattern = rf"^class ({re.escape(class_name)}(?:\d+|CreateRequest|UpdateRequest|CompleteRequest)?)\("
    candidate_names = [m.group(1) for m in re.finditer(pattern, source, re.M)]
    targets = []
    for candidate in candidate_names:
        span = _class_body_span(source, candidate)
        if span is None:
            continue
        declared = _declared_fields(source[span[0] : span[1]])
        applicable = [
            rule
            for rule in rules
            if rule["discriminator"] in declared
            and all(field in declared for field in rule["bounds"])
        ]
        if applicable:
            targets.append((candidate, applicable))
    return targets


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
            targets = _resolve_conditional_bounds_targets(
                source, class_name, rules
            )
            if not targets:
                continue
            updated = source
            for target, applicable_rules in targets:
                updated = inject_conditional_bounds(
                    updated, target, applicable_rules
                )
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


def _patch_conditional_array_retyping():
    """Inject array-item retyping checks; return counts and status."""
    rules_by_class = find_conditional_array_retyping(SCHEMA_DIR)
    if not rules_by_class:
        sys.stdout.write(
            "postprocess: no conditional array-item retyping rules found\n"
        )
        return 0, 0
    patched = 0
    for class_name, rules in sorted(rules_by_class.items()):
        hits = []
        for path in sorted(OUTPUT_DIR.rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            if re.search(
                rf"^{re.escape(class_name)} = TypeAliasType\(", source, re.M
            ):
                hits.append(path)
                continue
            if not re.search(
                rf"^class {re.escape(class_name)}\(", source, re.M
            ):
                continue
            updated = inject_conditional_array_retyping(
                source, class_name, rules
            )
            if updated != source:
                path.write_text(updated, encoding="utf-8")
                patched += 1
            hits.append(path)
        label = (
            ", ".join(str(path) for path in hits) or "NO GENERATED CLASS FOUND"
        )
        sys.stdout.write(
            f"  conditional array-item retyping on '{class_name}' -> {label}\n"
        )
        if not hits:
            return patched, 1
    return patched, 0


def _dependent_required_target_classes(source, class_name):
    """Return all generated class variants of ``class_name`` in ``source``."""
    base_name = class_name[:-4] if class_name.endswith("Base") else class_name
    suffixes = (
        "",
        "Base",
        "CreateRequest",
        "UpdateRequest",
        "CompleteRequest",
        "CreateRequestBase",
        "UpdateRequestBase",
        "CompleteRequestBase",
    )
    candidates = [f"{base_name}{s}" for s in suffixes]
    return [
        name
        for name in candidates
        if _class_body_span(source, name) is not None
    ]


def _patch_dependent_required():
    """Inject dependentRequired validators; return counts and status."""
    rules_by_class = find_root_dependent_required(SCHEMA_DIR)
    if not rules_by_class:
        sys.stdout.write(
            "postprocess: no dependentRequired constraints found\n"
        )
        return 0, 0
    patched = 0
    for class_name, rules in sorted(rules_by_class.items()):
        hits = []
        for path in sorted(OUTPUT_DIR.rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            targets = _dependent_required_target_classes(source, class_name)
            if not targets:
                if re.search(
                    rf"^{re.escape(class_name)} = TypeAliasType\(", source, re.M
                ):
                    hits.append(path)
                continue
            updated = source
            any_already = False
            for target in targets:
                span = _class_body_span(updated, target)
                if (
                    span is not None
                    and f"def {_DEPENDENT_REQUIRED_MARKER}("
                    in updated[span[0] : span[1]]
                ):
                    any_already = True
                    continue
                updated = inject_dependent_required(updated, target, rules)
            if updated != source:
                path.write_text(updated, encoding="utf-8")
                patched += 1
                hits.append(path)
            elif any_already:
                hits.append(path)
        label = (
            ", ".join(str(path) for path in hits) or "NO APPLICABLE CLASS FOUND"
        )
        sys.stdout.write(f"  dependentRequired on '{class_name}' -> {label}\n")
        if not hits:
            return patched, 1
    return patched, 0


def _patch_unique_items():
    """Inject uniqueItems validators; return (patched_count, exit_code)."""
    unique_fields_by_class = find_unique_items_fields(SCHEMA_DIR)
    if not unique_fields_by_class:
        sys.stdout.write("postprocess: no uniqueItems constraints found\n")
        return 0, 0
    for class_name, fields in list(unique_fields_by_class.items()):
        for suffix in ("CreateRequest", "UpdateRequest", "CompleteRequest"):
            unique_fields_by_class.setdefault(
                f"{class_name}{suffix}", set(fields)
            )
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


def _extract_variant_discriminator_tags(var_body, base_fields):
    """Extract (disc_field, tags) from Literal annotations in a variant body."""
    disc_field = None
    tags = set()
    for lit_match in re.finditer(
        r"^    (\w+): [^\n]*\bLiteral\[([^\]]+)\]", var_body, re.M
    ):
        field_name = lit_match.group(1)
        if field_name not in base_fields:
            continue
        disc_field = field_name
        for q1, q2 in re.findall(r'"([^"]+)"|\'([^\']+)\'', lit_match.group(2)):
            tags.add(q1 or q2)
    return disc_field, tags


def _find_open_union_base_exclusions(source):
    """Discover ``<Union>Base`` discriminator ``not.enum`` rules in ``source``."""
    exclusions = {}
    alias_re = re.compile(
        r"^(\w+) = TypeAliasType\(\s*\"\w+\",\s*Annotated\[\s*([^,\]]+),",
        re.M | re.S,
    )
    for match in alias_re.finditer(source):
        members = [m.strip() for m in match.group(2).split("|") if m.strip()]
        if len(members) < 2 or not members[-1].endswith("Base"):
            continue
        base_class = members[-1]
        base_span = _class_body_span(source, base_class)
        if base_span is None:
            continue
        base_fields = _declared_fields(source[base_span[0] : base_span[1]])
        disc_field = None
        tags = set()
        for variant in members[:-1]:
            var_span = _class_body_span(source, variant)
            if var_span is None:
                continue
            field_name, var_tags = _extract_variant_discriminator_tags(
                source[var_span[0] : var_span[1]], base_fields
            )
            if field_name:
                disc_field = field_name
                tags.update(var_tags)
        if disc_field and tags:
            exclusions[base_class] = {
                "field": disc_field,
                "excluded": sorted(tags),
            }
    return exclusions


def inject_open_union_not_enum(source, class_name, field, excluded):
    """Inject ``not.enum`` discriminator guard onto ``class_name``."""
    span = _class_body_span(source, class_name)
    if span is None:
        return source
    if f"def {_NOT_ENUM_MARKER}(" in source[span[0] : span[1]]:
        return source
    method = _NOT_ENUM_TEMPLATE.format(
        marker=_NOT_ENUM_MARKER,
        field=field,
        excluded=excluded,
    )
    body = source[: span[1]].rstrip("\n")
    rest = source[span[1] :]
    out = body + "\n" + method + ("\n" + rest if rest else "")
    return _ensure_pydantic_import(out, "model_validator")


def _patch_open_union_not_enum():
    """Inject ``not.enum`` discriminator checks on ``<Union>Base`` classes.

    Also performs single-file bundle hygiene:
    1. ``datamodel-code-generator`` emits ``typing.Dict[...]`` inside
       ``__pydantic_extra__`` annotations on ``extra="allow"`` models with typed
       ``additionalProperties`` (e.g. ``Actions``) even when
       ``--use-standard-collections`` is enabled; normalizing ``Dict[`` to
       built-in ``dict[`` keeps Ruff ``UP006``/``UP035`` clean.
    2. Strips the synthetic root bundle wrapper ``UCPSchemaTypes`` /
       ``UcpSchemaTypes = TypeAliasType(...)`` emitted from the bundle's root
       ``"title": "UCP Schema Types"``.
    """
    patched = 0
    for path in sorted(OUTPUT_DIR.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        updated = re.sub(r"\bDict\[", "dict[", source)
        updated = re.sub(
            r"^(from typing import [^\n]*?),\s*Dict\b",
            r"\1",
            updated,
            flags=re.M,
        )
        updated = re.sub(
            r"^(?:Ucp|UCP)SchemaTypes = TypeAliasType\((?:[^\n]*\)|.*?\n\))\n*",
            "",
            updated,
            flags=re.M | re.S,
        )
        exclusions = _find_open_union_base_exclusions(updated)
        for base_class, rule in sorted(exclusions.items()):
            updated = inject_open_union_not_enum(
                updated, base_class, rule["field"], rule["excluded"]
            )
            sys.stdout.write(
                f"  not.enum {rule['field']}!={rule['excluded']} on '{base_class}' -> {path}\n"
            )
        if updated != source:
            path.write_text(updated, encoding="utf-8")
            patched += 1
    return patched, 0


_FORBIDDEN_KEYS_MARKER = "_enforce_forbidden_keys"

_FORBIDDEN_KEYS_TEMPLATE = '''
    @model_validator(mode="after")
    def {marker}(self):
        """JSON Schema not: reject forbidden property keys."""
        _present = self.model_fields_set | set(self.model_extra or {{}})
        for _key in {forbidden!r}:
            if _key in _present:
                raise ValueError(
                    f"Field {{_key!r}} is forbidden by schema 'not' constraint"
                )
        return self
'''


def _extract_forbidden_keys(not_node):
    """Extract forbidden key names from a ``not`` schema dict, or return []."""
    if not isinstance(not_node, dict):
        return []
    if set(not_node) == {"required"} and isinstance(
        not_node.get("required"), list
    ):
        if len(not_node["required"]) == 1 and isinstance(
            not_node["required"][0], str
        ):
            return [not_node["required"][0]]
        return []
    if set(not_node) == {"anyOf"} and isinstance(not_node.get("anyOf"), list):
        keys = []
        for branch in not_node["anyOf"]:
            if (
                isinstance(branch, dict)
                and set(branch) == {"required"}
                and isinstance(branch.get("required"), list)
                and len(branch["required"]) == 1
                and isinstance(branch["required"][0], str)
            ):
                keys.append(branch["required"][0])
            else:
                return []
        return sorted(set(keys))
    return []


def find_forbidden_keys(schema_dir):
    """Map generated class names to forbidden property keys from ``not`` rules."""
    found = {}

    def walk(node, current_class_name, is_def=False):
        if not isinstance(node, dict):
            return
        if not is_def and isinstance(node.get("title"), str):
            current_class_name = _alias_name(node["title"])
        forbidden = _extract_forbidden_keys(node.get("not"))
        if forbidden and current_class_name:
            found.setdefault(current_class_name, set()).update(forbidden)
        for name, prop in (node.get("properties") or {}).items():
            if isinstance(prop, dict):
                walk(prop, _to_camel_case(name))
        for def_name, def_node in (node.get("$defs") or {}).items():
            if isinstance(def_node, dict):
                walk(def_node, _to_camel_case(def_name), is_def=True)
        for key in ("allOf", "anyOf", "oneOf"):
            if isinstance(node.get(key), list):
                for item in node[key]:
                    walk(item, current_class_name)

    for path in _iter_schema_files(schema_dir):
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
        walk(schema, initial_class)

    return {cls: sorted(keys) for cls, keys in found.items()}


def inject_forbidden_keys(source, class_name, forbidden):
    """Inject forbidden-key validator onto ``class_name``."""
    span = _class_body_span(source, class_name)
    if span is None:
        return source
    if f"def {_FORBIDDEN_KEYS_MARKER}(" in source[span[0] : span[1]]:
        return source
    method = _FORBIDDEN_KEYS_TEMPLATE.format(
        marker=_FORBIDDEN_KEYS_MARKER,
        forbidden=sorted(forbidden),
    )
    body = source[: span[1]].rstrip("\n")
    rest = source[span[1] :]
    out = body + "\n" + method + ("\n" + rest if rest else "")
    return _ensure_pydantic_import(out, "model_validator")


def _patch_forbidden_keys():
    """Inject forbidden-key validators for ``not`` required rules."""
    rules_by_class = find_forbidden_keys(SCHEMA_DIR)
    if not rules_by_class:
        sys.stdout.write("postprocess: no forbidden-key constraints found\n")
        return 0, 0
    patched = 0
    for class_name, forbidden in sorted(rules_by_class.items()):
        hits = []
        for path in sorted(OUTPUT_DIR.rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            if not re.search(
                rf"^class {re.escape(class_name)}\(", source, re.M
            ):
                continue
            updated = inject_forbidden_keys(source, class_name, forbidden)
            if updated != source:
                path.write_text(updated, encoding="utf-8")
                patched += 1
            hits.append(path)
        label = ", ".join(str(h) for h in hits) or "NO GENERATED CLASS FOUND"
        sys.stdout.write(
            f"  forbidden keys {forbidden} on '{class_name}' -> {label}\n"
        )
        if not hits:
            return patched, 1
    return patched, 0


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
        for def_name, child in (node.get("$defs") or {}).items():
            visit(child, _to_camel_case(def_name))

    for path in _iter_schema_files(schema_dir):
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


def _resolve_extra_forbid_targets(source, class_name):
    """Return matching class names in ``source`` for ``class_name`` (including parent-qualified names)."""
    if re.search(rf"^class {re.escape(class_name)}\(", source, re.M):
        return [class_name]
    return [
        m.group(1)
        for m in re.finditer(
            rf"^class (\w+{re.escape(class_name)})\(", source, re.M
        )
    ]


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
            targets = _resolve_extra_forbid_targets(source, class_name)
            if not targets:
                continue
            updated = source
            for target in targets:
                updated = inject_extra_forbid(updated, target)
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


def _mentions_datetime(annotation):
    """True if an annotation expression names a generated datetime type."""
    return any(
        isinstance(node, ast.Name) and node.id in _DATETIME_TYPES
        for node in ast.walk(annotation)
    )


def _pattern_keyword(node):
    """Return the string ``pattern=`` keyword of a ``Field(...)`` call."""
    if not (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "Field"
    ):
        return None
    for keyword in node.keywords:
        if (
            keyword.arg == "pattern"
            and isinstance(keyword.value, ast.Constant)
            and isinstance(keyword.value.value, str)
        ):
            return keyword
    return None


def _datetime_pattern_fields(tree):
    """Yield ``(class_node, statement)`` for patterned datetime fields."""
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        for stmt in node.body:
            if (
                isinstance(stmt, ast.AnnAssign)
                and isinstance(stmt.target, ast.Name)
                and _mentions_datetime(stmt.annotation)
                and _pattern_keyword(stmt.value) is not None
            ):
                yield node, stmt


def find_datetime_pattern_fields(source):
    """Find datetime fields the generator gave a regex ``pattern``.

    Returns ``(fields, unsupported)``. ``fields`` lists ``(class_name, field,
    pattern)`` for class attributes written as ``name: <datetime> =
    Field(..., pattern=...)``, which ``inject_datetime_patterns`` rewrites.
    ``unsupported`` lists the line of every other datetime pattern (e.g. an
    ``Annotated[AwareDatetime, Field(pattern=...)]`` type alias for array
    items), which is left as generated and must be reported.
    """
    tree = ast.parse(source)
    fields = [
        (
            cls.name,
            stmt.target.id,
            _pattern_keyword(stmt.value).value.value,
        )
        for cls, stmt in _datetime_pattern_fields(tree)
    ]
    unsupported = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and node.value.id == "Annotated"
        and isinstance(node.slice, ast.Tuple)
        and _mentions_datetime(node.slice.elts[0])
        and any(_pattern_keyword(meta) for meta in node.slice.elts[1:])
    ]
    return fields, unsupported


def _source_offset(source, lineno, col_offset):
    """Map an ast position (1-based line, UTF-8 byte column) to an index."""
    start = 0
    for _ in range(lineno - 1):
        start = source.index("\n", start) + 1
    line = source[start:].split("\n", 1)[0]
    return start + len(line.encode("utf-8")[:col_offset].decode("utf-8"))


def inject_datetime_patterns(source):
    """Move each datetime field's regex ``pattern`` into a before-validator.

    The ``pattern`` argument is removed from the field's ``Field(...)`` call,
    and the whole call when only the default remains, matching how the
    generator writes an unconstrained field. A ``field_validator(mode="before")``
    carrying the same pattern is appended to the class. Fields are rewritten
    one at a time, re-parsing in between so ast offsets stay valid.
    """
    while True:
        cls, stmt = next(
            _datetime_pattern_fields(ast.parse(source)), (None, None)
        )
        if stmt is None:
            return source
        call = stmt.value
        keyword = _pattern_keyword(call)
        call.keywords = [kw for kw in call.keywords if kw is not keyword]
        default = call.args[0] if len(call.args) == 1 else None
        if (
            not call.keywords
            and isinstance(default, ast.Constant)
            and default.value is Ellipsis
        ):
            value = ""
        elif (
            not call.keywords
            and isinstance(default, ast.Constant)
            and default.value is None
        ):
            value = " = None"
        else:
            value = f" = {ast.unparse(call)}"
        start = _source_offset(
            source, stmt.annotation.end_lineno, stmt.annotation.end_col_offset
        )
        end = _source_offset(source, call.end_lineno, call.end_col_offset)
        source = source[:start] + value + source[end:]

        match = re.search(rf"^class {re.escape(cls.name)}\(", source, re.M)
        end_match = re.compile(r"^\S", re.M).search(source, match.end())
        body_end = end_match.start() if end_match else len(source)
        method = _DATETIME_PATTERN_TEMPLATE.format(
            marker=_DATETIME_PATTERN_MARKER,
            field=stmt.target.id,
            pattern=keyword.value.value,
        )
        prefix = source[:body_end].rstrip("\n")
        suffix = source[body_end:]
        source = prefix + "\n" + method + ("\n" + suffix if suffix else "")
        source = _ensure_pydantic_import(source, "field_validator")
        source = _ensure_stdlib_import(source, "import re")


def _patch_datetime_patterns():
    """Move datetime regex patterns into validators; return counts, status."""
    patched = 0
    status = 0
    for path in sorted(OUTPUT_DIR.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        fields, unsupported = find_datetime_pattern_fields(source)
        for line in unsupported:
            sys.stderr.write(
                f"  ! {path}:{line}: regex pattern on a datetime type alias "
                "is not rewritten; validating it raises TypeError\n"
            )
            status = 1
        if not fields:
            continue
        path.write_text(inject_datetime_patterns(source), encoding="utf-8")
        patched += 1
        names = ", ".join(f"{cls}.{field}" for cls, field, _ in fields)
        sys.stdout.write(f"  date-time pattern on {names} -> {path}\n")
    if not patched and not status:
        sys.stdout.write(
            "postprocess: no regex patterns on datetime fields found\n"
        )
    return patched, status


def main(argv=None):
    """Main entry point to scan schemas and patch generated models."""
    global SCHEMA_DIR, OUTPUT_DIR, RAW_SCHEMA_DIR
    args = sys.argv[1:] if argv is None else list(argv)
    if len(args) >= 1:
        SCHEMA_DIR = Path(args[0])
    if len(args) >= 2:
        OUTPUT_DIR = Path(args[1])
    if len(args) >= 3:
        RAW_SCHEMA_DIR = Path(args[2])
    patched_mp, rc_mp = _patch_min_properties()
    patched_xp, rc_xp = _patch_max_properties()
    patched_pn, rc_pn = _patch_property_names()
    patched_ac, rc_ac = _patch_array_contains()
    patched_cr, rc_cr = _patch_conditional_required()
    patched_cb, rc_cb = _patch_conditional_bounds()
    patched_rt, rc_rt = _patch_conditional_array_retyping()
    patched_dr, rc_dr = _patch_dependent_required()
    patched_ui, rc_ui = _patch_unique_items()
    patched_ef, rc_ef = _patch_extra_forbid()
    patched_ne, rc_ne = _patch_open_union_not_enum()
    patched_fk, rc_fk = _patch_forbidden_keys()
    patched_dp, rc_dp = _patch_datetime_patterns()
    total = (
        patched_mp
        + patched_xp
        + patched_pn
        + patched_ac
        + patched_cr
        + patched_cb
        + patched_rt
        + patched_dr
        + patched_ui
        + patched_ef
        + patched_ne
        + patched_fk
        + patched_dp
    )
    sys.stdout.write(f"postprocess: {total} module(s) patched\n")
    return (
        rc_mp
        or rc_xp
        or rc_pn
        or rc_ac
        or rc_cr
        or rc_cb
        or rc_rt
        or rc_dr
        or rc_ui
        or rc_ef
        or rc_ne
        or rc_fk
        or rc_dp
    )


if __name__ == "__main__":
    sys.exit(main())
