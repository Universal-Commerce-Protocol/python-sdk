"""Card payment instrument models."""

from __future__ import annotations

from ...models import AvailablePaymentInstrument, PaymentInstrument

CardPaymentInstrument = PaymentInstrument

__all__ = [
    "CardPaymentInstrument",
    "AvailablePaymentInstrument",
    "PaymentInstrument",
]
