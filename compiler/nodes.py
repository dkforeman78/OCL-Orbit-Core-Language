from dataclasses import dataclass

from .diagnostics import SourceLocation

# The only integer type in OCL 0.1. Both the parser and the semantic analyzer
# bound literals against this, so it lives here rather than in either of them.
I32_MAX = 2_147_483_647


@dataclass(frozen=True)
class IntegerLiteral:
    value: int
    location: SourceLocation


@dataclass(frozen=True)
class ReturnStatement:
    expression: IntegerLiteral
    location: SourceLocation


@dataclass(frozen=True)
class Function:
    name: str
    return_type: str
    body: tuple[ReturnStatement, ...]
    location: SourceLocation


@dataclass(frozen=True)
class Program:
    functions: tuple[Function, ...]
