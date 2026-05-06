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
"""Semantic smoke checks for the regenerated UCP schema models.

These tests prevent silent schema regressions where datamodel-codegen
produces models that import cleanly but encode the wrong constraints —
e.g. a closed `Literal[...]` over `Total.type` when upstream documents
the field as an open vocabulary, or `Field(ge=0)` on `Total.amount` when
upstream switched to `signed_amount.json`.

The current acceptance bar is: regenerate from a UCP commit that contains
Cart, Catalog, identity-linking, info/warning codes, signed totals, and
the four `Context` fields, then prove each is usable from Python.
"""

from __future__ import annotations


def test_provenance_constant_present() -> None:
    """generate_models.sh writes the source ref into _schema_ref.py."""
    from ucp_sdk._schema_ref import GENERATE_COMMAND, UCP_SCHEMA_REF

    assert isinstance(UCP_SCHEMA_REF, str)
    assert len(UCP_SCHEMA_REF) >= 7  # at least short SHA
    assert isinstance(GENERATE_COMMAND, str)
    assert "generate_models.sh" in GENERATE_COMMAND


def test_context_carries_all_four_fields() -> None:
    """`Context` exposes intent, language, currency, and eligibility."""
    from ucp_sdk.models.schemas.shopping.types.context import Context

    c = Context(intent="buy a gift", language="en", currency="USD")
    assert c.intent == "buy a gift"
    assert c.language == "en"
    assert c.currency == "USD"
    # eligibility is a list of reverse-domain identifiers
    assert hasattr(c, "eligibility")


def test_total_type_is_open_vocabulary() -> None:
    """`Total.type` is a free string per the spec, not a closed Literal."""
    from ucp_sdk.models.schemas.shopping.types.total import Total

    # Well-known values must work
    for known in (
        "subtotal",
        "items_discount",
        "discount",
        "fulfillment",
        "tax",
        "fee",
        "total",
    ):
        assert Total(type=known, amount=100).type == known

    # Business-defined values must also work — the spec allows additional
    # values beyond the well-known list.
    assert Total(type="merchant_custom", amount=123).type == "merchant_custom"
    assert (
        Total(type="dev.example.shipping_insurance", amount=50).type
        == "dev.example.shipping_insurance"
    )


def test_total_amount_accepts_negative() -> None:
    """`Total.amount` resolves to `SignedAmount` (a signed integer)."""
    from ucp_sdk.models.schemas.shopping.types.total import Total

    # Discounts and refunds carry negative amounts post-signed-totals.
    assert Total(type="discount", amount=-100).amount.root == -100
    assert Total(type="items_discount", amount=-100).amount.root == -100
    # Charges are positive.
    assert Total(type="fulfillment", amount=100).amount.root == 100
    assert Total(type="fee", amount=10).amount.root == 10


def test_signed_amount_accepts_negative() -> None:
    """`SignedAmount` itself accepts negative integers."""
    from ucp_sdk.models.schemas.shopping.types.signed_amount import (
        SignedAmount,
    )

    assert SignedAmount(root=-1).root == -1
    assert SignedAmount(root=0).root == 0
    assert SignedAmount(root=1_000_000).root == 1_000_000


def test_message_codes_are_open_vocabularies() -> None:
    """error_code / info_code / warning_code accept arbitrary strings."""
    from ucp_sdk.models.schemas.shopping.types.error_code import ErrorCode
    from ucp_sdk.models.schemas.shopping.types.info_code import InfoCode
    from ucp_sdk.models.schemas.shopping.types.warning_code import WarningCode

    # Spec examples
    assert ErrorCode(root="not_found").root == "not_found"
    assert InfoCode(root="identity_optional").root == "identity_optional"
    assert WarningCode(root="some_warning").root == "some_warning"

    # Caller-defined codes
    assert ErrorCode(root="merchant_custom_error").root == "merchant_custom_error"


def test_cart_module_importable() -> None:
    """Cart capability schemas are present."""
    from ucp_sdk.models.schemas.shopping.cart import Cart

    assert Cart is not None


def test_catalog_modules_importable_with_get_product() -> None:
    """Catalog search + lookup, with get_product nested defs."""
    from ucp_sdk.models.schemas.shopping import catalog_lookup, catalog_search

    assert catalog_search is not None
    assert catalog_lookup.CatalogLookup is not None
    assert catalog_lookup.LookupRequest is not None
    assert catalog_lookup.LookupResponse is not None
    assert catalog_lookup.GetProductRequest is not None
    assert catalog_lookup.GetProductResponse is not None
    assert catalog_lookup.DetailProduct is not None


def test_identity_linking_importable() -> None:
    """Identity-linking common schema is present."""
    from ucp_sdk.models.schemas.common.identity_linking.dev.ucp.common import (
        IdentityLinking,
    )

    assert IdentityLinking is not None
