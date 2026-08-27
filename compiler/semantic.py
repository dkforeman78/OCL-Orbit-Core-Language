from __future__ import annotations

from .nodes import BinaryExpression, CallExpression, Expression, I32_MAX, IdentifierExpression, IntegerLiteral, LetStatement, Program, ReturnStatement
from .diagnostics import DiagnosticError, InternalCompilerError, SourceLocation


def analyze(program: Program, source: str) -> None:
    functions = {}
    for function in program.functions:
        if function.name in functions:
            raise DiagnosticError("E0201", f"duplicate function '{function.name}'", source, function.location)
        functions[function.name] = function
        if function.return_type != "i32":
            raise DiagnosticError("E0200", f"unknown type '{function.return_type}'; OCL 0.3 supports only i32", source, function.location)
        parameter_names: set[str] = set()
        for parameter in function.parameters:
            if parameter.type_name != "i32":
                raise DiagnosticError("E0200", f"unknown type '{parameter.type_name}'; OCL 0.3 supports only i32", source, parameter.location)
            if parameter.name in parameter_names:
                raise DiagnosticError("E0205", f"duplicate parameter '{parameter.name}'", source, parameter.location)
            parameter_names.add(parameter.name)

    if "main" not in functions:
        location = program.functions[0].location if program.functions else SourceLocation(0, 1, 1)
        raise DiagnosticError("E0204", "program must define fn main() -> i32", source, location)
    if functions["main"].parameters:
        raise DiagnosticError("E0209", "main function must not declare parameters", source, functions["main"].location)

    for function in program.functions:
        returns = [statement for statement in function.body if isinstance(statement, ReturnStatement)]
        if len(returns) != 1 or not function.body or function.body[-1] is not returns[0]:
            raise DiagnosticError("E0202", "function body must end with exactly one return statement", source, function.location)
        parameter_names = {parameter.name for parameter in function.parameters}
        scope = set(parameter_names)
        for statement in function.body:
            if isinstance(statement, LetStatement):
                if statement.type_name != "i32":
                    raise DiagnosticError("E0200", f"unknown type '{statement.type_name}'; OCL 0.3 supports only i32", source, statement.location)
                # Shadowing a parameter and redeclaring a local are separate
                # rules, so they get separate messages: "already declared" would
                # send the reader hunting for a `let` that does not exist.
                if statement.name in parameter_names:
                    raise DiagnosticError("E0210", f"local '{statement.name}' conflicts with parameter '{statement.name}'; OCL 0.3 has no shadowing", source, statement.location)
                if statement.name in scope:
                    raise DiagnosticError("E0210", f"local name '{statement.name}' is already declared in this function", source, statement.location)
                _analyze_expression(statement.initializer, scope, functions, source)
                scope.add(statement.name)
            elif isinstance(statement, ReturnStatement):
                _analyze_expression(statement.expression, scope, functions, source)
            else:
                raise InternalCompilerError(f"unsupported statement node: {type(statement).__name__}")


def _analyze_expression(expression: Expression, scope: set[str], functions: dict, source: str) -> None:
    """Validate one expression tree.

    Walks with an explicit stack rather than recursing: expression depth is
    bounded only by the source, and a deep but valid program must produce a
    binary, not a RecursionError. Operands are pushed reversed so errors are
    still reported leftmost-first, as a reader expects.
    """
    pending: list[Expression] = [expression]
    while pending:
        node = pending.pop()
        if isinstance(node, IntegerLiteral):
            if node.value > I32_MAX:
                raise DiagnosticError("E0203", "integer literal does not fit in i32", source, node.location)
        elif isinstance(node, IdentifierExpression):
            if node.name not in scope:
                raise DiagnosticError("E0206", f"unknown identifier '{node.name}'", source, node.location)
        elif isinstance(node, BinaryExpression):
            pending.append(node.right)
            pending.append(node.left)
        elif isinstance(node, CallExpression):
            callee = functions.get(node.callee)
            if callee is None:
                raise DiagnosticError("E0207", f"unknown function '{node.callee}'", source, node.location)
            if len(node.arguments) != len(callee.parameters):
                raise DiagnosticError(
                    "E0208",
                    f"function '{node.callee}' expects {len(callee.parameters)} argument(s), got {len(node.arguments)}",
                    source,
                    node.location,
                )
            pending.extend(reversed(node.arguments))
        else:
            raise InternalCompilerError(f"unsupported expression node: {type(node).__name__}")
