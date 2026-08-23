from __future__ import annotations

from .nodes import BinaryExpression, CallExpression, Expression, I32_MAX, IdentifierExpression, IntegerLiteral, Program
from .diagnostics import DiagnosticError, SourceLocation


def analyze(program: Program, source: str) -> None:
    functions = {}
    for function in program.functions:
        if function.name in functions:
            raise DiagnosticError("E0201", f"duplicate function '{function.name}'", source, function.location)
        functions[function.name] = function
        if function.return_type != "i32":
            raise DiagnosticError("E0200", f"unknown type '{function.return_type}'; OCL 0.2 supports only i32", source, function.location)
        parameter_names: set[str] = set()
        for parameter in function.parameters:
            if parameter.type_name != "i32":
                raise DiagnosticError("E0200", f"unknown type '{parameter.type_name}'; OCL 0.2 supports only i32", source, parameter.location)
            if parameter.name in parameter_names:
                raise DiagnosticError("E0205", f"duplicate parameter '{parameter.name}'", source, parameter.location)
            parameter_names.add(parameter.name)

    if "main" not in functions:
        location = program.functions[0].location if program.functions else SourceLocation(0, 1, 1)
        raise DiagnosticError("E0204", "program must define fn main() -> i32", source, location)
    if functions["main"].parameters:
        raise DiagnosticError("E0209", "main function must not declare parameters", source, functions["main"].location)

    for function in program.functions:
        if len(function.body) != 1:
            raise DiagnosticError("E0202", "function body must contain exactly one return statement", source, function.location)
        scope = {parameter.name for parameter in function.parameters}
        _analyze_expression(function.body[0].expression, scope, functions, source)


def _analyze_expression(expression: Expression, scope: set[str], functions: dict, source: str) -> None:
    if isinstance(expression, IntegerLiteral):
        if expression.value > I32_MAX:
            raise DiagnosticError("E0203", "integer literal does not fit in i32", source, expression.location)
        return
    if isinstance(expression, IdentifierExpression):
        if expression.name not in scope:
            raise DiagnosticError("E0206", f"unknown identifier '{expression.name}'", source, expression.location)
        return
    if isinstance(expression, BinaryExpression):
        _analyze_expression(expression.left, scope, functions, source)
        _analyze_expression(expression.right, scope, functions, source)
        return
    if isinstance(expression, CallExpression):
        callee = functions.get(expression.callee)
        if callee is None:
            raise DiagnosticError("E0207", f"unknown function '{expression.callee}'", source, expression.location)
        if len(expression.arguments) != len(callee.parameters):
            raise DiagnosticError(
                "E0208",
                f"function '{expression.callee}' expects {len(callee.parameters)} argument(s), got {len(expression.arguments)}",
                source,
                expression.location,
            )
        for argument in expression.arguments:
            _analyze_expression(argument, scope, functions, source)
        return
    raise TypeError(f"unsupported expression node: {type(expression).__name__}")
