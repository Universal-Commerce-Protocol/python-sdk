#!/bin/bash
# Generate Pydantic models from UCP JSON Schemas

# Ensure we are in the script's directory
cd "$(dirname "$0")"

# Check if git is installed
if ! command -v git &> /dev/null; then
    echo "Error: git not found. Please install git."
    exit 1
fi

# UCP Version to use (if provided, use release/$1 branch; otherwise, use main)
if [ -z "$1" ]; then
    BRANCH="main"
    echo "No version specified, cloning main branch..."
else
    BRANCH="release/$1"
    echo "Cloning version $1 (branch: $BRANCH)..."
fi

# Ensure ucp directory is clean before cloning
rm -rf ucp
git clone -b "$BRANCH" --depth 1 https://github.com/Universal-Commerce-Protocol/ucp ucp

# Output directory
OUTPUT_DIR="src/ucp_sdk/models"

# Schema directory (relative to this script)
SCHEMA_DIR="ucp/source"
TEMP_SCHEMA_DIR="temp_schemas"

echo "Preprocessing schemas..."
uv run python preprocess_schemas.py

echo "Generating Pydantic models from preprocessed schemas..."

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
# Note: Formatting is done as a post-processing step.
uv run \
    --link-mode=copy \
    --extra-index-url https://pypi.org/simple python \
    -m datamodel_code_generator \
    --input "$TEMP_SCHEMA_DIR" \
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
    --allow-extra-fields

echo "Formatting generated models..."
uv run ruff format
uv run ruff check --fix "$OUTPUT_DIR" 2>&1 | grep -E "^(All checks passed|Fixed|Found)" || echo "Formatting complete"

# Clean up temporary schemas
rm -rf "$TEMP_SCHEMA_DIR"

echo "Done. Models generated in $OUTPUT_DIR"
