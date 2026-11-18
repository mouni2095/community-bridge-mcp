"""Exact USD money handling (BR-7). Integers of cents only; never float."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from .errors import DomainError, ErrorCode

CURRENCY = "USD"
MAX_REQUEST_CENTS = 1_000_000_00  # $1,000,000.00 sanity cap (spec §2.3, open question 2)
AMOUNT_PATTERN = r"^[0-9]{1,7}(\.[0-9]{1,2})?$"
_AMOUNT_RE = re.compile(AMOUNT_PATTERN)


def parse_amount(value: object, field: str = "amount") -> int:
    """Parse a decimal string such as "300" or "300.05" into integer cents.

    Rejects non-strings (including JSON numbers, which may already have been
    rounded through binary floating point), more than two decimals, zero,
    negatives and values above the cap.
    """
    if not isinstance(value, str):
        raise DomainError(
            ErrorCode.VALIDATION_ERROR,
            f"{field} must be a decimal string such as \"300.00\"",
            {"field": field},
        )
    text = value.strip()
    if not _AMOUNT_RE.fullmatch(text):
        raise DomainError(
            ErrorCode.VALIDATION_ERROR,
            f"{field} must be a positive USD amount with at most 2 decimal places",
            {"field": field},
        )
    try:
        cents = int((Decimal(text) * 100).to_integral_exact())
    except InvalidOperation:  # pragma: no cover - regex already guarantees validity
        raise DomainError(ErrorCode.VALIDATION_ERROR, f"{field} is not a number", {"field": field})
    if cents <= 0:
        raise DomainError(ErrorCode.VALIDATION_ERROR, f"{field} must be greater than 0", {"field": field})
    if cents > MAX_REQUEST_CENTS:
        raise DomainError(
            ErrorCode.VALIDATION_ERROR,
            f"{field} must not exceed {format_cents(MAX_REQUEST_CENTS)}",
            {"field": field},
        )
    return cents


def format_cents(cents: int) -> str:
    """30000 -> "300.00"; -5 -> "-0.05"."""
    sign = "-" if cents < 0 else ""
    whole, frac = divmod(abs(cents), 100)
    return f"{sign}{whole}.{frac:02d}"


def money(cents: int) -> dict:
    return {"cents": cents, "amount": format_cents(cents), "currency": CURRENCY}
