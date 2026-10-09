#!/bin/bash
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

# Generate Pydantic models from UCP JSON Schemas using ucp-schema generate-types.

set -euo pipefail

# Ensure we are in the script's directory
cd "$(dirname "$0")"

# Add ~/.local/bin to PATH for uv
export PATH="$HOME/.local/bin:$PATH"

if ! command -v git &> /dev/null; then
    echo "Error: git not found. Please install git."
    exit 1
fi

if ! command -v uv &> /dev/null; then
    echo "Error: uv not found."
    echo "Please install uv: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

# Resolve ucp-schema binary
if [ -n "${UCP_SCHEMA_BIN:-}" ] && [ ! -x "$UCP_SCHEMA_BIN" ]; then
    echo "Error: UCP_SCHEMA_BIN='$UCP_SCHEMA_BIN' is not executable."
    exit 1
elif [ -n "${UCP_SCHEMA_BIN:-}" ]; then
    :
elif command -v ucp-schema &> /dev/null && ucp-schema generate-types --help &> /dev/null; then
    UCP_SCHEMA_BIN="$(command -v ucp-schema)"
elif [ -x "../ucp-schema/target/release/ucp-schema" ]; then
    UCP_SCHEMA_BIN="../ucp-schema/target/release/ucp-schema"
elif [ -x "../ucp-schema/target/debug/ucp-schema" ]; then
    UCP_SCHEMA_BIN="../ucp-schema/target/debug/ucp-schema"
elif [ -f "../ucp-schema/Cargo.toml" ] && command -v cargo &> /dev/null; then
    echo "Building ucp-schema from ../ucp-schema..."
    cargo build --release --manifest-path "../ucp-schema/Cargo.toml"
    UCP_SCHEMA_BIN="../ucp-schema/target/release/ucp-schema"
else
    echo "Error: ucp-schema binary (with generate-types) not found. Set UCP_SCHEMA_BIN, install ucp-schema on PATH, or build ../ucp-schema."
    exit 1
fi

TMP_CLONE_DIR=""
TMP_TYPES_JSON="$(mktemp "${TMPDIR:-/tmp}/ucp-types.XXXXXX.json")"
cleanup() {
    rm -f "$TMP_TYPES_JSON"
    if [ -n "$TMP_CLONE_DIR" ] && [ -d "$TMP_CLONE_DIR" ]; then
        rm -rf "$TMP_CLONE_DIR"
    fi
}
trap cleanup EXIT

INPUT_ARG="${1:-}"
if [ -n "$INPUT_ARG" ] && [ -d "$INPUT_ARG" ]; then
    INPUT_DIR="$INPUT_ARG"
    echo "Using local UCP directory: $INPUT_DIR"
elif [[ "$INPUT_ARG" == /* || "$INPUT_ARG" == ./* || "$INPUT_ARG" == ../* ]]; then
    echo "Error: Local UCP directory not found at '$INPUT_ARG'."
    exit 1
elif [ -z "$INPUT_ARG" ] && [ -d "../ucp" ]; then
    INPUT_DIR="../ucp"
    echo "No version specified; using sibling ../ucp directory..."
elif [ -z "$INPUT_ARG" ]; then
    TMP_CLONE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/ucp-clone.XXXXXX")"
    echo "No version specified, cloning main branch..."
    git clone -b main --depth 1 https://github.com/Universal-Commerce-Protocol/ucp "$TMP_CLONE_DIR"
    INPUT_DIR="$TMP_CLONE_DIR"
elif [[ "$INPUT_ARG" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
    TMP_CLONE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/ucp-clone.XXXXXX")"
    BRANCH="release/$INPUT_ARG"
    echo "Cloning version $INPUT_ARG (branch: $BRANCH)..."
    git clone -b "$BRANCH" --depth 1 https://github.com/Universal-Commerce-Protocol/ucp "$TMP_CLONE_DIR"
    INPUT_DIR="$TMP_CLONE_DIR"
elif [[ "$INPUT_ARG" =~ ^[0-9a-fA-F]{7,40}$ ]]; then
    TMP_CLONE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/ucp-clone.XXXXXX")"
    echo "Cloning UCP repository at commit $INPUT_ARG..."
    git clone https://github.com/Universal-Commerce-Protocol/ucp "$TMP_CLONE_DIR"
    git -C "$TMP_CLONE_DIR" checkout "$INPUT_ARG"
    INPUT_DIR="$TMP_CLONE_DIR"
else
    TMP_CLONE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/ucp-clone.XXXXXX")"
    echo "Cloning branch/ref $INPUT_ARG..."
    git clone -b "$INPUT_ARG" --depth 1 https://github.com/Universal-Commerce-Protocol/ucp "$TMP_CLONE_DIR"
    INPUT_DIR="$TMP_CLONE_DIR"
fi

if [ -d "$INPUT_DIR/source/schemas" ]; then
    SCHEMA_DIR="$INPUT_DIR/source/schemas"
elif [ -d "$INPUT_DIR/spec/schemas" ]; then
    SCHEMA_DIR="$INPUT_DIR/spec/schemas"
elif [ -d "$INPUT_DIR/schemas" ]; then
    SCHEMA_DIR="$INPUT_DIR/schemas"
elif [ -f "$INPUT_DIR/ucp.json" ]; then
    SCHEMA_DIR="$INPUT_DIR"
else
    echo "Error: Could not locate schema directory under '$INPUT_DIR'."
    exit 1
fi

OUTPUT_DIR="src/ucp_sdk/models/schemas"

echo "Compiling type bundle with ucp-schema generate-types..."
"$UCP_SCHEMA_BIN" generate-types --schema-dir "$SCHEMA_DIR" --output "$TMP_TYPES_JSON"

echo "Generating Pydantic models from compiled type bundle..."
rm -rf "$OUTPUT_DIR"
mkdir -p "$OUTPUT_DIR"

uv run \
    --link-mode=copy \
    --extra-index-url https://pypi.org/simple python \
    -m datamodel_code_generator \
    --input "$TMP_TYPES_JSON" \
    --input-file-type jsonschema \
    --output "$OUTPUT_DIR/__init__.py" \
    --output-model-type pydantic_v2.BaseModel \
    --use-schema-description \
    --field-constraints \
    --use-field-description \
    --enum-field-as-literal all \
    --disable-timestamp \
    --use-double-quotes \
    --extra-fields=allow \
    --use-type-alias \
    --use-standard-collections \
    --reuse-model \
    --use-title-as-name \
    --additional-imports pydantic.ConfigDict

echo "Post-processing generated models (constraints the generator ignores)..."
uv run python postprocess_models.py "$TMP_TYPES_JSON" src/ucp_sdk/models "$SCHEMA_DIR"

echo "Formatting generated models..."
uv run ruff check --fix src/ucp_sdk/models
uv run ruff format

# Normalize file endings (as pre-commit's end-of-file-fixer does)
python3 - <<'PY'
from pathlib import Path

for path in Path("src/ucp_sdk/models").rglob("*.py"):
    text = path.read_text(encoding="utf-8")
    fixed = text.rstrip("\n") + "\n" if text.strip() else ""
    if fixed != text:
        path.write_text(fixed, encoding="utf-8")
PY

echo "Done. Models generated in $OUTPUT_DIR/__init__.py"
