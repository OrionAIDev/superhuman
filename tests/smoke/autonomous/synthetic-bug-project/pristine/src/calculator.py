"""A tiny calculator with two intentional bugs for the autonomous-loop smoke."""


def add(a: int, b: int) -> int:
    """Return the sum of two integers.

    Args:
        a: first addend.
        b: second addend.

    Returns:
        The arithmetic sum ``a + b``.
    """
    return a + b


def sum_to(n: int) -> int:
    """Return the sum of all integers from 1 to ``n`` inclusive.

    Args:
        n: the inclusive upper bound (n >= 1).

    Returns:
        1 + 2 + ... + n.
    """
    total = 0
    for i in range(1, n):  # BUG 1: off-by-one, should be range(1, n + 1)
        total += i
    return total


def double(x: int) -> int:
    """Return twice ``x``.

    Args:
        x: the value to double.

    Returns:
        ``x * 2``.
    """
    return x + 2  # BUG 2: TODO stub, should be x * 2
