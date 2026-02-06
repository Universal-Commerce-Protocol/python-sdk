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

## Overview

This repository contains the Python SDK for the
[Universal Commerce Protocol (UCP)](https://ucp.dev). It provides Pydantic
models for UCP schemas, making it easy to build UCP-compliant applications
in Python.

## Installation

For now, you can install the SDK using the following commands:

```bash
# Create a directory for the SDK
mkdir sdk

# Clone the repository
git clone https://github.com/Universal-Commerce-Protocol/python-sdk.git

# Install dependencies
uv sync
```

## Development

### Prerequisites

This project uses `uv` for dependency management.

### Generating Pydantic Models

The models are automatically generated from the JSON schemas in the UCP
Specification.

Clone the UCP main repository in the same folder:

```bash
git clone https://github.com/Universal-Commerce-Protocol/ucp.git
```

To regenerate the models:

```bash
uv sync
./generate_models.sh
```

The generated code is automatically formatted using `ruff`.

## Contributing

We welcome community contributions. See our [Contribution Guide](https://github.com/Universal-Commerce-Protocol/ucp/blob/main/CONTRIBUTING.md) for details.

## License

UCP is an open-source project under the [Apache License 2.0](LICENSE).
