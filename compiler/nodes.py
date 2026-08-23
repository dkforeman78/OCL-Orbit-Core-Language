from __future__ import annotations

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
class IdentifierExpression:
    name: str
    location: SourceLocation


@dataclass(frozen=True)
class BinaryExpression:
    left: Expression
    operator: str
    right: Expression
    location: SourceLocation


@dataclass(frozen=True)
class CallExpression:
    callee: str
    arguments: tuple[Expression, ...]
    location: SourceLocation


Expression = IntegerLiteral | IdentifierExpression | BinaryExpression | CallExpression


@dataclass(frozen=True)
class ReturnStatement:
    expression: Expression
    location: SourceLocation


@dataclass(frozen=True)
class Parameter:
    name: str
    type_name: str
    location: SourceLocation


@dataclass(frozen=True)
class Function:
    name: str
    return_type: str
    body: tuple[ReturnStatement, ...]
    location: SourceLocation
    parameters: tuple[Parameter, ...] = ()


@dataclass(frozen=True)
class Program:
    functions: tuple[Function, ...]
