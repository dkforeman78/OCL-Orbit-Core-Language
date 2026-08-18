from dataclasses import dataclass

from .diagnostics import SourceLocation


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
