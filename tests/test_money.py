from decimal import Decimal

import pytest

from collectos.money import MoneyError, to_money


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("1000", "1000.00"),
        ("1,23,456.50", "123456.50"),
        ("₹ 2,500", "2500.00"),
        ("Rs. 99.9", "99.90"),
        ("INR 10", "10.00"),
        ("(500.00)", "-500.00"),
        ("-12.34", "-12.34"),
        (7, "7.00"),
        (Decimal("0.10"), "0.10"),
    ],
)
def test_valid_amounts(raw, expected):
    assert to_money(raw) == Decimal(expected)


@pytest.mark.parametrize("raw", [10.5, 0.1, True, None, [], "abc", "", "1e3", "NaN", "Infinity", "12.345", "1.2.3"])
def test_rejected_amounts(raw):
    with pytest.raises(MoneyError):
        to_money(raw)


def test_decimal_sums_are_exact():
    # The classic float failure: 0.1 + 0.2 != 0.3
    assert to_money("0.10") + to_money("0.20") == to_money("0.30")


def test_trailing_zero_decimal_over_precision_is_accepted():
    assert to_money(Decimal("5.100")) == Decimal("5.10")
