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

Install the SDK using pip or uv:

```bash
pip install ucp-sdk
```

or

```bash
uv init  # if not already in an initialized project
uv add ucp-sdk
```

> **Note:** `uv add` requires an initialized project directory. If you're not starting a new project, ensure you're in a directory with a `pyproject.toml` file, or run `uv init` first.

## Development

### Prerequisites

This project uses `uv` for dependency management.

### Generating Pydantic Models

The models are automatically generated from the JSON schemas in the UCP
Specification.

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
