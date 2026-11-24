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

Starting with the `2026-08-25` UCP specification release, PyPI releases use
spec-aligned `YYYYMMDD.MINOR.PATCH` versioning (for example, `20260825.0.0` for
UCP `2026-08-25`), while `main` tracks the `draft` UCP schema (`0.0.0.dev0`)
pinned in [`ucp-lock.json`](ucp-lock.json):

| SDK Version                      | UCP Schema Version                 | Channel             |
| -------------------------------- | ---------------------------------- | ------------------- |
| **`YYYYMMDD.MINOR.PATCH`**       | **`YYYY-MM-DD`** (`>= 2026-08-25`) | PyPI                |
| `0.0.0.dev0` (`main` git commit) | `draft` (`ucp@main` in `ucp-lock`) | Git (`main`)        |
| `0.5.x`                          | `2026-08-25`                       | PyPI (legacy `0.x`) |
| `0.4.x`                          | `2026-04-08`                       | PyPI                |
| `0.3.x`                          | `2026-01-23`                       | PyPI                |
| `0.2.x` / `0.1.x`                | `2026-01-11`                       | PyPI                |

## Installation

### Published Releases (PyPI)

Install the latest published UCP release from PyPI:

```bash
pip install ucp-sdk
```

Or with [uv](https://docs.astral.sh/uv/):

```bash
uv add ucp-sdk
```

To pin a specific UCP specification release epoch (for example, `2026-08-25`):

```bash
uv add "ucp-sdk>=20260825.0.0,<20260826.0.0"
```

### Unreleased `draft` Schemas (Git-Native)

To build against unreleased `draft` schemas on `main`, install directly from a
commit SHA on `Universal-Commerce-Protocol/python-sdk`:

```bash
uv add "ucp-sdk @ git+https://github.com/Universal-Commerce-Protocol/python-sdk.git@<commit-sha>"
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

In particular, open discriminated unions (such as `FulfillmentDestination`,
`FulfillmentMethod`, and `Provider`) are emitted as untagged unions with an open
`*Base` fallback (`type: str`), so payloads carrying a known `type`
discriminator that fail a variant-specific field constraint will deserialize
into `*Base` unless validated upfront against the compiled JSON Schema.

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
   [`jsonschema`](https://python-jsonschema.readthedocs.io/) (install
   `pip install "jsonschema[format-nongpl]"` to enable `uri` and `date-time`
   format validation via MIT-licensed validators without pulling in GPL
   dependencies) or OpenAPI ASGI middleware before deserializing into Pydantic
   DTOs:

```python
import json
from pathlib import Path
from jsonschema import Draft202012Validator
from ucp_sdk.models import Checkout

# At startup: load the pre-compiled schema bundle once into memory
bundle = json.loads(Path("ucp.types.json").read_text(encoding="utf-8"))
checkout_validator = Draft202012Validator(
    {**bundle["$defs"]["Checkout"], "$defs": bundle["$defs"]},
    format_checker=Draft202012Validator.FORMAT_CHECKER,
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

### Generating REST Server & Client Stubs

Compiling custom capability profiles requires `ucp-schema >= 2.0.0` (starter
Shopping models are already included in `ucp_sdk.models`), and
`datamodel-code-generator`, `fastapi`, and `httpx` are installed separately from
`ucp-sdk`.

1. **Merchant Server Stubs**: Compile `./profile.json` into OpenAPI 3.1,
   generate Pydantic v2 models, validate incoming payloads per
   [Validation](#validation), and mark union request bodies with `Body()`:

```bash
ucp-schema generate-openapi --profile ./profile.json -o openapi.json
datamodel-codegen --input openapi.json --input-file-type openapi --openapi-scopes schemas \
    --output merchant_models.py --output-model-type pydantic_v2.BaseModel \
    --enum-field-as-literal all --extra-fields allow --field-constraints \
    --reuse-model --use-standard-collections --use-title-as-name --use-type-alias
```

```python
from typing import Annotated
from fastapi import Body, FastAPI, Response
from merchant_models import Checkout, CheckoutCreateRequest, ErrorResponse

app = FastAPI()


async def _build_checkout(
    payload: CheckoutCreateRequest,
) -> Checkout | ErrorResponse:
    raise NotImplementedError


@app.post(
    "/checkout-sessions",
    response_model=Checkout | ErrorResponse,
    response_model_exclude_none=True,
    status_code=201,
)
async def create_checkout(
    payload: Annotated[CheckoutCreateRequest, Body()],
    response: Response,
) -> Checkout | ErrorResponse:
    result = await _build_checkout(payload)
    if isinstance(result, ErrorResponse):
        response.status_code = 200
    return result
```

2. **Platform Client Stubs**: Compile each supported platform profile
   (`--profile ./platform-2026-08-25.json` into `ucp_models_2026_08_25.py` with
   the same flags above) to negotiate versions, cache stubs, and sign requests
   (in multi-merchant services, pair `httpx` with an SSRF-safe transport
   blocking private IPs and validate fetched profiles per
   [Validation](#validation)):

```python
import base64
from collections.abc import Callable, Generator
from dataclasses import dataclass, field
import hashlib
from types import ModuleType
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4
import httpx
from pydantic import BaseModel, TypeAdapter


def _https_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError(f"Expected https:// URL, got {url!r}")
    return url


@dataclass
class UcpTransportAuth(httpx.Auth):
    agent_profile_url: str
    sign_request: Callable[[httpx.Request], None] | None = None
    requires_request_body = True

    def auth_flow(
        self, request: httpx.Request
    ) -> Generator[httpx.Request, httpx.Response, None]:
        h = request.headers
        h.setdefault("UCP-Agent", f'profile="{self.agent_profile_url}"')
        h.setdefault("Request-Id", str(uuid4()))
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            h.setdefault("Idempotency-Key", str(uuid4()))
        if request.content:
            digest = base64.b64encode(hashlib.sha256(request.content).digest())
            h["Content-Digest"] = f"sha-256=:{digest.decode()}:"
        if self.sign_request is not None:
            self.sign_request(request)
        yield request


@dataclass
class UcpRestStub:
    version: str
    models: ModuleType
    client: httpx.Client
    capabilities: frozenset[str] = frozenset()
    _adapters: dict[object, TypeAdapter[Any]] = field(
        default_factory=dict, init=False
    )

    def request_model(
        self,
        method: str,
        path: str,
        model: Any,
        body: BaseModel | None = None,
        **kw: Any,
    ) -> Any:
        data = body.model_dump(mode="json", exclude_none=True) if body else None
        resp = self.client.request(method, path.lstrip("/"), json=data, **kw)
        resp.raise_for_status()
        if model not in self._adapters:
            self._adapters[model] = TypeAdapter(
                model | self.models.ErrorResponse
            )
        return self._adapters[model].validate_json(resp.content)


@dataclass
class PlatformStubFactory:
    versions: dict[str, tuple[ModuleType, str]]
    sign_request: Callable[[httpx.Request], None] | None = None
    _cache: dict[object, UcpRestStub] = field(default_factory=dict, init=False)
    _profiles: dict[str, dict[str, Any]] = field(
        default_factory=dict, init=False
    )

    def close(self) -> None:
        for stub in self._cache.values():
            stub.client.close()
        self._cache.clear()

    def __enter__(self) -> "PlatformStubFactory":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def for_merchant(self, merchant_profile: dict[str, Any]) -> UcpRestStub:
        ucp = merchant_profile["ucp"]
        supported: dict[str, str] = ucp.get("supported_versions") or {}
        candidates = {ucp["version"], *supported} & self.versions.keys()
        if not candidates:
            raise ValueError(f"Unsupported UCP version: {ucp['version']}")
        ver = max(candidates)
        if ver != ucp["version"]:
            prof_url = _https_url(supported[ver])
            if prof_url not in self._profiles:
                with httpx.Client(follow_redirects=False) as c:
                    self._profiles[prof_url] = (
                        c.get(prof_url).raise_for_status().json()["ucp"]
                    )
            ucp = self._profiles[prof_url]
            if ucp.get("version") != ver or "supported_versions" in ucp:
                raise ValueError(f"Invalid version profile: {ver}")
        svcs = ucp.get("services", {}).get("dev.ucp.shopping", ())
        rest = next((s for s in svcs if s.get("transport") == "rest"), None)
        if rest is None:
            raise ValueError("Merchant profile has no REST shopping endpoint")
        ep = _https_url(rest["endpoint"]).rstrip("/")
        # Merchant-advertised capability names (not pruned to platform profile)
        caps = frozenset(ucp.get("capabilities", ()))
        key = (ver, ep, caps)
        if key not in self._cache:
            models, agent_url = self.versions[ver]
            auth = UcpTransportAuth(_https_url(agent_url), self.sign_request)
            http = httpx.Client(base_url=f"{ep}/", auth=auth)
            self._cache[key] = UcpRestStub(ver, models, http, caps)
        return self._cache[key]
```

```python
from pydantic import TypeAdapter
import ucp_models_2026_08_25 as m

with PlatformStubFactory(
    {"2026-08-25": (m, "https://platform.example.com/profiles/2026-08-25.json")}
) as factory:
    stub = factory.for_merchant(merchant_profile)
    req = TypeAdapter(m.CheckoutCreateRequest).validate_python(
        {"line_items": [{"item": {"id": "sku_1"}, "quantity": 1}]}
    )
    checkout = stub.request_model("POST", "/checkout-sessions", m.Checkout, req)
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
Specification using `ucp-schema generate-types` and `datamodel-codegen`, with
provenance recorded in [`ucp-lock.json`](ucp-lock.json).

To regenerate the models:

```bash
uv sync

# Reproduce committed models from the exact commit pinned in ucp-lock.json
./generate_models.sh

# Advance ucp-lock.json and regenerate models from ucp@main (draft channel)
./generate_models.sh --ref main --write-lock

# Pin ucp-lock.json to a release branch
./generate_models.sh --ref release/2026-08-25 --write-lock

# Regenerate models from a local UCP checkout without modifying ucp-lock.json
./generate_models.sh --spec-dir /path/to/ucp
```

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
