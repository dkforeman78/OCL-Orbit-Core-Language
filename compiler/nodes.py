from __future__ import annotations

from dataclasses import dataclass

from .diagnostics import SourceLocation
from .types import TypeRef

# The only integer type in OCL 0.8. Both the parser and the semantic analyzer
# bound literals against this, so it lives here rather than in either of them.
I32_MAX = 2_147_483_647


@dataclass(frozen=True)
class IntegerLiteral:
    value: int
    location: SourceLocation


@dataclass(frozen=True)
class BooleanLiteral:
    value: bool
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


@dataclass(frozen=True)
class IfExpression:
    condition: Expression
    then_expression: Expression
    else_expression: Expression
    location: SourceLocation


@dataclass(frozen=True)
class UnaryExpression:
    operator: str
    operand: Expression
    location: SourceLocation


@dataclass(frozen=True)
class ArrayLiteral:
    elements: tuple[Expression, ...]
    location: SourceLocation


@dataclass(frozen=True)
class IndexExpression:
    base: Expression
    index: Expression
    location: SourceLocation


@dataclass(frozen=True)
class StructLiteralField:
    name: str
    expression: Expression
    location: SourceLocation


@dataclass(frozen=True)
class StructLiteral:
    type_name: TypeRef
    fields: tuple[StructLiteralField, ...]
    location: SourceLocation


@dataclass(frozen=True)
class FieldExpression:
    base: Expression
    field: str
    location: SourceLocation


@dataclass(frozen=True)
class EnumVariantExpression:
    enum_name: str
    variant: str
    location: SourceLocation


@dataclass(frozen=True)
class MatchArm:
    enum_name: str
    variant: str
    expression: Expression
    location: SourceLocation


@dataclass(frozen=True)
class MatchExpression:
    scrutinee: Expression
    arms: tuple[MatchArm, ...]
    location: SourceLocation


Expression = BooleanLiteral | IntegerLiteral | IdentifierExpression | BinaryExpression | CallExpression | IfExpression | UnaryExpression | ArrayLiteral | IndexExpression | StructLiteral | FieldExpression | EnumVariantExpression | MatchExpression


@dataclass(frozen=True)
class ReturnStatement:
    expression: Expression
    location: SourceLocation


@dataclass(frozen=True)
class LetStatement:
    name: str
    type_name: str
    initializer: Expression
    location: SourceLocation


@dataclass(frozen=True)
class VarStatement:
    name: str
    type_name: str
    initializer: Expression
    location: SourceLocation


@dataclass(frozen=True)
class AssignmentStatement:
    name: str
    expression: Expression
    location: SourceLocation


@dataclass(frozen=True)
class IndexAssignmentStatement:
    name: str
    index: Expression
    expression: Expression
    location: SourceLocation


@dataclass(frozen=True)
class FieldAssignmentStatement:
    name: str
    field: str
    expression: Expression
    location: SourceLocation


@dataclass(frozen=True)
class BlockStatement:
    statements: tuple[Statement, ...]
    location: SourceLocation


@dataclass(frozen=True)
class WhileStatement:
    condition: Expression
    body: BlockStatement
    location: SourceLocation


@dataclass(frozen=True)
class BreakStatement:
    location: SourceLocation


@dataclass(frozen=True)
class ContinueStatement:
    location: SourceLocation


Statement = LetStatement | VarStatement | AssignmentStatement | IndexAssignmentStatement | FieldAssignmentStatement | BlockStatement | WhileStatement | BreakStatement | ContinueStatement | ReturnStatement


@dataclass(frozen=True)
class Parameter:
    name: str
    type_name: str
    location: SourceLocation


@dataclass(frozen=True)
class StructField:
    name: str
    type_name: TypeRef
    location: SourceLocation


@dataclass(frozen=True)
class StructDeclaration:
    name: str
    fields: tuple[StructField, ...]
    location: SourceLocation


@dataclass(frozen=True)
class EnumVariant:
    name: str
    location: SourceLocation


@dataclass(frozen=True)
class EnumDeclaration:
    name: str
    variants: tuple[EnumVariant, ...]
    location: SourceLocation


@dataclass(frozen=True)
class Function:
    name: str
    return_type: str
    body: tuple[Statement, ...]
    location: SourceLocation
    parameters: tuple[Parameter, ...] = ()


@dataclass(frozen=True)
class Program:
    functions: tuple[Function, ...]
    structures: tuple[StructDeclaration, ...] = ()
    enumerations: tuple[EnumDeclaration, ...] = ()
