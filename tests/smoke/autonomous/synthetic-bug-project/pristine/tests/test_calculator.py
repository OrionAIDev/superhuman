"""Tests for the synthetic calculator. Two fail until the loop fixes the bugs."""

from src.calculator import add, sum_to, double


def test_add() -> None:
    assert add(2, 3) == 5


def test_sum_to_inclusive() -> None:
    assert sum_to(5) == 15  # fails while BUG 1 (off-by-one) is present


def test_double() -> None:
    assert double(4) == 8  # fails while BUG 2 (x + 2) is present
