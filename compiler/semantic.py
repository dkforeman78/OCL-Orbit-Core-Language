from __future__ import annotations

from .nodes import I32_MAX, Program
from .diagnostics import DiagnosticError, SourceLocation


def analyze(program: Program, source: str) -> None:
    names: set[str] = set()
    for function in program.functions:
        if function.name in names:
            raise DiagnosticError("E0201", f"duplicate function '{function.name}'", source, function.location)
        names.add(function.name)
        if function.return_type != "i32":
            raise DiagnosticError("E0200", f"unknown type '{function.return_type}'; OCL 0.1 supports only i32", source, function.location)
        if len(function.body) != 1:
            raise DiagnosticError("E0202", "function body must contain exactly one return statement", source, function.location)
        value = function.body[0].expression
        if value.value > I32_MAX:
            raise DiagnosticError("E0203", "integer literal does not fit in i32", source, value.location)
    if "main" not in names:
        location = program.functions[0].location if program.functions else SourceLocation(0, 1, 1)
        raise DiagnosticError("E0204", "program must define fn main() -> i32", source, location)
