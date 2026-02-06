#!/bin/bash
# Generate Pydantic models from UCP JSON Schemas

# Ensure we are in the script's directory
cd "$(dirname "$0")"

# Output directory
OUTPUT_DIR="src/ucp_sdk/models"

# Schema directory (relative to this script)
SCHEMA_DIR="ucp/source"
TEMP_SCHEMA_DIR="temp_schemas"

echo "Preprocessing schemas..."
python3 preprocess_schemas.py

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

# Create ruff configuration for generated code
cat > "$OUTPUT_DIR/ruff.toml" << 'EOF'
# Ruff configuration for generated models
# These are auto-generated files, so we're more lenient with style rules

line-length = 120
target-version = "py311"

[lint]
select = ["E", "F", "I"]
ignore = ["E501"]

[lint.pydocstyle]
convention = "google"

[lint.per-file-ignores]
"__init__.py" = ["D104"]
"*.py" = ["D100", "D101", "D102", "D103", "D200", "D205", "D212"]
EOF

# Run generation using uv
# We use --use-schema-description to use descriptions from JSON schema as docstrings
# We use --field-constraints to include validation constraints (regex, min/max, etc.)
# Note: Formatters removed as they can hang on large schemas
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
uv run ruff format "$OUTPUT_DIR"
uv run ruff check --fix --config "$OUTPUT_DIR/ruff.toml" "$OUTPUT_DIR" 2>&1 | grep -E "^(All checks passed|Fixed|Found)" || echo "Formatting complete"

# Clean up temp schemas
rm -rf "$TEMP_SCHEMA_DIR"

echo "Done. Models generated in $OUTPUT_DIR"
