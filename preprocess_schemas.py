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


def process_file(schema_file, all_variant_needs):
  """Analyzes a schema file to see which request variants it needs."""
  try:
    with open(schema_file, "r") as f:
      schema = json.load(f)
  except Exception as e:
    print(f"Error loading {schema_file}: {e}")
    return None

  if (
    not isinstance(schema, dict)
    or schema.get("type") != "object"
    or "properties" not in schema
  ):
    return None

  ops_found = set()
  for prop_name, prop_data in schema.get("properties", {}).items():
    if not isinstance(prop_data, dict):
      continue
    ucp_req = prop_data.get("ucp_request")
    if isinstance(ucp_req, str):
      if ucp_req != "omit":
        ops_found.update(["create", "update", "complete"])
    elif isinstance(ucp_req, dict):
      for op in ucp_req:
        ops_found.add(op)

  if ops_found:
    all_variant_needs[str(schema_file.resolve())] = ops_found
  return schema


def get_variant_filename(base_path, op):
  p = Path(base_path)
  return p.parent / f"{op}_{p.stem}_request.json"


def generate_variants(schema_file, schema, ops, all_variant_needs):
  schema_file_path = Path(schema_file)
  for op in ops:
    variant_schema = copy.deepcopy(schema)

    # Update title and id
    base_title = schema.get("title", schema_file_path.stem)
    variant_schema["title"] = f"{op.capitalize()} {base_title} Request"

    # Update $id if present
    if "$id" in variant_schema:
      old_id = variant_schema["$id"]
      if "/" in old_id:
        old_id_parts = old_id.split("/")
        old_id_filename = old_id_parts[-1]
        if "." in old_id_filename:
          stem = old_id_filename.split(".")[0]
          ext = old_id_filename.split(".")[-1]
          new_id_filename = f"{op}_{stem}_request.{ext}"
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
                  variant_ref_filename = f"{op}_{ref_path.stem}_request.json"
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

    if new_properties:
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

  all_variant_needs = {}
  schemas_cache = {}

  for f in all_files:
    if "_request.json" in f.name:
      continue
    s = process_file(f, all_variant_needs)
    if s:
      schemas_cache[str(f.resolve())] = s

  for f_abs, ops in all_variant_needs.items():
    generate_variants(f_abs, schemas_cache[f_abs], ops, all_variant_needs)


if __name__ == "__main__":
  main()
