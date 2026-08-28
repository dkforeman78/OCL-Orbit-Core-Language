from __future__ import annotations

from .diagnostics import DiagnosticError, InternalCompilerError, SourceLocation
from .parser import FRAMES_PER_BLOCK_LEVEL, MAX_BLOCK_DEPTH
from .stack import reserved
from .nodes import (
    BinaryExpression,
    AssignmentStatement,
    BlockStatement,
    BreakStatement,
    BooleanLiteral,
    CallExpression,
    ContinueStatement,
    Expression,
    I32_MAX,
    IdentifierExpression,
    IfExpression,
    IntegerLiteral,
    LetStatement,
    Program,
    ReturnStatement,
    UnaryExpression,
    VarStatement,
    WhileStatement,
)

SUPPORTED_TYPES = frozenset(("bool", "i32"))
ARITHMETIC_OPERATORS = frozenset(("+", "-", "*", "/", "%"))
RELATIONAL_OPERATORS = frozenset(("<", "<=", ">", ">="))
EQUALITY_OPERATORS = frozenset(("==", "!="))
LOGICAL_OPERATORS = frozenset(("&&", "||"))


def _require_known_type(type_name: str, source: str, location: SourceLocation) -> None:
    if type_name not in SUPPORTED_TYPES:
        raise DiagnosticError("E0200", f"unknown type '{type_name}'; OCL 0.6 supports i32 and bool", source, location)


def analyze(program: Program, source: str) -> None:
    """Validate a parsed program.

    `_analyze_statements` recurses once per block level, and the parser admits
    `MAX_BLOCK_DEPTH` of them. The parser's own reservation is released when
    parsing ends, so this walk reserves the stack it needs itself; otherwise a
    deeply blocked but entirely valid program becomes a `RecursionError` for any
    caller that is not already near the top of the stack.
    """
    with reserved(MAX_BLOCK_DEPTH * FRAMES_PER_BLOCK_LEVEL):
        _analyze(program, source)


def _analyze(program: Program, source: str) -> None:
    functions = {}
    for function in program.functions:
        if function.name in functions:
            raise DiagnosticError("E0201", f"duplicate function '{function.name}'", source, function.location)
        functions[function.name] = function
        _require_known_type(function.return_type, source, function.location)
        parameter_names: set[str] = set()
        for parameter in function.parameters:
            _require_known_type(parameter.type_name, source, parameter.location)
            if parameter.name in parameter_names:
                raise DiagnosticError("E0205", f"duplicate parameter '{parameter.name}'", source, parameter.location)
            parameter_names.add(parameter.name)

    if "main" not in functions:
        location = program.functions[0].location if program.functions else SourceLocation(0, 1, 1)
        raise DiagnosticError("E0204", "program must define fn main() -> i32", source, location)
    if functions["main"].parameters:
        raise DiagnosticError("E0209", "main function must not declare parameters", source, functions["main"].location)
    if functions["main"].return_type != "i32":
        raise DiagnosticError("E0214", "main function must return i32", source, functions["main"].location)

    for function in program.functions:
        parameter_names = {parameter.name for parameter in function.parameters}
        scope = {parameter.name: (parameter.type_name, False) for parameter in function.parameters}
        declared = set(parameter_names)
        if _analyze_statements(function.body, scope, declared, functions, function, source) != "return":
            raise DiagnosticError("E0202", f"function '{function.name}' can reach the end without returning", source, function.location)


def _analyze_statements(statements, scope, declared, functions, function, source, loop_depth=0):
    flow = None
    for statement in statements:
        if flow:
            raise DiagnosticError("E0216", f"unreachable statement after {flow}", source, statement.location)
        if isinstance(statement, (LetStatement, VarStatement)):
            _require_known_type(statement.type_name, source, statement.location)
            if statement.name in declared:
                raise DiagnosticError("E0210", f"name '{statement.name}' is already declared in this function; OCL 0.6 has no shadowing", source, statement.location)
            actual = _analyze_expression(statement.initializer, scope, functions, source)
            if actual != statement.type_name:
                raise DiagnosticError("E0214", f"local '{statement.name}' expects {statement.type_name}, got {actual}", source, statement.location)
            scope[statement.name] = (statement.type_name, isinstance(statement, VarStatement))
            declared.add(statement.name)
        elif isinstance(statement, AssignmentStatement):
            if statement.name not in scope:
                raise DiagnosticError("E0206", f"unknown identifier '{statement.name}'", source, statement.location)
            expected, mutable = scope[statement.name]
            if not mutable:
                raise DiagnosticError("E0215", f"cannot assign to immutable binding '{statement.name}'", source, statement.location)
            actual = _analyze_expression(statement.expression, scope, functions, source)
            if actual != expected:
                raise DiagnosticError("E0214", f"assignment to '{statement.name}' expects {expected}, got {actual}", source, statement.location)
        elif isinstance(statement, ReturnStatement):
            actual = _analyze_expression(statement.expression, scope, functions, source)
            if actual != function.return_type:
                raise DiagnosticError("E0214", f"function '{function.name}' returns {function.return_type}, got {actual}", source, statement.location)
            flow = "return"
        elif isinstance(statement, (BreakStatement, ContinueStatement)):
            if loop_depth == 0:
                raise DiagnosticError("E0218", f"'{type(statement).__name__.removesuffix('Statement').lower()}' is only valid inside while", source, statement.location)
            flow = "break" if isinstance(statement, BreakStatement) else "continue"
        elif isinstance(statement, BlockStatement):
            flow = _analyze_statements(statement.statements, dict(scope), declared, functions, function, source, loop_depth)
        elif isinstance(statement, WhileStatement):
            condition = _analyze_expression(statement.condition, scope, functions, source)
            if condition != "bool":
                raise DiagnosticError("E0212", f"while condition must be bool, got {condition}", source, statement.condition.location)
            _analyze_statements(statement.body.statements, dict(scope), declared, functions, function, source, loop_depth + 1)
        else:
            raise InternalCompilerError(f"unsupported statement node: {type(statement).__name__}")
    return flow


def _operands(expression: Expression) -> tuple[Expression, ...]:
    if isinstance(expression, BinaryExpression):
        return (expression.left, expression.right)
    if isinstance(expression, CallExpression):
        return expression.arguments
    if isinstance(expression, IfExpression):
        return (expression.condition, expression.then_expression, expression.else_expression)
    if isinstance(expression, UnaryExpression):
        return (expression.operand,)
    return ()


def _analyze_expression(expression: Expression, scope: dict[str, str], functions: dict, source: str) -> str:
    """Type-check one expression without recursing over its AST."""
    types: dict[int, str] = {}
    pending: list[tuple[Expression, bool]] = [(expression, False)]
    while pending:
        node, operands_ready = pending.pop()
        if not operands_ready:
            if isinstance(node, BooleanLiteral):
                types[id(node)] = "bool"
                continue
            if isinstance(node, IntegerLiteral):
                if node.value > I32_MAX:
                    raise DiagnosticError("E0203", "integer literal does not fit in i32", source, node.location)
                types[id(node)] = "i32"
                continue
            if isinstance(node, IdentifierExpression):
                try:
                    types[id(node)] = scope[node.name][0]
                except KeyError as error:
                    raise DiagnosticError("E0206", f"unknown identifier '{node.name}'", source, node.location) from error
                continue
            if not isinstance(node, (BinaryExpression, CallExpression, IfExpression, UnaryExpression)):
                raise InternalCompilerError(f"unsupported expression node: {type(node).__name__}")
            pending.append((node, True))
            pending.extend((operand, False) for operand in reversed(_operands(node)))
            continue

        if isinstance(node, BinaryExpression):
            left = types[id(node.left)]
            right = types[id(node.right)]
            if node.operator in ARITHMETIC_OPERATORS:
                if left != "i32" or right != "i32":
                    raise DiagnosticError("E0211", f"operator '{node.operator}' requires i32 operands, got {left} and {right}", source, node.location)
                if node.operator in ("/", "%") and isinstance(node.right, IntegerLiteral) and node.right.value == 0:
                    raise DiagnosticError("E0217", "division by zero", source, node.right.location)
                types[id(node)] = "i32"
            elif node.operator in RELATIONAL_OPERATORS:
                if left != "i32" or right != "i32":
                    raise DiagnosticError("E0211", f"operator '{node.operator}' requires i32 operands, got {left} and {right}", source, node.location)
                types[id(node)] = "bool"
            elif node.operator in EQUALITY_OPERATORS:
                if left != right:
                    raise DiagnosticError("E0211", f"operator '{node.operator}' requires matching operand types, got {left} and {right}", source, node.location)
                types[id(node)] = "bool"
            elif node.operator in LOGICAL_OPERATORS:
                if left != "bool" or right != "bool":
                    raise DiagnosticError("E0211", f"operator '{node.operator}' requires bool operands, got {left} and {right}", source, node.location)
                types[id(node)] = "bool"
            else:
                raise InternalCompilerError(f"unsupported binary operator: {node.operator}")
        elif isinstance(node, CallExpression):
            callee = functions.get(node.callee)
            if callee is None:
                raise DiagnosticError("E0207", f"unknown function '{node.callee}'", source, node.location)
            if len(node.arguments) != len(callee.parameters):
                raise DiagnosticError("E0208", f"function '{node.callee}' expects {len(callee.parameters)} argument(s), got {len(node.arguments)}", source, node.location)
            for index, (argument, parameter) in enumerate(zip(node.arguments, callee.parameters), start=1):
                actual = types[id(argument)]
                if actual != parameter.type_name:
                    raise DiagnosticError("E0214", f"argument {index} to '{node.callee}' expects {parameter.type_name}, got {actual}", source, argument.location)
            types[id(node)] = callee.return_type
        elif isinstance(node, IfExpression):
            condition = types[id(node.condition)]
            then_type = types[id(node.then_expression)]
            else_type = types[id(node.else_expression)]
            if condition != "bool":
                raise DiagnosticError("E0212", f"if condition must be bool, got {condition}", source, node.condition.location)
            if then_type != else_type:
                raise DiagnosticError("E0213", f"if branches must have the same type, got {then_type} and {else_type}", source, node.location)
            types[id(node)] = then_type
        elif isinstance(node, UnaryExpression):
            operand = types[id(node.operand)]
            if node.operator == "!" and operand == "bool":
                types[id(node)] = "bool"
            elif node.operator == "-" and operand == "i32":
                types[id(node)] = "i32"
            else:
                requirement = "bool" if node.operator == "!" else "i32"
                raise DiagnosticError("E0211", f"operator '{node.operator}' requires a {requirement} operand, got {operand}", source, node.location)
        else:
            raise InternalCompilerError(f"unsupported expression node: {type(node).__name__}")
    return types[id(expression)]
