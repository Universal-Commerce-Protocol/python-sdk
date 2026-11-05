<!--
   Copyright 2026 UCP Authors

   Licensed under the Apache License, Version 2.0 (the "License");
   you may not use this file except in compliance with the License.
   You may obtain a copy of the License at

       http://www.apache.org/licenses/LICENSE-2.0

   Unless required by applicable law or agreed to in writing, software
   distributed under the License is distributed on an "AS IS" BASIS,
   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
   See the License for the specific language governing permissions and
   limitations under the License.
-->

<p align="center">
  <h1 align="center">UCP Python SDK</h1>
</p>

<p align="center">
  <b>Official Python library for the Universal Commerce Protocol (UCP).</b>
</p>

<p align="center">
  <a href="https://pypi.org/project/ucp-sdk/"><img src="https://img.shields.io/pypi/v/ucp-sdk" alt="PyPI version"></a>
  <a href="https://pypi.org/project/ucp-sdk/"><img src="https://img.shields.io/pypi/pyversions/ucp-sdk" alt="Python versions"></a>
  <a href="https://github.com/Universal-Commerce-Protocol/python-sdk/blob/main/LICENSE"><img src="https://img.shields.io/github/license/Universal-Commerce-Protocol/python-sdk" alt="License"></a>
</p>

## Overview

This repository contains the Python SDK for the
[Universal Commerce Protocol (UCP)](https://ucp.dev). It provides Pydantic
models for UCP schemas, making it easy to build UCP-compliant applications in
Python.

### UCP Version Compatibility

Each version of the Python SDK is generated against a specific version of the
UCP schema:

| SDK Version       | UCP Schema Version |
| ----------------- | ------------------ |
| **`0.6.x`**       | **`2026-08-25`**   |
| `0.5.x`           | `2026-08-25`       |
| `0.4.x`           | `2026-04-08`       |
| `0.3.x`           | `2026-01-23`       |
| `0.2.x` / `0.1.x` | `2026-01-11`       |

## Installation

To use this SDK in your own project, install it from PyPI:

```bash
pip install ucp-sdk
```

Or, if you are managing your project with [uv](https://docs.astral.sh/uv/):

```bash
uv add ucp-sdk
```

## Usage

The example below parses a UCP checkout response and reads typed fields:

```python
from ucp_sdk.models import Checkout

# Parse a UCP checkout response
checkout = Checkout.model_validate(checkout_data)

# Access typed fields
print(checkout.status)  # "incomplete" | "ready_for_complete" | ...
print(checkout.currency)  # ISO 4217 currency code
for item in checkout.line_items:
    print(f"{item.item.title}: {item.quantity}")
```

### Available model packages

Generated Pydantic models and type aliases for the Shopping vertical
(`shopping`, `common`, and `transports` schemas) are lazily re-exported from
both `ucp_sdk.models` and `ucp_sdk.models.schemas`:

| Package                  | Description                                                                                                |
| ------------------------ | ---------------------------------------------------------------------------------------------------------- |
| `ucp_sdk.models`         | Top-level lazy re-export of all generated UCP models (checkout, cart, order, fulfillment, profile, etc.)   |
| `ucp_sdk.models.schemas` | Lazy re-export of Pydantic v2 models synthesized from the UCP Shopping, Common, and Transports JSON Schema |

### Validation

Pydantic models in `ucp_sdk.models` provide Tier 1 wire DTO structural typing
and tolerant-reader deserialization (preserving unknown extension fields for
forward compatibility) rather than full UCP protocol conformance.

To enforce normative UCP constraints (capability profile composition,
directional `ucp_request` / `ucp_response` visibility, `dependentRequired`,
`if`/`then`, `uniqueItems`, `contains`, `propertyNames`, `not.enum`, and
closed-schema rules):

1. **In-Memory Runtime Validation (Production Services)**: Compile your UCP
   discovery profile once at build or startup time into a standard JSON Schema
   Draft 2020-12 bundle
   (`ucp-schema generate-types --profile profile.json -o ucp.types.json`) or
   OpenAPI 3.1 specification
   (`ucp-schema generate-openapi --profile profile.json -o openapi.json`).
   Because `ucp-schema` resolves all UCP-specific annotations and capability
   extensions into standard JSON Schema 2020-12, you can validate incoming
   payloads in memory on every request using
   [`jsonschema`](https://python-jsonschema.readthedocs.io/) or OpenAPI ASGI
   middleware before deserializing into Pydantic DTOs:

```python
import json
from pathlib import Path
from jsonschema import Draft202012Validator
from ucp_sdk.models import Checkout

# At startup: load the pre-compiled schema bundle once into memory
bundle = json.loads(Path("ucp.types.json").read_text(encoding="utf-8"))
checkout_validator = Draft202012Validator(
    {**bundle["$defs"]["Checkout"], "$defs": bundle["$defs"]}
)

# Per request: validate in-memory, then parse into typed Pydantic DTO
checkout_validator.validate(raw_payload)
checkout = Checkout.model_validate(raw_payload)
```

2. **CLI & CI Conformance Testing**: Verify fixture payloads or live traffic
   dumps from the command line or CI pipelines with
   [`ucp-schema validate`](https://crates.io/crates/ucp-schema):

```bash
# Self-describing response or JSON-RPC envelope
ucp-schema validate payload.json --op read

# REST request against a discovery profile (or --schema checkout.json --request --op create)
ucp-schema validate payload.json --profile profile.json --op create
```

## Development

### Prerequisites

This project uses [`uv`](https://docs.astral.sh/uv/) for dependency management
and [`ucp-schema`](https://crates.io/crates/ucp-schema)
(`cargo install ucp-schema`) to synthesize the flat `$defs` bundle for model
generation.

### Setup

```bash
# Clone the repository
git clone https://github.com/Universal-Commerce-Protocol/python-sdk.git
cd python-sdk

# Install dependencies
uv sync
```

### Generating Pydantic Models

The models are automatically generated from the JSON schemas in the UCP
Specification using `ucp-schema generate-types` and `datamodel-codegen`.

To regenerate the models:

```bash
uv sync
./generate_models.sh <version-or-path>
```

Where `<version-or-path>` is a UCP release version (for example, `2026-08-25`),
a branch or commit SHA, or a path to a local UCP repository checkout.

If no argument is specified, the `main` branch of the
[UCP repo](https://github.com/Universal-Commerce-Protocol/ucp) will be used.

The generated code is automatically formatted using `ruff`.

### Running Tests

Run the test suite with:

```bash
uv run python -m unittest discover -s tests -p "test_*.py"
```

## Contributing

We welcome community contributions. See our
[Contribution Guide](https://github.com/Universal-Commerce-Protocol/.github/blob/main/CONTRIBUTING.md)
for details.

## License

UCP is an open-source project under the [Apache License 2.0](LICENSE).
