#!/bin/bash
# Generate Pydantic models from UCP JSON Schemas

# Ensure we are in the script's directory
cd "$(dirname "$0")" || exit

# Output directory
OUTPUT_DIR="src/ucp_sdk/models"

# Schema directory (relative to this script)
SCHEMA_DIR="${SCHEMA_DIR:-../../spec/}"

echo "Generating Pydantic models from $SCHEMA_DIR..."

# Check if uv is installed
if ! command -v uv &> /dev/null; then
    echo "Error: uv not found."
    echo "Please install uv: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

# Ensure output directory is clean
rm -r -f "$OUTPUT_DIR"
mkdir -p "$OUTPUT_DIR"

# Run generation using uv
# We use --use-schema-description to use descriptions from JSON schema as docstrings
# We use --field-constraints to include validation constraints (regex, min/max, etc.)
uv run \
    --link-mode=copy \
    --extra-index-url https://pypi.org/simple python \
    -m datamodel_code_generator \
    --input "$SCHEMA_DIR" \
    --input-file-type jsonschema \
    --output "$OUTPUT_DIR" \
    --output-model-type pydantic_v2.BaseModel \
    --use-schema-description \
    --field-constraints \
    --use-field-description \
    --enum-field-as-literal all \
    --disable-timestamp \
    --use-double-quotes \
    --no-use-annotated \
    --allow-extra-fields \
    --formatters ruff-format ruff-check

echo "Done. Models generated in $OUTPUT_DIR"
