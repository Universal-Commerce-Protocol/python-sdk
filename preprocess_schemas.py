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


def update_refs_for_scenario(obj: Any, scenario: str, schemas_with_scenarios: set, current_dir: str = "") -> Any:
    """
    Recursively update $ref paths to point to scenario-specific schemas.
    For example, changes "types/line_item.json" to "types/line_item_create_request.json" for create scenario.
    Only updates references to schemas that actually have scenarios.
    """
    if isinstance(obj, dict):
        result = {}
        for key, value in obj.items():
            if key == "$ref" and isinstance(value, str):
                # Skip internal references and references with fragments (e.g., "../ucp.json#/$defs/...")
                if value.startswith("#/") or "#/$defs/" in value:
                    result[key] = value
                # Check if this is a reference to a schema file (not internal #/$defs/)
                elif value.endswith(".json"):
                    # Resolve the relative path from current schema's directory
                    if current_dir:
                        ref_path = str(Path(current_dir) / value)
                    else:
                        ref_path = value
                    
                    # Normalize the path
                    ref_path = ref_path.replace("\\", "/")
                    
                    # Check if the referenced schema has scenarios
                    if ref_path in schemas_with_scenarios:
                        base_path = value[:-5]  # Remove .json
                        # Create scenario-specific reference
                        result[key] = f"{base_path}_{scenario}_request.json"
                    else:
                        # Keep original reference for schemas without scenarios
                        result[key] = value
                else:
                    result[key] = value
            else:
                result[key] = update_refs_for_scenario(value, scenario, schemas_with_scenarios, current_dir)
        return result
    elif isinstance(obj, list):
        return [update_refs_for_scenario(item, scenario, schemas_with_scenarios, current_dir) for item in obj]
    return obj


def process_ucp_request_scenarios(schema: Dict[str, Any], base_name: str, schemas_with_scenarios: set, schema_dir: str = "") -> Dict[str, Dict[str, Any]]:
    """
    Generate separate schemas for create, update, and complete scenarios based on ucp_request annotations.
    
    Returns a dict mapping scenario names (e.g., 'create', 'update', 'complete') to their schemas.
    """
    scenarios = {}
    
    # Check if this schema has properties with ucp_request annotations
    if "properties" not in schema:
        return {"base": schema}
    
    # Detect which scenarios exist
    scenario_types = set()
    for prop_name, prop_schema in schema["properties"].items():
        if isinstance(prop_schema, dict) and "ucp_request" in prop_schema:
            ucp_req = prop_schema["ucp_request"]
            if isinstance(ucp_req, dict):
                scenario_types.update(ucp_req.keys())
    
    # If no scenarios detected, return base schema
    if not scenario_types:
        return {"base": schema}
    
    # Generate a schema for each scenario
    for scenario in scenario_types:
        scenario_schema = copy.deepcopy(schema)
        
        # Update title and description to reflect the scenario
        if "title" in scenario_schema:
            scenario_schema["title"] = f"{scenario_schema['title']} ({scenario.capitalize()} Request)"
        
        # Process each property based on its ucp_request directive
        properties_to_remove = []
        required_fields = set(scenario_schema.get("required", []))
        
        for prop_name, prop_schema in scenario_schema["properties"].items():
            if isinstance(prop_schema, dict) and "ucp_request" in prop_schema:
                ucp_req = prop_schema["ucp_request"]
                
                # Clean up the ucp_request annotation from the property
                del prop_schema["ucp_request"]
                
                # Determine the directive for this scenario
                if isinstance(ucp_req, str):
                    directive = ucp_req
                elif isinstance(ucp_req, dict):
                    directive = ucp_req.get(scenario, "optional")
                else:
                    directive = "optional"
                
                # Handle the directive
                if directive == "omit":
                    properties_to_remove.append(prop_name)
                    required_fields.discard(prop_name)
                elif directive == "required":
                    required_fields.add(prop_name)
                elif directive == "optional":
                    required_fields.discard(prop_name)
        
        # Remove omitted properties
        for prop_name in properties_to_remove:
            del scenario_schema["properties"][prop_name]
        
        # Update required fields
        if required_fields:
            scenario_schema["required"] = sorted(list(required_fields))
        elif "required" in scenario_schema:
            del scenario_schema["required"]
        
        # Update all $ref paths to point to scenario-specific schemas
        scenario_schema = update_refs_for_scenario(scenario_schema, scenario, schemas_with_scenarios, schema_dir)
        
        scenarios[scenario] = scenario_schema
    
    return scenarios


def clean_schema_for_codegen(schema: Dict[str, Any]) -> Dict[str, Any]:
    """
    Clean up schema to avoid issues with datamodel-code-generator.
    - Remove $id fields that might cause URL resolution issues
    - Remove $schema fields
    """
    schema = copy.deepcopy(schema)
    
    # Remove fields that can cause URL resolution issues
    if "$id" in schema:
        del schema["$id"]
    if "$schema" in schema:
        del schema["$schema"]
    
    return schema


def preprocess_schema_file(input_path: Path, output_path: Path, schemas_with_scenarios: set) -> None:
    """Preprocess a single schema file."""
    with open(input_path, 'r', encoding='utf-8') as f:
        schema = json.load(f)
    
    # WARN: Workaround for datamodel-codegen issue with relative paths
    # When ucp.json is referenced from a subdirectory (e.g. schemas/shopping/checkout.json referencing ../ucp.json),
    # the subsequent reference to service.json (in ucp.json) is incorrectly resolved relative to the subdirectory.
    # We force an absolute path here.
    if input_path.name == "ucp.json":
        # Force absolute paths for all sibling references in ucp.json
        # This fixes resolution issues when ucp.json is referenced from subdirectories
        schema_str = json.dumps(schema)
        
        input_dir_abs = output_path.parent.resolve()
        for ref_file in ["service.json", "capability.json", "payment_handler.json"]:
            ref_path = input_dir_abs / ref_file
            schema_str = schema_str.replace(f'"{ref_file}', f'"file://{ref_path}')
            
        schema = json.loads(schema_str)

    # Remove extension definitions that reference external schemas
    schema = remove_extension_defs(schema)
    
    # Flatten allOf patterns within $defs that only use internal refs
    schema = flatten_allof_in_defs(schema)
    
    # Generate scenario-specific schemas
    base_name = output_path.stem  # filename without extension
    
    # Get the directory of this schema relative to the root (for resolving refs)
    schema_dir = str(output_path.parent.relative_to(output_path.parent.parent.parent))
    
    scenarios = process_ucp_request_scenarios(schema, base_name, schemas_with_scenarios, schema_dir)
    
    # Ensure output directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Write preprocessed schema(s)
    if len(scenarios) == 1 and "base" in scenarios:
        # No scenarios, write single file
        cleaned = clean_schema_for_codegen(scenarios["base"])
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(cleaned, f, indent=2)
    else:
        # Write both base schema and scenario-specific files
        # First, write the base schema (for non-scenario-specific references)
        base_schema = copy.deepcopy(schema)
        cleaned_base = clean_schema_for_codegen(base_schema)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(cleaned_base, f, indent=2)
        
        # Then write separate files for each scenario
        for scenario_name, scenario_schema in scenarios.items():
            scenario_path = output_path.parent / f"{base_name}_{scenario_name}_request.json"
            print(f"    -> Generating {scenario_name} request schema")
            cleaned = clean_schema_for_codegen(scenario_schema)
            with open(scenario_path, 'w', encoding='utf-8') as f:
                json.dump(cleaned, f, indent=2)


def preprocess_schemas(input_dir: Path, output_dir: Path) -> None:
    """Preprocess all schema files in the directory tree."""
    # Clean output directory
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)
    
    # Find all JSON files
    json_files = list(input_dir.rglob("*.json"))
    
    print(f"Preprocessing {len(json_files)} schema files...")
    
    # First pass: track which schemas have scenarios
    schemas_with_scenarios = set()
    for json_file in json_files:
        with open(json_file, 'r', encoding='utf-8') as f:
            schema = json.load(f)
        
        # Check if this schema has ucp_request scenarios
        if "properties" in schema:
            for prop_schema in schema["properties"].values():
                if isinstance(prop_schema, dict) and "ucp_request" in prop_schema:
                    ucp_req = prop_schema["ucp_request"]
                    if isinstance(ucp_req, dict):
                        # This schema has scenarios
                        rel_path = json_file.relative_to(input_dir)
                        schemas_with_scenarios.add(str(rel_path))
                        break
    
    # Second pass: preprocess and generate schemas
    for json_file in json_files:
        # Calculate relative path
        rel_path = json_file.relative_to(input_dir)
        output_path = output_dir / rel_path
        
        print(f"  Processing: {rel_path}")
        preprocess_schema_file(json_file, output_path, schemas_with_scenarios)
    
    print(f"Preprocessing complete. Output in {output_dir}")


if __name__ == "__main__":
    script_dir = Path(__file__).parent
    input_schemas = script_dir / "ucp" / "source"
    output_schemas = script_dir / "temp_schemas"
    
    preprocess_schemas(input_schemas, output_schemas)
