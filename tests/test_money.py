"""BR-7 exact money (AC-14, AC-15 at the parsing layer)."""

import pytest

from community_bridge.errors import DomainError, ErrorCode
from community_bridge.money import format_cents, money, parse_amount


@pytest.mark.parametrize(
    ("text", "cents"),
    [("300", 30000), ("300.00", 30000), ("0.01", 1), ("0.1", 10), ("10.05", 1005), ("1000000.00", 100000000),
     (" 42.50 ", 4250), ("0000300", 30000)],
)
def test_parse_valid(text, cents):
    assert parse_amount(text) == cents


@pytest.mark.parametrize(
    "bad",
    ["0", "0.00", "-5", "10.001", "abc", "", " ", "1e3", "1,000", "1000000.01", "12345678", ".5", "5.", "NaN",
     "٣٠٠", 300, 300.0, None, True],
)
def test_parse_invalid(bad):
    with pytest.raises(DomainError) as info:
        parse_amount(bad)
    assert info.value.code == ErrorCode.VALIDATION_ERROR
    assert info.value.details["field"] == "amount"


def test_no_float_drift():
    # 0.1 + 0.2 != 0.3 in binary floating point; cents arithmetic is exact.
    assert parse_amount("0.10") + parse_amount("0.20") == parse_amount("0.30") == 30


def test_format():
    assert format_cents(30000) == "300.00"
    assert format_cents(5) == "0.05"
    assert format_cents(-5) == "-0.05"
    assert money(970000) == {"cents": 970000, "amount": "9700.00", "currency": "USD"}
