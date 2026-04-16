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

## Installation

Install from PyPI:

```bash
pip install ucp-sdk
```

Or with [uv](https://docs.astral.sh/uv/):

```bash
uv add ucp-sdk
```

## Usage

The SDK provides typed [Pydantic v2](https://docs.pydantic.dev/) models for all
UCP schemas. Import models from `ucp_sdk.models.schemas`:

```python
from ucp_sdk.models.schemas.shopping.checkout import Checkout
from ucp_sdk.models.schemas.shopping.types.line_item import LineItem
from ucp_sdk.models.schemas.shopping.types.total import Total

# Parse a UCP checkout response
checkout = Checkout.model_validate(checkout_data)

# Access typed fields
print(checkout.status)       # "incomplete" | "ready_for_complete" | ...
print(checkout.currency)     # ISO 4217 currency code
for item in checkout.line_items:
    print(f"{item.item.title}: {item.quantity}")
```

### Available model packages

| Package | Description |
|---------|-------------|
| `ucp_sdk.models.schemas.shopping` | Checkout, cart, order, payment models |
| `ucp_sdk.models.schemas.shopping.types` | Line items, totals, buyer, fulfillment, etc. |
| `ucp_sdk.models.schemas.transports` | REST, MCP, and embedded protocol bindings |
| `ucp_sdk.models.schemas` | Service definitions, capabilities, payment handlers |

### Validation

All models support Pydantic validation and serialization:

```python
from pydantic import ValidationError

# Validate data against UCP schemas
try:
    checkout = Checkout.model_validate(data)
except ValidationError as e:
    print(e.errors())

# Serialize to JSON-compatible dict
checkout_dict = checkout.model_dump(exclude_none=True)
```

## Development

### Prerequisites

This project uses [`uv`](https://docs.astral.sh/uv/) for dependency management.

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
Specification.

To regenerate the models:

```bash
uv sync
./generate_models.sh <version>
```

Where `<version>` is the version of the UCP specification to use (for example,
"2026-01-23").

If no version is specified, the `main` branch of the
[UCP repo](https://github.com/Universal-Commerce-Protocol/ucp) will be used.

The generated code is automatically formatted using `ruff`.

## Contributing

We welcome community contributions. See our
[Contribution Guide](https://github.com/Universal-Commerce-Protocol/.github/blob/main/CONTRIBUTING.md)
for details.

## License

UCP is an open-source project under the [Apache License 2.0](LICENSE).
