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

import json
import copy
from pathlib import Path
import sys

# --- I/O Helpers ---


def load_json(path):
    """Loads JSON data from a file."""
    with Path(path).open(encoding="utf-8") as f:
        return json.load(f)


def save_json(data, path):
    """Saves data to a JSON file with standard indentation."""
    with Path(path).open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# --- Traversal Helper ---


def iter_nodes(root):
    """
    Iteratively yields all dictionary or list nodes in a JSON tree.
    This replaces multiple manual stack-walking implementations.
    """
    stack = [root]
    visited = {id(root)}
    while stack:
        curr = stack.pop()
        yield curr

        # Identify children for the next iteration
        children = []
        if isinstance(curr, dict):
            children = curr.values()
        elif isinstance(curr, list):
            children = curr

        for child in children:
            if isinstance(child, (dict, list)) and id(child) not in visited:
                visited.add(id(child))
                stack.append(child)


# --- Reference Resolution ---


def resolve_local_ref(ref, root):
    """
    Resolves a local JSON pointer (e.g., #/$defs/name) within the same document.
    Returns the resolved schema fragment or None if invalid.
    """
    if not isinstance(ref, str) or not ref.startswith("#/"):
        return None

    parts = ref.split("/")
    current = root
    for part in parts[1:]:
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit():
            idx = int(part)
            if 0 <= idx < len(current):
                current = current[idx]
            else:
                return None
        else:
            return None
    return current


# --- Schema Normalization and Flattening ---


def _process_all_of_item(item, node, root, state):
    """Processes a single allOf item and updates the shared merge state."""
    # Resolve local references (internal inheritance) before merging
    if isinstance(item, dict) and "$ref" in item:
        resolved = resolve_local_ref(item["$ref"], root)
        if resolved:
            item = copy.deepcopy(resolved)
        else:
            # Keep external refs; we'll handle them in variant generation if needed
            state["remaining_refs"].append(item)
            return

    if not isinstance(item, dict):
        return

    # Extract polymorphic branches (anyOf, oneOf) to keep the node flat
    for poly_key in ["anyOf", "oneOf"]:
        if poly_key in item:
            state["poly_branches"].setdefault(poly_key, []).extend(
                item.pop(poly_key)
            )

    # Merge core property definitions and requirements
    if "properties" in item:
        state["merged_properties"].update(item["properties"])
    if "required" in item:
        for req in item["required"]:
            if req not in state["merged_required"]:
                state["merged_required"].append(req)

    # Carry over generic metadata (title, description, etc.) if not defined in the base
    reserved_keys = {
        "properties",
        "required",
        "allOf",
        "$ref",
        "anyOf",
        "oneOf",
    }
    for k, v in item.items():
        if k not in reserved_keys and k not in node:
            node[k] = v


def _apply_merged_state(node, state):
    """Applies accumulated properties and branches to the node."""
    if state["merged_properties"]:
        node.setdefault("properties", {}).update(state["merged_properties"])

    if state["merged_required"]:
        existing = node.setdefault("required", [])
        for r in state["merged_required"]:
            if r not in existing:
                existing.append(r)

    # Re-insert any combined polymorphic branches
    for k, branches in state["poly_branches"].items():
        node.setdefault(k, []).extend(branches)

    # If some refs couldn't be resolved locally, put them back into a slim allOf
    if state["remaining_refs"]:
        node["allOf"] = state["remaining_refs"]


def merge_all_of_to_node(node, root):
    """
    Merges 'allOf' components into the node itself.

    RATIONALE: Code generators (like datamodel-codegen) often create cleaner Pydantic
    models if inheritance is flattened at the schema level rather than relying on
    complex 'allOf' chains which can lead to redundant intermediate classes.
    """
    if "allOf" not in node:
        return

    all_of_sources = node.pop("allOf")
    state = {
        "merged_properties": {},
        "merged_required": [],
        "poly_branches": {},
        "remaining_refs": [],
    }

    for item in all_of_sources:
        _process_all_of_item(item, node, root, state)

    _apply_merged_state(node, state)


def distribute_properties_to_branches(node):
    """
    Inherits base properties/requirements into anyOf/oneOf branches.

    RATIONALE: This ensures that each branch of a union is a self-contained, valid
    model in Pydantic. Without this, a generated union model might miss required
    common fields if it's treated as a pure 'oneOf' alternative.
    """
    if "properties" not in node:
        return

    base_props = node["properties"]
    base_req = node.get("required", [])
    base_type = node.get("type")

    for poly_key in ["anyOf", "oneOf"]:
        if poly_key not in node:
            continue

        updated_branches = []
        for branch in node[poly_key]:
            if not isinstance(branch, dict):
                updated_branches.append(branch)
                continue

            # Branch properties override common base properties
            new_branch = copy.deepcopy(branch)
            branch_props = new_branch.setdefault("properties", {})
            combined_props = copy.deepcopy(base_props)
            combined_props.update(branch_props)
            new_branch["properties"] = combined_props

            # Combine union and base required field lists
            new_branch["required"] = list(
                set(base_req + new_branch.get("required", []))
            )

            # Ensure the branch knows its JSON type (usually 'object') if inheriting from common base
            if "type" not in new_branch and base_type:
                new_branch["type"] = base_type

            updated_branches.append(new_branch)
        node[poly_key] = updated_branches


def flatten_entity_reference(node, entity_definition):
    """
    Replaces $ref to 'ucp.json#/$defs/entity' with actual logic.
    This effectively converts 'Entity' inheritance into direct 'BaseModel' fields.
    """
    if "allOf" not in node:
        return

    filtered_all_of = []
    for item in node["allOf"]:
        is_entity_ref = isinstance(item, dict) and item.get(
            "$ref", ""
        ).endswith("ucp.json#/$defs/entity")
        if is_entity_ref:
            if not entity_definition:
                raise ValueError(
                    "Node requires 'ucp.json#/$defs/entity' but no entity definition was provided."
                )
            # Inline a copy; strip name to prevent unwanted class generation for the base
            e_copy = copy.deepcopy(entity_definition)
            e_copy.pop("title", None)
            e_copy.pop("description", None)
            filtered_all_of.append(e_copy)
        else:
            filtered_all_of.append(item)
    node["allOf"] = filtered_all_of


def preprocess_full_schema(schema, entity_def=None):
    """
    Main entry point for normalizing a single schema file.
    Uses bottom-up iteration to ensure nested structures are flat before parents process them.
    """
    # 1. Discovery: find all dictionaries in the tree
    nodes = [n for n in iter_nodes(schema) if isinstance(n, dict)]

    # 2. Execution: process in reverse (approximate bottom-to-top)
    for node in reversed(nodes):
        if entity_def:
            flatten_entity_reference(node, entity_def)
        merge_all_of_to_node(node, schema)
        distribute_properties_to_branches(node)


# --- Dotted $defs Flattening ---


def _rewrite_local_defs_refs(node, rename_map):
    """Walks a schema tree and rewrites local $defs refs whose target was renamed."""
    prefix = "#/$defs/"
    for n in iter_nodes(node):
        if not isinstance(n, dict):
            continue
        ref = n.get("$ref")
        if not isinstance(ref, str) or not ref.startswith(prefix):
            continue
        rest = ref[len(prefix) :]
        name, sep, tail = rest.partition("/")
        if name in rename_map:
            n["$ref"] = prefix + rename_map[name] + (sep + tail if sep else "")


def _rewrite_external_defs_refs(schema_path, schema, global_rename_maps):
    """Walks a schema tree and rewrites external $defs refs whose target was renamed."""
    path = Path(schema_path)
    for n in iter_nodes(schema):
        if not isinstance(n, dict):
            continue
        ref = n.get("$ref")
        if not isinstance(ref, str):
            continue
        if "#" not in ref:
            continue

        file_part, fragment_part = ref.split("#", 1)
        if not file_part or not file_part.endswith(".json"):
            continue

        prefix = "/$defs/"
        if not fragment_part.startswith(prefix):
            continue

        # Resolve target schema path
        target_path = (path.parent / file_part).resolve()
        target_path_str = str(target_path)

        if target_path_str not in global_rename_maps:
            continue

        rename_map = global_rename_maps[target_path_str]

        rest = fragment_part[len(prefix) :]
        name, sep, tail = rest.partition("/")
        if name in rename_map:
            new_name = rename_map[name]
            new_fragment = prefix + new_name + (sep + tail if sep else "")
            n["$ref"] = file_part + "#" + new_fragment


def flatten_dotted_defs(schema):
    """
    Renames $defs keys containing '.' so codegen does not emit nested directories.

    RATIONALE: datamodel-codegen treats dots in $def names as path separators,
    so a definition like 'dev.ucp.shopping.checkout' produces
    'dev/ucp/shopping/checkout.py' rather than a class in the parent module.
    UCP uses reverse-DNS def names as extension mount points (e.g. an extension
    schema's contribution to the base Checkout type), so those classes belong
    inline with the rest of the schema's output.

    Strategy: prefer the last dotted component as the new key (giving a clean
    class name like 'Checkout'); fall back to dot-replaced-with-underscore
    (e.g. 'DevUcpShoppingFulfillment') if the bare tail would collide with
    an existing def in the same file.
    """
    defs = schema.get("$defs")
    if not isinstance(defs, dict):
        return {}

    existing = set(defs.keys())
    rename_map = {}
    for old in list(defs.keys()):
        if "." not in old:
            continue
        tail = old.rsplit(".", 1)[-1]
        if tail and tail not in existing:
            new = tail
        else:
            new = old.replace(".", "_")
            if new in existing:
                # Both candidates collide; leave as-is rather than risk corruption
                continue
        rename_map[old] = new
        existing.discard(old)
        existing.add(new)

    if not rename_map:
        return {}

    for old, new in rename_map.items():
        defs[new] = defs.pop(old)
    _rewrite_local_defs_refs(schema, rename_map)
    return rename_map


# --- Variant Generation (Create/Update/Complete) ---


def get_required_ops(schema):
    """
    Scans a schema for the custom 'ucp_request' metadata.
    Returns a set of operation keys (e.g. {'create', 'update'}) that need distinct models.
    """
    ops = set()
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        return ops

    for data in properties.values():
        if isinstance(data, dict):
            marker = data.get("ucp_request")
            if isinstance(marker, str):
                ops.update(["create", "update"])  # Standard shortcut
            elif isinstance(marker, dict):
                ops.update(marker.keys())
    return ops


def eval_prop_inclusion(name, data, op, base_required):
    """
    Decides if a property should be included or required for a specific operation.
    Follows UCP 'ucp_request' metadata rules.
    """
    if not isinstance(data, dict):
        return True, name in base_required

    marker = data.get("ucp_request")
    include = True
    is_required = name in base_required

    if marker == "omit":
        include = False
    elif marker == "required":
        is_required = True
    elif marker == "optional":
        # A simple string "optional" marker overrides the base schema's required list for all operations.
        is_required = False
    elif isinstance(marker, dict):
        val = marker.get(op)
        if val == "omit" or val is None:
            include = False
        elif val == "required":
            is_required = True
        elif val == "optional":
            # Override base schema's required list when a field is explicitly marked optional for a specific operation.
            is_required = False

    return include, is_required


def update_variant_identity(variant_schema, op, stem):
    """Updates title and $id so that generated code doesn't have naming collisions."""
    base_title = variant_schema.get("title", stem)
    variant_schema["title"] = f"{base_title} {op.capitalize()} Request"

    if "$id" in variant_schema:
        old_id = variant_schema["$id"]
        if "/" in old_id:
            parts = old_id.split("/")
            # Support both .json and extension-less IDs
            name_ext = parts[-1].split(".", 1)
            name = name_ext[0]
            ext = name_ext[1] if len(name_ext) > 1 else "json"

            parts[-1] = f"{name}_{op}_request.{ext}"
            variant_schema["$id"] = "/".join(parts)


def rewrite_refs_to_variants(root, op, file_path, variant_needs):
    """
    Walks a schema tree and updates external links to point to variant files.
    Example: product.json -> product_create_request.json.
    """
    for node in iter_nodes(root):
        if isinstance(node, dict) and "$ref" in node:
            ref = node["$ref"]
            if "#" not in ref:  # External file reference
                abs_target = (file_path.parent / ref).resolve()
                if (
                    str(abs_target) in variant_needs
                    and op in variant_needs[str(abs_target)]
                ):
                    ref_path = Path(ref)
                    node["$ref"] = str(
                        ref_path.parent / f"{ref_path.stem}_{op}_request.json"
                    )


def _create_single_variant(
    schema, op, stem, file_path, global_variant_requirements
):
    """Creates a modified copy of the schema tailored for a specific operation."""
    variant = copy.deepcopy(schema)
    update_variant_identity(variant, op, stem)

    new_props = {}
    new_required = []
    base_req = schema.get("required", [])

    for name, data in schema.get("properties", {}).items():
        include, required = eval_prop_inclusion(name, data, op, base_req)
        if include:
            prop_data = copy.deepcopy(data)
            if isinstance(prop_data, dict):
                prop_data.pop("ucp_request", None)
                rewrite_refs_to_variants(
                    prop_data, op, file_path, global_variant_requirements
                )

            new_props[name] = prop_data
            if required:
                new_required.append(name)

    variant["properties"] = new_props
    variant["required"] = new_required
    return variant


def generate_variants(path, schema, ops, global_variant_requirements):
    """Creates specific JSON files (create/update/complete) based on ucp_request markers."""
    file_path = Path(path)
    for op in ops:
        variant = _create_single_variant(
            schema, op, file_path.stem, file_path, global_variant_requirements
        )
        out = file_path.parent / f"{file_path.stem}_{op}_request.json"
        save_json(variant, out)
        sys.stdout.write(f"Generated variant: {out}\n")


# --- Global Normalization ---


def normalize_metadata_schemas(schemas, target_dir):
    """
    Ensures ucp.json has a root union and other files point to it generically.
    This enables a unified 'ucp' metadata property across the entire SDK.
    Returns structurally corrected schemas.
    """
    ucp_path = str((target_dir / "ucp.json").resolve())
    if ucp_path in schemas:
        ucp = schemas[ucp_path]
        ucp["oneOf"] = [
            {"$ref": f"#/$defs/{d}"}
            for d in [
                "platform_schema",
                "business_schema",
                "response_checkout_schema",
                "response_order_schema",
                "response_cart_schema",
            ]
        ]

    for p_abs, s in schemas.items():
        if "ucp.json" in p_abs or "_request.json" in p_abs:
            continue
        # Find the 'ucp' property and point it to the ucp.json root
        ucp_prop = s.get("properties", {}).get("ucp", {})
        if (
            isinstance(ucp_prop, dict)
            and "$ref" in ucp_prop
            and "ucp.json" in ucp_prop["$ref"]
        ):
            ucp_prop["$ref"] = ucp_prop["$ref"].split("#")[0]
    return schemas


# --- Dependency Management ---


def extract_external_refs(schema, path):
    """Finds all relative external file references in the schema properties."""
    refs = []
    props = schema.get("properties", {})
    if not isinstance(props, dict):
        return refs

    for name, data in props.items():
        for node in iter_nodes(data):
            if isinstance(node, dict) and "$ref" in node:
                ref = node["$ref"]
                if "#" not in ref:
                    abs_path = str((path.parent / ref).resolve())
                    refs.append((name, abs_path))
    return refs


def propagate_needs_transitive(variant_needs, schema_refs, schemas):
    """
    If a parent model needs a 'create' variant, and it has a child property 'X',
    then 'X' also needs a 'create' variant so that references match.
    """
    changed = True
    while changed:
        changed = False
        for path, refs in schema_refs.items():
            if path not in variant_needs:
                continue

            for op in list(variant_needs[path]):
                for prop_name, child_path in refs:
                    if child_path not in schemas:
                        continue

                    # Only propagate if the property isn't 'omit'ted for this op
                    data = (
                        schemas[path].get("properties", {}).get(prop_name, {})
                    )
                    include, _ = eval_prop_inclusion(
                        prop_name, data, op, schemas[path].get("required", [])
                    )

                    if include:
                        target_set = variant_needs.setdefault(child_path, set())
                        if op not in target_set:
                            target_set.add(op)
                            changed = True


# --- Main Flow ---


def main():
    """
    Orchestrates the schema preprocessing pipeline:
    1. Pass 1: Local flattening (allOf) and discovery of needed variants
    2. metadata normalization: unifies ucp properties
    3. Pass 2: Transitive propagation (ensuring matched variants for linked schemas)
    4. Pass 3: Variant file generation (*_request.json).
    """
    target_dir = Path(
        sys.argv[1] if len(sys.argv) > 1 else "ucp/source/schemas"
    )
    if not target_dir.exists():
        sys.stderr.write(f"Error: Directory {target_dir} not found.\n")
        return

    schemas, schema_refs, variant_needs = {}, {}, {}
    for f in target_dir.rglob("*.json"):
        if "_request.json" in f.name:
            continue
        try:
            s = load_json(f)
            p_abs = str(f.resolve())
            schemas[p_abs] = s
        except Exception as e:
            sys.stderr.write(f"Failed to load {f}: {e}\n")

    # Phase 0: Ensure the metadata 'ucp' property is consistent across all files
    normalize_metadata_schemas(schemas, target_dir)

    ucp_path = str((target_dir / "ucp.json").resolve())
    entity_def = {}
    if ucp_path in schemas:
        entity_def = schemas[ucp_path].get("$defs", {}).get("entity", {})
    if not entity_def:
        raise ValueError(
            "Entity definition not found! 'ucp.json' must define '$defs.entity'"
        )

    global_rename_maps = {}
    # Pass 1a: Local flattening, find explicit variant markers, collect renames
    for p_abs, s in schemas.items():
        if "ucp.json" in p_abs or "_request.json" in p_abs:
            continue
        rename_map = flatten_dotted_defs(s)
        if rename_map:
            global_rename_maps[p_abs] = rename_map
        preprocess_full_schema(s, entity_def)

    # Pass 1b: Rewrite external references to renamed defs
    for p_abs, s in schemas.items():
        if "ucp.json" in p_abs or "_request.json" in p_abs:
            continue
        _rewrite_external_defs_refs(p_abs, s, global_rename_maps)

    # Pass 1c: Save and extract refs
    for p_abs, s in schemas.items():
        if "ucp.json" in p_abs or "_request.json" in p_abs:
            continue
        save_json(s, Path(p_abs))
        schema_refs[p_abs] = extract_external_refs(s, Path(p_abs))

        # Check if this schema explicitly asks for variants via 'ucp_request' markers
        ops = get_required_ops(s)
        if ops:
            variant_needs[p_abs] = ops

    if ucp_path in schemas:
        save_json(schemas[ucp_path], Path(ucp_path))

    # Pass 2: Propagate the need for variants down the dependency tree
    propagate_needs_transitive(variant_needs, schema_refs, schemas)

    # Pass 3: Finally write out the new variant files
    for path, ops in variant_needs.items():
        generate_variants(path, schemas[path], ops, variant_needs)


if __name__ == "__main__":
    main()
