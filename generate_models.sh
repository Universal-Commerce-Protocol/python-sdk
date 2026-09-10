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

# Generate modular domain structure re-exporting from models.py
python3 - <<'PY'
from pathlib import Path

BASE = Path("src/ucp_sdk/models/schemas")

MODULES = {
    # Shopping domain
    "shopping/checkout.py": """\"\"\"Shopping checkout models.\"\"\"
from __future__ import annotations

from ..models import (
    Checkout,
    CheckoutCompleteRequest,
    CheckoutCreateRequest,
    CheckoutUpdateRequest,
)

__all__ = [
    "Checkout",
    "CheckoutCompleteRequest",
    "CheckoutCreateRequest",
    "CheckoutUpdateRequest",
]
""",
    "shopping/cart.py": """\"\"\"Shopping cart models.\"\"\"
from __future__ import annotations

from ..models import (
    Cart,
    CartCreateRequest,
    CartUpdateRequest,
)

__all__ = [
    "Cart",
    "CartCreateRequest",
    "CartUpdateRequest",
]
""",
    "shopping/order.py": """\"\"\"Shopping order models.\"\"\"
from __future__ import annotations

from ..models import (
    Order,
    OrderCreateRequest,
    OrderUpdateRequest,
)

__all__ = [
    "Order",
    "OrderCreateRequest",
    "OrderUpdateRequest",
]
""",
    "shopping/fulfillment.py": """\"\"\"Shopping fulfillment models.\"\"\"
from __future__ import annotations

from ..models import (
    Fulfillment,
    FulfillmentCreateRequest,
    FulfillmentUpdateRequest,
)

__all__ = [
    "Fulfillment",
    "FulfillmentCreateRequest",
    "FulfillmentUpdateRequest",
]
""",
    "shopping/types/fulfillment_destination.py": """\"\"\"Fulfillment destination models.\"\"\"
from __future__ import annotations

from ...models import (
    FulfillmentDestination,
    LocationDestination,
    LocationDestinationCreateRequest,
    LocationDestinationUpdateRequest,
    ShippingDestination,
    ShippingDestinationCreateRequest,
    ShippingDestinationUpdateRequest,
)

__all__ = [
    "FulfillmentDestination",
    "LocationDestination",
    "LocationDestinationCreateRequest",
    "LocationDestinationUpdateRequest",
    "ShippingDestination",
    "ShippingDestinationCreateRequest",
    "ShippingDestinationUpdateRequest",
]
""",
    "shopping/types/fulfillment_method.py": """\"\"\"Fulfillment method models.\"\"\"
from __future__ import annotations

from ...models import (
    FulfillmentMethod,
    FulfillmentMethodCreateRequest,
    FulfillmentMethodUpdateRequest,
)

__all__ = [
    "FulfillmentMethod",
    "FulfillmentMethodCreateRequest",
    "FulfillmentMethodUpdateRequest",
]
""",
    "shopping/types/business_fulfillment_config.py": """\"\"\"Business fulfillment configuration.\"\"\"
from __future__ import annotations

from ...models import BusinessFulfillmentConfig

__all__ = ["BusinessFulfillmentConfig"]
""",
    "shopping/types/line_item.py": """\"\"\"Line item models.\"\"\"
from __future__ import annotations

from ...models import (
    LineItem,
    LineItemCreateRequest,
    LineItemModel,
    LineItemUpdateRequest,
)

__all__ = [
    "LineItem",
    "LineItemCreateRequest",
    "LineItemModel",
    "LineItemUpdateRequest",
]
""",
    "shopping/types/item.py": """\"\"\"Item models.\"\"\"
from __future__ import annotations

from ...models import (
    Item,
    ItemCreateRequest,
    ItemUpdateRequest,
)

__all__ = [
    "Item",
    "ItemCreateRequest",
    "ItemUpdateRequest",
]
""",
    "shopping/types/buyer.py": """\"\"\"Buyer models.\"\"\"
from __future__ import annotations

from ...models import Buyer

__all__ = ["Buyer"]
""",
    "shopping/types/attribution.py": """\"\"\"Attribution models.\"\"\"
from __future__ import annotations

from ...models import Attribution

__all__ = ["Attribution"]
""",
    "shopping/types/order_line_item.py": """\"\"\"Order line item models.\"\"\"
from __future__ import annotations

from ...models import OrderLineItem

__all__ = ["OrderLineItem"]
""",
    "shopping/types/product.py": """\"\"\"Product models.\"\"\"
from __future__ import annotations

from ...models import DetailProduct, FulfillmentProduct, Product

__all__ = ["Product", "DetailProduct", "FulfillmentProduct"]
""",
    "shopping/types/variant.py": """\"\"\"Variant models.\"\"\"
from __future__ import annotations

from ...models import FulfillmentVariant, LookupVariant, Variant

__all__ = ["Variant", "LookupVariant", "FulfillmentVariant"]
""",
    "shopping/types/option_value.py": """\"\"\"Option value models.\"\"\"
from __future__ import annotations

from ...models import DetailOptionValue, OptionValue, ProductOption, SelectedOption

__all__ = ["OptionValue", "SelectedOption", "ProductOption", "DetailOptionValue"]
""",
    "shopping/types/location_summary.py": """\"\"\"Location summary models.\"\"\"
from __future__ import annotations

from ...models import (
    LocationSummary,
    LocationSummaryCreateRequest,
    LocationSummaryUpdateRequest,
)

__all__ = [
    "LocationSummary",
    "LocationSummaryCreateRequest",
    "LocationSummaryUpdateRequest",
]
""",
    "shopping/__init__.py": """\"\"\"Shopping models.\"\"\"
from .checkout import *  # noqa: F403
from .cart import *  # noqa: F403
from .order import *  # noqa: F403
from .fulfillment import *  # noqa: F403
""",
    "shopping/types/__init__.py": """\"\"\"Shopping auxiliary types.\"\"\"
from .fulfillment_destination import *  # noqa: F403
from .fulfillment_method import *  # noqa: F403
from .business_fulfillment_config import *  # noqa: F403
from .line_item import *  # noqa: F403
from .item import *  # noqa: F403
from .buyer import *  # noqa: F403
from .attribution import *  # noqa: F403
from .order_line_item import *  # noqa: F403
from .product import *  # noqa: F403
from .variant import *  # noqa: F403
from .option_value import *  # noqa: F403
from .location_summary import *  # noqa: F403
""",

    # Common domain
    "common/types/totals.py": """\"\"\"Totals models.\"\"\"
from __future__ import annotations

from ...models import (
    Total,
    TotalCreateRequest,
    Totals,
    TotalsCreateRequest,
    TotalsUpdateRequest,
    TotalUpdateRequest,
)

__all__ = [
    "Totals",
    "TotalsCreateRequest",
    "TotalsUpdateRequest",
    "Total",
    "TotalCreateRequest",
    "TotalUpdateRequest",
]
""",
    "common/types/amount.py": """\"\"\"Amount and price models.\"\"\"
from __future__ import annotations

from ...models import Amount, Price, SignedAmount

__all__ = ["Amount", "Price", "SignedAmount"]
""",
    "common/types/unit.py": """\"\"\"Unit models.\"\"\"
from __future__ import annotations

from ...models import QuantityUnit, Unit, UnitPrice

__all__ = ["Unit", "QuantityUnit", "UnitPrice"]
""",
    "common/types/signals.py": """\"\"\"Signals models.\"\"\"
from __future__ import annotations

from ...models import Signals

__all__ = ["Signals"]
""",
    "common/types/description.py": """\"\"\"Description models.\"\"\"
from __future__ import annotations

from ...models import Description

__all__ = ["Description"]
""",
    "common/types/card_payment_instrument.py": """\"\"\"Card payment instrument models.\"\"\"
from __future__ import annotations

from ...models import AvailablePaymentInstrument, PaymentInstrument

CardPaymentInstrument = PaymentInstrument

__all__ = ["CardPaymentInstrument", "AvailablePaymentInstrument", "PaymentInstrument"]
""",
    "common/types/payment_instrument.py": """\"\"\"Payment instrument models.\"\"\"
from __future__ import annotations

from ...models import AvailablePaymentInstrument, PaymentInstrument, SelectedPaymentInstrument

__all__ = ["PaymentInstrument", "AvailablePaymentInstrument", "SelectedPaymentInstrument"]
""",
    "common/types/error_response.py": """\"\"\"Error response models.\"\"\"
from __future__ import annotations

from ...models import Error, ErrorCode, ErrorResponse

__all__ = ["ErrorResponse", "Error", "ErrorCode"]
""",
    "common/types/postal_address.py": """\"\"\"Postal address models.\"\"\"
from __future__ import annotations

from ...models import PostalAddress

__all__ = ["PostalAddress"]
""",
    "common/types/payment.py": """\"\"\"Payment models.\"\"\"
from __future__ import annotations

from ...models import Payment, PaymentCredential

__all__ = ["Payment", "PaymentCredential"]
""",
    "common/types/message.py": """\"\"\"Message models.\"\"\"
from __future__ import annotations

from ...models import Message, MessageError, MessageInfo, MessageWarning

__all__ = ["Message", "MessageError", "MessageInfo", "MessageWarning"]
""",
    "common/types/locality.py": """\"\"\"Locality models.\"\"\"
from __future__ import annotations

from ...models import Locality

__all__ = ["Locality"]
""",
    "common/types/context.py": """\"\"\"Context models.\"\"\"
from __future__ import annotations

from ...models import Context

__all__ = ["Context"]
""",
    "common/types/policy.py": """\"\"\"Policy models.\"\"\"
from __future__ import annotations

from ...models import Policy

__all__ = ["Policy"]
""",
    "common/types/actions.py": """\"\"\"Actions models.\"\"\"
from __future__ import annotations

from ...models import Actions

__all__ = ["Actions"]
""",
    "common/__init__.py": """\"\"\"Common models.\"\"\"
from .types import *  # noqa: F403
""",
    "common/types/__init__.py": """\"\"\"Common types.\"\"\"
from .totals import *  # noqa: F403
from .amount import *  # noqa: F403
from .unit import *  # noqa: F403
from .signals import *  # noqa: F403
from .description import *  # noqa: F403
from .card_payment_instrument import *  # noqa: F403
from .payment_instrument import *  # noqa: F403
from .error_response import *  # noqa: F403
from .postal_address import *  # noqa: F403
from .payment import *  # noqa: F403
from .message import *  # noqa: F403
from .locality import *  # noqa: F403
from .context import *  # noqa: F403
from .policy import *  # noqa: F403
from .actions import *  # noqa: F403
""",

    # Root schemas
    "profile.py": """\"\"\"Profile models.\"\"\"
from __future__ import annotations

from .models import JwkPublicKey, Profile, ProfileBase

__all__ = ["Profile", "ProfileBase", "JwkPublicKey"]
""",
    "capability.py": """\"\"\"Capability models.\"\"\"
from __future__ import annotations

from .models import (
    CapabilityBase,
    CapabilityBusinessSchema,
    CapabilityPlatformSchema,
    CapabilityResponseSchema,
)

Base = CapabilityBase
__all__ = [
    "CapabilityBase",
    "CapabilityBusinessSchema",
    "CapabilityPlatformSchema",
    "CapabilityResponseSchema",
    "Base",
]
""",
    "service.py": """\"\"\"Service models.\"\"\"
from __future__ import annotations

from .models import (
    ServiceBase,
    ServiceBusinessSchema,
    ServicePlatformSchema,
    ServiceResponseSchema,
)

Base = ServiceBase
__all__ = [
    "ServiceBase",
    "ServiceBusinessSchema",
    "ServicePlatformSchema",
    "ServiceResponseSchema",
    "Base",
]
""",
    "payment_handler.py": """\"\"\"Payment handler models.\"\"\"
from __future__ import annotations

from .models import (
    PaymentHandlerBase,
    PaymentHandlerBusinessSchema,
    PaymentHandlerPlatformSchema,
    PaymentHandlerResponseSchema,
)

Base = PaymentHandlerBase
__all__ = [
    "PaymentHandlerBase",
    "PaymentHandlerBusinessSchema",
    "PaymentHandlerPlatformSchema",
    "PaymentHandlerResponseSchema",
    "Base",
]
""",
    "ucp.py": """\"\"\"UCP core models.\"\"\"
from __future__ import annotations

from .models import (
    Ucp,
    UcpBase,
    UcpBusinessSchema,
    UcpCreateRequest,
    UcpEntity,
    UcpPlatformSchema,
    UcpUpdateRequest,
)

__all__ = [
    "Ucp",
    "UcpBase",
    "UcpBusinessSchema",
    "UcpPlatformSchema",
    "UcpEntity",
    "UcpCreateRequest",
    "UcpUpdateRequest",
]
""",
}

for rel_path, code in MODULES.items():
    target = BASE / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(code, encoding="utf-8")

print("Generated modular domain schemas successfully.")
PY

# Package root re-exports
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

echo "Done. Models generated in $MODELS_FILE and modular packages."
