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

# Generate Pydantic v2 models from UCP OpenAPI 3.1 Specification

# Ensure we are in the script's directory
cd "$(dirname "$0")" || exit

# Add ~/.local/bin to PATH for uv
export PATH="$HOME/.local/bin:$PATH"

# Check if uv is installed
if ! command -v uv &> /dev/null; then
    echo "Error: uv not found."
    echo "Please install uv: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

# Input OpenAPI Specification (default: embedded shopping.openapi.json)
INPUT_ARG="${1:-shopping.openapi.json}"

if [ -f "$INPUT_ARG" ]; then
    OPENAPI_SPEC="$INPUT_ARG"
elif [ -f "shopping.openapi.json" ]; then
    OPENAPI_SPEC="shopping.openapi.json"
elif [ -f "../ucp-schema/dist/shopping.openapi.json" ]; then
    OPENAPI_SPEC="../ucp-schema/dist/shopping.openapi.json"
elif [ -f "dist/shopping.openapi.json" ]; then
    OPENAPI_SPEC="dist/shopping.openapi.json"
else
    echo "Error: OpenAPI spec not found at $INPUT_ARG"
    exit 1
fi

# Output directory and target models file
OUTPUT_DIR="src/ucp_sdk/models/schemas"
MODELS_FILE="$OUTPUT_DIR/models.py"

echo "Generating Pydantic models directly from OpenAPI 3.1 specification ($OPENAPI_SPEC)..."

mkdir -p "$OUTPUT_DIR"

# Run generation using datamodel-code-generator via uv
uv run \
    --with "datamodel-code-generator[http]" \
    python -m datamodel_code_generator \
    --input "$OPENAPI_SPEC" \
    --input-file-type openapi \
    --output "$MODELS_FILE" \
    --output-model-type pydantic_v2.BaseModel \
    --use-schema-description \
    --field-constraints \
    --use-field-description \
    --enum-field-as-literal all \
    --disable-timestamp \
    --use-double-quotes \
    --use-type-alias \
    --reuse-model \
    --use-one-literal-as-default \
    --use-default

# Ensure package re-exports and request type compatibility aliases
cat << 'PY' > "$OUTPUT_DIR/__init__.py"
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

"""UCP schema models."""

from . import models
from .models import *  # noqa: F403

__all__ = ["models"]
PY

cat << 'PY' > "src/ucp_sdk/models/__init__.py"
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

"""UCP models."""

from .schemas import models
from .schemas.models import *  # noqa: F403

__all__ = ["models"]
PY

# Normalize file endings
python3 - <<'PY'
from pathlib import Path

for path in Path("src/ucp_sdk/models").rglob("*.py"):
    text = path.read_text(encoding="utf-8")
    fixed = text.rstrip("\n") + "\n" if text.strip() else ""
    if fixed != text:
        path.write_text(fixed, encoding="utf-8")
PY

echo "Formatting generated models..."
uv run ruff format "$OUTPUT_DIR"
uv run ruff check --fix "$OUTPUT_DIR"

echo "Done. Models generated in $MODELS_FILE"
