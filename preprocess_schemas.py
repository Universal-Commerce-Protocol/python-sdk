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


def get_explicit_ops(schema):
  """Finds ops explicitly mentioned in ucp_request fields."""
  ops = set()
  properties = schema.get("properties", {})
  for prop_data in properties.values():
    if not isinstance(prop_data, dict):
      continue
    ucp_req = prop_data.get("ucp_request")
    if isinstance(ucp_req, str):
      # Strings like "omit" or "required" only imply standard ops.
      # "complete" request should only be generated when it's explicitly defined in a dict.
      ops.update(["create", "update"])
    elif isinstance(ucp_req, dict):
      for op in ucp_req:
        ops.add(op)
  return ops


def get_props_with_refs(schema, schema_file_path):
  """Finds all external schema references associated with their properties."""
  results = []  # list of (prop_name, abs_ref_path)

  def find_refs(obj, prop_name):
    if isinstance(obj, dict):
      if "$ref" in obj:
        ref = obj["$ref"]
        if "#" not in ref:
          ref_path = (schema_file_path.parent / ref).resolve()
          results.append((prop_name, str(ref_path)))
      for v in obj.values():
        find_refs(v, prop_name)
    elif isinstance(obj, list):
      for item in obj:
        find_refs(item, prop_name)

  properties = schema.get("properties", {})
  for prop_name, prop_data in properties.items():
    find_refs(prop_data, prop_name)
  return results


def get_variant_filename(base_path, op):
  p = Path(base_path)
  return p.parent / f"{p.stem}_{op}_request.json"


def generate_variants(schema_file, schema, ops, all_variant_needs):
  schema_file_path = Path(schema_file)
  for op in ops:
    variant_schema = copy.deepcopy(schema)

    # Update title and id
    base_title = schema.get("title", schema_file_path.stem)
    variant_schema["title"] = f"{base_title} {op.capitalize()} Request"

    # Update $id if present
    if "$id" in variant_schema:
      old_id = variant_schema["$id"]
      if "/" in old_id:
        old_id_parts = old_id.split("/")
        old_id_filename = old_id_parts[-1]
        if "." in old_id_filename:
          stem = old_id_filename.split(".")[0]
          ext = old_id_filename.split(".")[-1]
          new_id_filename = f"{stem}_{op}_request.{ext}"
          variant_schema["$id"] = "/".join(
            old_id_parts[:-1] + [new_id_filename]
          )

    new_properties = {}
    new_required = []

    for prop_name, prop_data in schema.get("properties", {}).items():
      if not isinstance(prop_data, dict):
        new_properties[prop_name] = prop_data
        continue

      ucp_req = prop_data.get("ucp_request")

      include = True
      is_required = False

      if ucp_req is not None:
        if isinstance(ucp_req, str):
          if ucp_req == "omit":
            include = False
          elif ucp_req == "required":
            is_required = True
        elif isinstance(ucp_req, dict):
          op_val = ucp_req.get(op)
          if op_val == "omit" or op_val is None:
            include = False
          elif op_val == "required":
            is_required = True
      else:
        # No ucp_request. Include if it was required in base?
        if prop_name in schema.get("required", []):
          is_required = True

      if include:
        prop_copy = copy.deepcopy(prop_data)
        if "ucp_request" in prop_copy:
          del prop_copy["ucp_request"]

        # Recursive reference check (deep)
        def update_refs(obj):
          if isinstance(obj, dict):
            if "$ref" in obj:
              ref = obj["$ref"]
              if "#" not in ref:
                ref_path = Path(ref)
                target_base_abs = (schema_file_path.parent / ref_path).resolve()
                if (
                  str(target_base_abs) in all_variant_needs
                  and op in all_variant_needs[str(target_base_abs)]
                ):
                  variant_ref_filename = f"{ref_path.stem}_{op}_request.json"
                  obj["$ref"] = str(ref_path.parent / variant_ref_filename)
            for k, v in obj.items():
              update_refs(v)
          elif isinstance(obj, list):
            for item in obj:
              update_refs(item)

        update_refs(prop_copy)

        new_properties[prop_name] = prop_copy
        if is_required:
          new_required.append(prop_name)

    # Always generate the variant schema to avoid breaking refs in parents
    variant_schema["properties"] = new_properties
    variant_schema["required"] = new_required

    variant_path = get_variant_filename(schema_file_path, op)
    with open(variant_path, "w") as f:
      json.dump(variant_schema, f, indent=2)
    print(f"Generated {variant_path}")


def main():
  schema_dir = "ucp/source"
  if len(sys.argv) > 1:
    schema_dir = sys.argv[1]

  schema_dir_path = Path(schema_dir)
  if not schema_dir_path.exists():
    print(f"Directory {schema_dir} does not exist.")
    return

  all_files = list(schema_dir_path.rglob("*.json"))

  schemas_cache = {}
  schema_props_refs = {}
  all_variant_needs = {}

  # 1. First pass: load all schemas and find properties with refs
  for f in all_files:
    if "_request.json" in f.name:
      continue
    try:
      with open(f, "r") as open_f:
        schema = json.load(open_f)
        if (
          not isinstance(schema, dict)
          or schema.get("type") != "object"
          or "properties" not in schema
        ):
          continue

        abs_path = str(f.resolve())
        schemas_cache[abs_path] = schema
        schema_props_refs[abs_path] = get_props_with_refs(schema, f)

        # 2. Get explicit needs defined in the schema itself
        explicit_ops = get_explicit_ops(schema)
        if explicit_ops:
          all_variant_needs[abs_path] = explicit_ops
    except Exception as e:
      print(f"Error processing {f}: {e}")

  # 3. Transitive dependency tracking (Parent -> Child):
  # If P needs variant OP, and P includes property S (not omitted for OP),
  # then S also needs variant OP to ensure ref matching works correctly.
  changed = True
  while changed:
    changed = False
    for abs_path, props_refs in schema_props_refs.items():
      if abs_path not in all_variant_needs:
        continue

      parent_schema = schemas_cache[abs_path]
      parent_ops = all_variant_needs[abs_path]

      for op in list(parent_ops):
        for prop_name, ref_path in props_refs:
          if ref_path not in schemas_cache:
            continue

          # Check if this property is omitted for this op in parent
          prop_data = parent_schema["properties"].get(prop_name, {})
          ucp_req = prop_data.get("ucp_request")

          include = True
          if ucp_req is not None:
            if isinstance(ucp_req, str):
              if ucp_req == "omit":
                include = False
            elif isinstance(ucp_req, dict):
              op_val = ucp_req.get(op)
              if op_val == "omit" or op_val is None:
                include = False

          if include:
            # Propagate op from parent to child
            child_needs = all_variant_needs.get(ref_path, set())
            if op not in child_needs:
              all_variant_needs.setdefault(ref_path, set()).add(op)
              changed = True

  # 4. Final pass: generate variants
  for f_abs, ops in all_variant_needs.items():
    generate_variants(f_abs, schemas_cache[f_abs], ops, all_variant_needs)


if __name__ == "__main__":
  main()
