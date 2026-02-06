#!/usr/bin/env python3

# Copyright 2026 UCP Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#       http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Preprocess JSON schemas for datamodel-code-generator compatibility.
"""
import json
import shutil
import copy
from pathlib import Path
from typing import Any, Dict


def remove_extension_defs(schema: Dict[str, Any]) -> Dict[str, Any]:
    """
    Remove $defs that extend EXTERNAL schemas using allOf.
    These cause circular dependency issues.
    """
    if "$defs" not in schema:
        return schema
    
    defs_to_remove = []
    
    for def_name, def_schema in schema["$defs"].items():
        if isinstance(def_schema, dict) and "allOf" in def_schema:
            # Check if this extends an external schema
            all_of = def_schema["allOf"]
            has_external_ref = False
            for item in all_of:
                if isinstance(item, dict) and "$ref" in item:
                    ref = item["$ref"]
                    # If referencing another top-level schema (not within same file's $defs)
                    if not ref.startswith("#/"):
                        has_external_ref = True
                        break
            
            if has_external_ref:
                print(f"    -> Removing extension def: {def_name}")
                defs_to_remove.append(def_name)
    
    # Remove extension defs
    for def_name in defs_to_remove:
        del schema["$defs"][def_name]
    
    # Remove empty $defs
    if "$defs" in schema and not schema["$defs"]:
        del schema["$defs"]
    
    return schema


def inline_internal_refs(obj: Any, defs: Dict[str, Any], processed: set = None) -> Any:
    """
    Recursively inline $ref references that point to #/$defs/...
    This resolves internal references to avoid cross-file confusion.
    """
    if processed is None:
        processed = set()
    
    if isinstance(obj, dict):
        # Check for $ref
        if "$ref" in obj and len(obj) == 1:
            ref = obj["$ref"]
            if ref.startswith("#/$defs/"):
                def_name = ref.split("/")[-1]
                # Avoid infinite recursion
                if def_name not in processed and def_name in defs:
                    processed.add(def_name)
                    # Inline the definition
                    inlined = copy.deepcopy(defs[def_name])
                    result = inline_internal_refs(inlined, defs, processed)
                    processed.remove(def_name)
                    return result
            return obj
        
        # Recursively process all properties
        result = {}
        for key, value in obj.items():
            result[key] = inline_internal_refs(value, defs, processed)
        return result
    elif isinstance(obj, list):
        return [inline_internal_refs(item, defs, processed) for item in obj]
    return obj


def flatten_allof_in_defs(schema: Dict[str, Any]) -> Dict[str, Any]:
    """
    Flatten allOf patterns within $defs that only use internal references.
    """
    if "$defs" not in schema:
        return schema
    
    defs = schema["$defs"]
    
    for def_name, def_schema in list(defs.items()):
        if isinstance(def_schema, dict) and "allOf" in def_schema:
            all_of = def_schema["allOf"]
            
            # Check if all refs are internal
            all_internal = True
            for item in all_of:
                if isinstance(item, dict) and "$ref" in item:
                    ref = item["$ref"]
                    if not ref.startswith("#/"):
                        all_internal = False
                        break
            
            if all_internal:
                # Flatten the allOf by inlining refs
                merged = {}
                for item in all_of:
                    resolved = inline_internal_refs(item, defs, set())
                    # Merge properties
                    for k, v in resolved.items():
                        if k == "properties" and k in merged:
                            merged[k].update(v)
                        elif k == "required" and k in merged:
                            merged[k] = list(set(merged[k] + v))
                        elif k not in ["title", "description", "allOf"]:
                            merged[k] = v
                
                # Keep original title and description
                if "title" in def_schema:
                    merged["title"] = def_schema["title"]
                if "description" in def_schema:
                    merged["description"] = def_schema["description"]
                
                defs[def_name] = merged
    
    return schema


def preprocess_schema_file(input_path: Path, output_path: Path) -> None:
    """Preprocess a single schema file."""
    with open(input_path, 'r', encoding='utf-8') as f:
        schema = json.load(f)
    
    # Remove extension definitions that reference external schemas
    schema = remove_extension_defs(schema)
    
    # Flatten allOf patterns within $defs that only use internal refs
    schema = flatten_allof_in_defs(schema)
    
    # Ensure output directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Write preprocessed schema
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(schema, f, indent=2)


def preprocess_schemas(input_dir: Path, output_dir: Path) -> None:
    """Preprocess all schema files in the directory tree."""
    # Clean output directory
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)
    
    # Find all JSON files
    json_files = list(input_dir.rglob("*.json"))
    
    print(f"Preprocessing {len(json_files)} schema files...")
    
    for json_file in json_files:
        # Calculate relative path
        rel_path = json_file.relative_to(input_dir)
        output_path = output_dir / rel_path
        
        print(f"  Processing: {rel_path}")
        preprocess_schema_file(json_file, output_path)
    
    print(f"Preprocessing complete. Output in {output_dir}")


if __name__ == "__main__":
    script_dir = Path(__file__).parent
    input_schemas = script_dir / "ucp" / "source"
    output_schemas = script_dir / "temp_schemas"
    
    preprocess_schemas(input_schemas, output_schemas)
