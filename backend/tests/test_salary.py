from decimal import Decimal

import pytest

from app.pipeline.salary import Salary, parse_salary

M = Decimal(1_000_000)


@pytest.mark.parametrize(
    ("text", "hint", "expected"),
    [
        ("15 - 25 triệu", None, Salary(15 * M, 25 * M, "VND")),
        ("15-25tr", None, Salary(15 * M, 25 * M, "VND")),
        ("Lương 15tr", None, Salary(15 * M, 15 * M, "VND")),
        ("từ 20 triệu", None, Salary(20 * M, None, "VND")),
        ("12-15M", None, Salary(12 * M, 15 * M, "VND")),
        ("30.000.000 VNĐ", None, Salary(30 * M, 30 * M, "VND")),
        ("1500k", None, Salary(Decimal(1_500_000), Decimal(1_500_000), "VND")),
        # ITviec labels this USD, but 35M can only be VND
        ("Up To 35M Gross", "USD", Salary(None, 35 * M, "VND")),
        ("1,000 - 1,750 USD", "USD", Salary(Decimal(1000), Decimal(1750), "USD")),
        ("1.500 – 2.000 USD", None, Salary(Decimal(1500), Decimal(2000), "USD")),
        ("upto 3000 USD", None, Salary(None, Decimal(3000), "USD")),
        ("tới 2000$", None, Salary(None, Decimal(2000), "USD")),
        ("$1.5k", None, Salary(Decimal(1500), Decimal(1500), "USD")),
        ("2000 - 2500", "USD", Salary(Decimal(2000), Decimal(2500), "USD")),
    ],
)
def test_parse_amounts(text: str, hint: str | None, expected: Salary) -> None:
    assert parse_salary(text, hint) == expected


@pytest.mark.parametrize(
    "text",
    ["Thỏa thuận", "thoa thuan", "Negotiable", "Cạnh tranh", "You'll love it", "Lương hấp dẫn"],
)
def test_negotiable(text: str) -> None:
    assert parse_salary(text) == Salary(None, None, None, negotiable=True)


@pytest.mark.parametrize("text", [None, "", "Gross", "Đãi ngộ tốt"])
def test_unparseable(text: str | None) -> None:
    assert parse_salary(text) is None
