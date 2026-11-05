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
#
# Usage:
#   ./generate_models.sh --from-lock [--spec-dir <dir>]
#   ./generate_models.sh [draft | main | YYYY-MM-DD | <git-sha> | <local-ucp-dir>] [--channel draft|release] [--ref <ucp-ref>] [--write-lock]

set -euo pipefail

cd "$(dirname "$0")"

export PATH="$HOME/.local/bin:$PATH"

usage() {
    cat <<'EOF'
Usage:
  ./generate_models.sh --from-lock [--spec-dir <dir>]
  ./generate_models.sh [draft | main | YYYY-MM-DD | <git-sha> | <local-ucp-dir>] [--channel draft|release] [--ref <ucp-ref>] [--write-lock]
EOF
}

FROM_LOCK=0
SPEC_DIR=""
CHANNEL_OVERRIDE=""
REF_OVERRIDE=""
WRITE_LOCK=0
POSITIONAL_ARG=""

while [ $# -gt 0 ]; do
    case "$1" in
        --from-lock)
            FROM_LOCK=1
            shift
            ;;
        --spec-dir)
            if [ $# -lt 2 ]; then
                echo "Error: --spec-dir requires a directory argument." >&2
                exit 1
            fi
            SPEC_DIR="$2"
            shift 2
            ;;
        --channel)
            if [ $# -lt 2 ]; then
                echo "Error: --channel requires an argument (draft or release)." >&2
                exit 1
            fi
            CHANNEL_OVERRIDE="$2"
            shift 2
            ;;
        --ref)
            if [ $# -lt 2 ]; then
                echo "Error: --ref requires a UCP ref argument." >&2
                exit 1
            fi
            REF_OVERRIDE="$2"
            shift 2
            ;;
        --write-lock)
            WRITE_LOCK=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        --*)
            echo "Error: Unknown option '$1'." >&2
            usage >&2
            exit 1
            ;;
        *)
            if [ -n "$POSITIONAL_ARG" ]; then
                echo "Error: Unexpected extra argument '$1'." >&2
                usage >&2
                exit 1
            fi
            POSITIONAL_ARG="$1"
            shift
            ;;
    esac
done

if [ -n "$CHANNEL_OVERRIDE" ] && [ "$CHANNEL_OVERRIDE" != "draft" ] && [ "$CHANNEL_OVERRIDE" != "release" ]; then
    echo "Error: --channel must be 'draft' or 'release' (got '$CHANNEL_OVERRIDE')." >&2
    exit 1
fi

if ! command -v git &> /dev/null; then
    echo "Error: git not found. Please install git." >&2
    exit 1
fi

if ! command -v uv &> /dev/null; then
    echo "Error: uv not found." >&2
    echo "Please install uv: curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
    exit 1
fi

# Resolve ucp-schema binary
if [ -n "${UCP_SCHEMA_BIN:-}" ] && [ ! -x "$UCP_SCHEMA_BIN" ]; then
    echo "Error: UCP_SCHEMA_BIN='$UCP_SCHEMA_BIN' is not executable." >&2
    exit 1
elif [ -n "${UCP_SCHEMA_BIN:-}" ]; then
    if ! "$UCP_SCHEMA_BIN" generate-types --help &> /dev/null; then
        echo "Error: UCP_SCHEMA_BIN='$UCP_SCHEMA_BIN' does not support 'generate-types'." >&2
        exit 1
    fi
elif command -v ucp-schema &> /dev/null && ucp-schema generate-types --help &> /dev/null; then
    UCP_SCHEMA_BIN="$(command -v ucp-schema)"
elif [ -x "../ucp-schema/target/release/ucp-schema" ] && "../ucp-schema/target/release/ucp-schema" generate-types --help &> /dev/null; then
    UCP_SCHEMA_BIN="../ucp-schema/target/release/ucp-schema"
elif [ -x "../ucp-schema/target/debug/ucp-schema" ] && "../ucp-schema/target/debug/ucp-schema" generate-types --help &> /dev/null; then
    UCP_SCHEMA_BIN="../ucp-schema/target/debug/ucp-schema"
elif [ -f "../ucp-schema/Cargo.toml" ] && command -v cargo &> /dev/null; then
    echo "Building ucp-schema from ../ucp-schema..."
    cargo build --release --manifest-path "../ucp-schema/Cargo.toml"
    UCP_SCHEMA_BIN="../ucp-schema/target/release/ucp-schema"
    if ! "$UCP_SCHEMA_BIN" generate-types --help &> /dev/null; then
        echo "Error: ../ucp-schema checkout does not support 'generate-types'." >&2
        exit 1
    fi
else
    echo "Error: ucp-schema binary (with generate-types) not found. Set UCP_SCHEMA_BIN, install ucp-schema on PATH, or build ../ucp-schema." >&2
    exit 1
fi

BIN_VERSION="$("$UCP_SCHEMA_BIN" --version | awk '{print $2}')"

if [ "$FROM_LOCK" -eq 0 ] && [ -z "$POSITIONAL_ARG" ]; then
    if [ -f "ucp-lock.json" ]; then
        FROM_LOCK=1
    else
        POSITIONAL_ARG="draft"
    fi
fi

TMP_CLONE_DIR=""
SCOPED_SCHEMA_DIR=""
TMP_TYPES_JSON="$(mktemp "${TMPDIR:-/tmp}/ucp-types.XXXXXX.json")"
cleanup() {
    rm -f "$TMP_TYPES_JSON"
    if [ -n "$SCOPED_SCHEMA_DIR" ] && [ -d "$SCOPED_SCHEMA_DIR" ]; then
        rm -rf "$SCOPED_SCHEMA_DIR"
    fi
    if [ -n "$TMP_CLONE_DIR" ] && [ -d "$TMP_CLONE_DIR" ]; then
        rm -rf "$TMP_CLONE_DIR"
    fi
}
trap cleanup EXIT

SHOULD_WRITE_LOCK=0
RESOLVED_CHANNEL=""
RESOLVED_REF=""
RESOLVED_SHA=""
RESOLVED_SCHEMA_VERSION="${UCP_SCHEMA_VERSION:-$BIN_VERSION}"
VERTICAL="shopping"

if [ "$FROM_LOCK" -eq 1 ]; then
    if [ -n "$POSITIONAL_ARG" ]; then
        echo "Error: --from-lock cannot be combined with positional argument '$POSITIONAL_ARG'." >&2
        exit 1
    fi
    if [ ! -f "ucp-lock.json" ]; then
        echo "Error: ucp-lock.json not found in $(pwd)." >&2
        exit 1
    fi
    read -r LOCK_SHA LOCK_SCHEMA_VERSION LOCK_VERTICAL < <(python3 - <<'PY'
import json
from pathlib import Path

lock = json.loads(Path("ucp-lock.json").read_text(encoding="utf-8"))
print(
    lock["ucp_sha"],
    lock["ucp_schema_version"],
    lock.get("vertical", "shopping"),
)
PY
)
    VERTICAL="$LOCK_VERTICAL"
    if [ "${UCP_SKIP_VERSION_CHECK:-0}" != "1" ] && [ "$BIN_VERSION" != "$LOCK_SCHEMA_VERSION" ]; then
        echo "Error: ucp-schema binary version '$BIN_VERSION' does not match ucp-lock.json ucp_schema_version '$LOCK_SCHEMA_VERSION' (set UCP_SKIP_VERSION_CHECK=1 to override)." >&2
        exit 1
    fi
    if [ -n "$SPEC_DIR" ]; then
        if [ ! -d "$SPEC_DIR" ]; then
            echo "Error: --spec-dir '$SPEC_DIR' does not exist." >&2
            exit 1
        fi
        SPEC_HEAD="$(git -C "$SPEC_DIR" rev-parse HEAD)"
        if [ "$SPEC_HEAD" != "$LOCK_SHA" ]; then
            echo "Error: --spec-dir HEAD ($SPEC_HEAD) does not match ucp-lock.json ucp_sha ($LOCK_SHA)." >&2
            exit 1
        fi
        INPUT_DIR="$SPEC_DIR"
        echo "Using locked UCP checkout at $INPUT_DIR ($LOCK_SHA)"
    else
        TMP_CLONE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/ucp-clone.XXXXXX")"
        echo "Cloning UCP repository at locked commit $LOCK_SHA..."
        git clone https://github.com/Universal-Commerce-Protocol/ucp "$TMP_CLONE_DIR"
        git -C "$TMP_CLONE_DIR" checkout --detach "$LOCK_SHA"
        INPUT_DIR="$TMP_CLONE_DIR"
    fi
else
    if [ "${UCP_SKIP_VERSION_CHECK:-0}" = "1" ] && [ -z "${UCP_SCHEMA_VERSION:-}" ]; then
        if [ -f "ucp-lock.json" ]; then
            RESOLVED_SCHEMA_VERSION="$(python3 - <<'PY'
import json
from pathlib import Path

lock = json.loads(Path("ucp-lock.json").read_text(encoding="utf-8"))
print(lock.get("ucp_schema_version", "2.0.0"))
PY
)"
        else
            RESOLVED_SCHEMA_VERSION="2.0.0"
        fi
    fi

    if [ -d "$POSITIONAL_ARG" ]; then
        INPUT_DIR="$POSITIONAL_ARG"
        echo "Using local UCP directory: $INPUT_DIR"
        if [ "$WRITE_LOCK" -eq 1 ]; then
            SHOULD_WRITE_LOCK=1
            RESOLVED_SHA="$(git -C "$INPUT_DIR" rev-parse HEAD)"
            RESOLVED_CHANNEL="${CHANNEL_OVERRIDE:-draft}"
            RESOLVED_REF="${REF_OVERRIDE:-main}"
        fi
    elif [[ "$POSITIONAL_ARG" == /* || "$POSITIONAL_ARG" == ./* || "$POSITIONAL_ARG" == ../* ]]; then
        echo "Error: Local UCP directory not found at '$POSITIONAL_ARG'." >&2
        exit 1
    elif [ "$POSITIONAL_ARG" = "draft" ] || [ "$POSITIONAL_ARG" = "main" ]; then
        TMP_CLONE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/ucp-clone.XXXXXX")"
        echo "Cloning UCP main branch (draft channel)..."
        git clone -b main --depth 1 https://github.com/Universal-Commerce-Protocol/ucp "$TMP_CLONE_DIR"
        INPUT_DIR="$TMP_CLONE_DIR"
        RESOLVED_SHA="$(git -C "$TMP_CLONE_DIR" rev-parse HEAD)"
        RESOLVED_CHANNEL="${CHANNEL_OVERRIDE:-draft}"
        RESOLVED_REF="${REF_OVERRIDE:-main}"
        SHOULD_WRITE_LOCK=1
    elif [[ "$POSITIONAL_ARG" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
        TMP_CLONE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/ucp-clone.XXXXXX")"
        BRANCH="release/$POSITIONAL_ARG"
        echo "Cloning UCP version $POSITIONAL_ARG (branch: $BRANCH)..."
        git clone -b "$BRANCH" --depth 1 https://github.com/Universal-Commerce-Protocol/ucp "$TMP_CLONE_DIR"
        INPUT_DIR="$TMP_CLONE_DIR"
        RESOLVED_SHA="$(git -C "$TMP_CLONE_DIR" rev-parse HEAD)"
        RESOLVED_CHANNEL="${CHANNEL_OVERRIDE:-release}"
        RESOLVED_REF="${REF_OVERRIDE:-$BRANCH}"
        SHOULD_WRITE_LOCK=1
    elif [[ "$POSITIONAL_ARG" =~ ^[0-9a-fA-F]{7,40}$ ]]; then
        TMP_CLONE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/ucp-clone.XXXXXX")"
        echo "Cloning UCP repository at commit $POSITIONAL_ARG..."
        git clone https://github.com/Universal-Commerce-Protocol/ucp "$TMP_CLONE_DIR"
        git -C "$TMP_CLONE_DIR" checkout --detach "$POSITIONAL_ARG"
        INPUT_DIR="$TMP_CLONE_DIR"
        RESOLVED_SHA="$(git -C "$TMP_CLONE_DIR" rev-parse HEAD)"
        DEFAULT_CHANNEL="draft"
        DEFAULT_REF="main"
        if [ -f "ucp-lock.json" ]; then
            read -r DEFAULT_CHANNEL DEFAULT_REF < <(python3 - <<'PY'
import json
from pathlib import Path

lock = json.loads(Path("ucp-lock.json").read_text(encoding="utf-8"))
print(lock.get("channel", "draft"), lock.get("ucp_ref", "main"))
PY
)
        fi
        RESOLVED_CHANNEL="${CHANNEL_OVERRIDE:-$DEFAULT_CHANNEL}"
        RESOLVED_REF="${REF_OVERRIDE:-$DEFAULT_REF}"
        SHOULD_WRITE_LOCK=1
    else
        TMP_CLONE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/ucp-clone.XXXXXX")"
        echo "Cloning UCP branch/ref $POSITIONAL_ARG..."
        git clone -b "$POSITIONAL_ARG" --depth 1 https://github.com/Universal-Commerce-Protocol/ucp "$TMP_CLONE_DIR"
        INPUT_DIR="$TMP_CLONE_DIR"
        RESOLVED_SHA="$(git -C "$TMP_CLONE_DIR" rev-parse HEAD)"
        if [[ "$POSITIONAL_ARG" == release/* ]]; then
            RESOLVED_CHANNEL="${CHANNEL_OVERRIDE:-release}"
        else
            RESOLVED_CHANNEL="${CHANNEL_OVERRIDE:-draft}"
        fi
        RESOLVED_REF="${REF_OVERRIDE:-$POSITIONAL_ARG}"
        SHOULD_WRITE_LOCK=1
    fi
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
    echo "Error: Could not locate schema directory under '$INPUT_DIR'." >&2
    exit 1
fi

OUTPUT_DIR="src/ucp_sdk/models/schemas"

# Scope compilation to vertical + Common + Transports so other verticals do not bleed in.
SCOPED_SCHEMA_DIR="$(mktemp -d "${TMPDIR:-/tmp}/ucp-schemas.XXXXXX")"
if compgen -G "$SCHEMA_DIR/*.json" > /dev/null; then
    cp "$SCHEMA_DIR"/*.json "$SCOPED_SCHEMA_DIR"/
fi
for dir in common transports "$VERTICAL"; do
    if [ -d "$SCHEMA_DIR/$dir" ]; then
        cp -R "$SCHEMA_DIR/$dir" "$SCOPED_SCHEMA_DIR/$dir"
    fi
done

echo "Compiling type bundle with ucp-schema generate-types..."
"$UCP_SCHEMA_BIN" generate-types --schema-dir "$SCOPED_SCHEMA_DIR" --output "$TMP_TYPES_JSON"

echo "Generating Pydantic models from compiled type bundle..."
mkdir -p "$OUTPUT_DIR" && rm -f "$OUTPUT_DIR/_generated.py"

uv run \
    --link-mode=copy \
    --extra-index-url https://pypi.org/simple python \
    -m datamodel_code_generator \
    --input "$TMP_TYPES_JSON" \
    --input-file-type jsonschema \
    --output "$OUTPUT_DIR/_generated.py" \
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

echo "Generating lazy package index in $OUTPUT_DIR/__init__.py..."
python3 - <<'PY'
import ast
from pathlib import Path

_SYNTHETIC_WRAPPERS = {"UCPSchemaTypes", "UcpSchemaTypes"}
schemas_dir = Path("src/ucp_sdk/models/schemas")
tree = ast.parse((schemas_dir / "_generated.py").read_text(encoding="utf-8"))
type_alias_cls = getattr(ast, "TypeAlias", ())
names: set[str] = set()
for node in tree.body:
    if isinstance(node, ast.ClassDef):
        names.add(node.name)
    elif isinstance(node, ast.Assign):
        names.update(t.id for t in node.targets if isinstance(t, ast.Name))
    elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        names.add(node.target.id)
    elif isinstance(node, type_alias_cls) and isinstance(getattr(node, "name", None), ast.Name):
        names.add(node.name.id)
exports = sorted(n for n in names if not n.startswith("_") and n not in _SYNTHETIC_WRAPPERS)
imports = ", ".join(exports)
all_items = ", ".join(f'"{n}"' for n in exports)
(schemas_dir / "__init__.py").write_text(
    '# generated by generate_models.sh\n\n'
    '"""Generated UCP schema models."""\n\n'
    'from __future__ import annotations\n\n'
    'import importlib\n'
    'from typing import TYPE_CHECKING, Any\n\n'
    f'if TYPE_CHECKING:\n    from ._generated import ({imports},)\n\n'
    f'__all__ = [{all_items},]\n\n\n'
    'def __getattr__(name: str) -> Any:\n'
    '    """Import public schema models and type aliases on first access."""\n'
    '    if name not in __all__:\n'
    '        msg = f"module {__name__!r} has no attribute {name!r}"\n'
    '        raise AttributeError(msg)\n'
    '    module = importlib.import_module("._generated", __name__)\n'
    '    value = getattr(module, name)\n'
    '    globals()[name] = value\n'
    '    return value\n\n\n'
    'def __dir__() -> list[str]:\n'
    '    """Return module attributes including lazily re-exported schema names."""\n'
    '    return sorted({*globals(), *__all__})\n',
    encoding="utf-8",
)
PY

echo "Formatting generated models..."
uv run ruff check --fix src/ucp_sdk/models
uv run ruff format src/ucp_sdk/models

# Normalize file endings (as pre-commit's end-of-file-fixer does)
python3 - <<'PY'
from pathlib import Path

for path in Path("src/ucp_sdk/models").rglob("*.py"):
    text = path.read_text(encoding="utf-8")
    fixed = text.rstrip("\n") + "\n" if text.strip() else ""
    if fixed != text:
        path.write_text(fixed, encoding="utf-8")
PY

if [ "$SHOULD_WRITE_LOCK" -eq 1 ]; then
    RESOLVED_CHANNEL="$RESOLVED_CHANNEL" \
    RESOLVED_REF="$RESOLVED_REF" \
    RESOLVED_SHA="$RESOLVED_SHA" \
    RESOLVED_SCHEMA_VERSION="$RESOLVED_SCHEMA_VERSION" \
    VERTICAL="$VERTICAL" \
    python3 - <<'PY'
import json
import os
from pathlib import Path

lock_data = {
    "channel": os.environ["RESOLVED_CHANNEL"],
    "ucp_ref": os.environ["RESOLVED_REF"],
    "ucp_sha": os.environ["RESOLVED_SHA"].lower(),
    "ucp_schema_version": os.environ["RESOLVED_SCHEMA_VERSION"],
    "vertical": os.environ["VERTICAL"],
}
Path("ucp-lock.json").write_text(
    json.dumps(lock_data, indent=2) + "\n",
    encoding="utf-8",
)
PY
    echo "Updated ucp-lock.json (channel=$RESOLVED_CHANNEL, ucp_ref=$RESOLVED_REF, ucp_sha=$RESOLVED_SHA)"
fi

echo "Done. Models generated in $OUTPUT_DIR/_generated.py"
