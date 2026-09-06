"""Validated integer spelling and bounded magnitude decoding."""


def literal_parts(spelling: str) -> tuple[int, str]:
    if spelling.startswith("0x"):
        return 16, spelling[2:]
    if spelling.startswith("0b"):
        return 2, spelling[2:]
    return 10, spelling


def bounded_integer(spelling: str, maximum: int) -> int:
    """Return maximum + 1 on overflow; never construct an unbounded integer.

    The lexer has already validated digits and separators.
    """
    base, digits = literal_parts(spelling)
    value = 0
    for digit in digits:
        if digit == "_":
            continue
        value = value * base + int(digit, base)
        if value > maximum:
            return maximum + 1
    return value
