from __future__ import annotations

from .diagnostics import DiagnosticError, InternalCompilerError, SourceLocation
from .parser import FRAMES_PER_BLOCK_LEVEL, MAX_BLOCK_DEPTH, MAX_EXPRESSION_DEPTH
from .stack import reserved
from .nodes import (
    BinaryExpression,
    AssignmentStatement,
    ArrayLiteral,
    BlockStatement,
    BreakStatement,
    BooleanLiteral,
    CallExpression,
    ContinueStatement,
    Expression,
    I32_MAX,
    IdentifierExpression,
    IfExpression,
    EnumVariantExpression,
    MatchExpression,
    FieldAssignmentStatement,
    FieldExpression,
    IndexAssignmentStatement,
    IndexExpression,
    IntegerLiteral,
    LetStatement,
    Program,
    ReturnStatement,
    UnaryExpression,
    StructLiteral,
    VarStatement,
    WhileStatement,
)
from .types import ArrayType, EnumType, ScalarType, StructType

SUPPORTED_TYPES = frozenset(("bool", "i32"))
ARITHMETIC_OPERATORS = frozenset(("+", "-", "*", "/", "%"))
RELATIONAL_OPERATORS = frozenset(("<", "<=", ">", ">="))
EQUALITY_OPERATORS = frozenset(("==", "!="))
LOGICAL_OPERATORS = frozenset(("&&", "||"))
MAX_LOCAL_ARRAY_BYTES = 2_048


def _array_type(type_name: str) -> tuple[str, int] | None:
    if isinstance(type_name, ArrayType):
        return str(type_name.element), type_name.length
    if not (type_name.startswith("[") and type_name.endswith("]") and ";" in type_name):
        return None
    element, length = type_name[1:-1].split(";", 1)
    try:
        return element.strip(), int(length.strip())
    except ValueError:
        return None


def _require_known_type(type_name: str, source: str, location: SourceLocation, *, arrays: bool = False, structures=None, enumerations=None) -> None:
    if type_name in SUPPORTED_TYPES:
        return
    array = _array_type(type_name)
    if arrays and array and array[0] in SUPPORTED_TYPES and 1 <= array[1] <= 256:
        return
    if arrays and array and array[0] not in SUPPORTED_TYPES:
        raise DiagnosticError("E0200", f"unknown array element type '{array[0]}'", source, location)
    if arrays and array and array[1] <= 0:
        raise DiagnosticError("E0219", "array length must be greater than zero", source, location)
    if arrays and array and array[1] > 256:
        raise DiagnosticError("E0219", "Prototype 0.10 arrays may contain at most 256 elements", source, location)
    if structures is not None and str(type_name) in structures:
        return
    if enumerations is not None and str(type_name) in enumerations:
        return
    if isinstance(type_name, StructType):
        raise DiagnosticError("E0200", f"unknown type '{type_name}'", source, location)
    raise DiagnosticError("E0200", f"unknown type '{type_name}'; OCL 0.10 supports scalar, enumeration, and approved local aggregate types", source, location)


def _require_constant_expression(expression: Expression, constants: dict, source: str) -> None:
    allowed = (BooleanLiteral, IntegerLiteral, IdentifierExpression, EnumVariantExpression,
               BinaryExpression, IfExpression, UnaryExpression, MatchExpression)
    pending = [expression]
    while pending:
        node = pending.pop()
        if not isinstance(node, allowed):
            raise DiagnosticError(
                "E0240", f"{type(node).__name__} is not allowed in a compile-time constant",
                source, node.location)
        if isinstance(node, IdentifierExpression) and node.name not in constants:
            raise DiagnosticError("E0206", f"unknown constant '{node.name}'", source, node.location)
        pending.extend(_operands(node))


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
    enumerations = {}
    for enumeration in program.enumerations:
        if enumeration.name in enumerations:
            raise DiagnosticError("E0232", f"duplicate enumeration '{enumeration.name}'", source, enumeration.location)
        if not enumeration.variants:
            raise DiagnosticError("E0233", f"enumeration '{enumeration.name}' must declare at least one variant", source, enumeration.location)
        if len(enumeration.variants) > 256:
            raise DiagnosticError("E0233", f"enumeration '{enumeration.name}' has more than 256 variants", source, enumeration.location)
        names = set()
        for variant in enumeration.variants:
            if variant.name in names:
                raise DiagnosticError("E0233", f"duplicate variant '{variant.name}' in enumeration '{enumeration.name}'", source, variant.location)
            names.add(variant.name)
        enumerations[enumeration.name] = enumeration

    structures = {}
    for structure in program.structures:
        if structure.name in enumerations:
            raise DiagnosticError("E0232", f"type name '{structure.name}' is already declared", source, structure.location)
        if structure.name in structures:
            raise DiagnosticError("E0225", f"duplicate structure '{structure.name}'", source, structure.location)
        if not structure.fields:
            raise DiagnosticError("E0226", f"structure '{structure.name}' must declare at least one field", source, structure.location)
        if len(structure.fields) > 64:
            raise DiagnosticError("E0226", f"structure '{structure.name}' has more than 64 fields", source, structure.location)
        names = set()
        for field in structure.fields:
            _require_known_type(field.type_name, source, field.location)
            if field.name in names:
                raise DiagnosticError("E0226", f"duplicate field '{field.name}' in structure '{structure.name}'", source, field.location)
            names.add(field.name)
        structures[structure.name] = structure

    constants = {}
    if len(program.constants) > 256:
        raise DiagnosticError("E0238", "Prototype 0.10 allows at most 256 top-level constants", source, program.constants[256].location)
    for constant in program.constants:
        if constant.name in constants:
            raise DiagnosticError("E0238", f"duplicate constant '{constant.name}'", source, constant.location)
        _require_known_type(constant.type_name, source, constant.location, enumerations=enumerations)
        constants[constant.name] = constant

    constant_scope = {name: (constant.type_name, False) for name, constant in constants.items()}
    for constant in program.constants:
        _require_constant_expression(constant.initializer, constants, source)
        actual = _analyze_expression(constant.initializer, constant_scope, {}, source, structures, enumerations)
        if actual != constant.type_name:
            raise DiagnosticError("E0214", f"constant '{constant.name}' expects {constant.type_name}, got {actual}", source, constant.location)
    evaluate_constants(program, source)

    functions = {}
    for function in program.functions:
        if function.name in constants:
            raise DiagnosticError("E0238", f"top-level value name '{function.name}' is already declared as a constant", source, function.location)
        if function.name in functions:
            raise DiagnosticError("E0201", f"duplicate function '{function.name}'", source, function.location)
        functions[function.name] = function
        _require_known_type(function.return_type, source, function.location, enumerations=enumerations)
        parameter_names: set[str] = set()
        for parameter in function.parameters:
            _require_known_type(parameter.type_name, source, parameter.location, enumerations=enumerations)
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
        array_bytes = _local_aggregate_bytes(function.body, structures)
        if array_bytes > MAX_LOCAL_ARRAY_BYTES:
            raise DiagnosticError(
                "E0219",
                f"function '{function.name}' declares {array_bytes} bytes of aggregates; Prototype 0.10 allows at most {MAX_LOCAL_ARRAY_BYTES}",
                source,
                function.location,
            )
        parameter_names = {parameter.name for parameter in function.parameters}
        scope = dict(constant_scope)
        scope.update({parameter.name: (parameter.type_name, False) for parameter in function.parameters})
        declared = set(parameter_names)
        if _analyze_statements(function.body, scope, declared, functions, function, source, structures=structures, enumerations=enumerations) != "return":
            raise DiagnosticError("E0202", f"function '{function.name}' can reach the end without returning", source, function.location)


def _local_aggregate_bytes(statements, structures) -> int:
    total = 0
    pending = list(reversed(statements))
    while pending:
        statement = pending.pop()
        if isinstance(statement, (LetStatement, VarStatement)):
            array = _array_type(statement.type_name)
            if array:
                total += array[1] * (4 if array[0] == "i32" else 1)
            elif str(statement.type_name) in structures:
                offset = 0
                maximum_alignment = 1
                for field in structures[str(statement.type_name)].fields:
                    size = 4 if field.type_name == "i32" else 1
                    maximum_alignment = max(maximum_alignment, size)
                    offset = (offset + size - 1) // size * size
                    offset += size
                total += (offset + maximum_alignment - 1) // maximum_alignment * maximum_alignment
        elif isinstance(statement, BlockStatement):
            pending.extend(reversed(statement.statements))
        elif isinstance(statement, WhileStatement):
            pending.extend(reversed(statement.body.statements))
    return total


def _analyze_statements(statements, scope, declared, functions, function, source, loop_depth=0, structures=None, enumerations=None):
    flow = None
    for statement in statements:
        if flow:
            raise DiagnosticError("E0216", f"unreachable statement after {flow}", source, statement.location)
        if isinstance(statement, (LetStatement, VarStatement)):
            _require_known_type(statement.type_name, source, statement.location, arrays=True, structures=structures, enumerations=enumerations)
            if statement.name in declared:
                raise DiagnosticError("E0210", f"name '{statement.name}' is already declared in this function; OCL 0.10 has no shadowing", source, statement.location)
            if _array_type(statement.type_name) and not isinstance(statement.initializer, ArrayLiteral):
                raise DiagnosticError("E0223", "array initializer must be an array literal in OCL 0.7", source, statement.initializer.location)
            if str(statement.type_name) in structures and not isinstance(statement.initializer, StructLiteral):
                raise DiagnosticError("E0231", "structure initializer must be a named-field literal in OCL 0.8", source, statement.initializer.location)
            actual = _analyze_expression(statement.initializer, scope, functions, source, structures, enumerations)
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
            if _array_type(expected):
                raise DiagnosticError("E0223", "whole-array assignment is not supported in OCL 0.7", source, statement.location)
            if str(expected) in structures:
                raise DiagnosticError("E0231", "whole-structure assignment is not supported in OCL 0.8", source, statement.location)
            actual = _analyze_expression(statement.expression, scope, functions, source, structures, enumerations)
            if actual != expected:
                raise DiagnosticError("E0214", f"assignment to '{statement.name}' expects {expected}, got {actual}", source, statement.location)
        elif isinstance(statement, IndexAssignmentStatement):
            if statement.name not in scope:
                raise DiagnosticError("E0206", f"unknown identifier '{statement.name}'", source, statement.location)
            expected, mutable = scope[statement.name]
            array = _array_type(expected)
            if array is None:
                raise DiagnosticError("E0220", f"'{statement.name}' is not an array", source, statement.location)
            if not mutable:
                raise DiagnosticError("E0215", f"cannot assign through immutable binding '{statement.name}'", source, statement.location)
            index_type = _analyze_expression(statement.index, scope, functions, source, structures, enumerations)
            if index_type != "i32":
                raise DiagnosticError("E0221", f"array index must be i32, got {index_type}", source, statement.index.location)
            if isinstance(statement.index, IntegerLiteral) and not 0 <= statement.index.value < array[1]:
                raise DiagnosticError("E0222", f"array index {statement.index.value} is outside length {array[1]}", source, statement.index.location)
            actual = _analyze_expression(statement.expression, scope, functions, source, structures, enumerations)
            if actual != array[0]:
                raise DiagnosticError("E0214", f"array element expects {array[0]}, got {actual}", source, statement.expression.location)
        elif isinstance(statement, FieldAssignmentStatement):
            if statement.name not in scope:
                raise DiagnosticError("E0206", f"unknown identifier '{statement.name}'", source, statement.location)
            expected, mutable = scope[statement.name]
            structure = structures.get(str(expected))
            if structure is None:
                raise DiagnosticError("E0230", f"'{statement.name}' is not a structure", source, statement.location)
            if not mutable:
                raise DiagnosticError("E0215", f"cannot assign through immutable binding '{statement.name}'", source, statement.location)
            fields = {field.name: field for field in structure.fields}
            if statement.field not in fields:
                raise DiagnosticError("E0229", f"structure '{structure.name}' has no field '{statement.field}'", source, statement.location)
            actual = _analyze_expression(statement.expression, scope, functions, source, structures, enumerations)
            required = fields[statement.field].type_name
            if actual != required:
                raise DiagnosticError("E0214", f"field '{statement.field}' expects {required}, got {actual}", source, statement.expression.location)
        elif isinstance(statement, ReturnStatement):
            actual = _analyze_expression(statement.expression, scope, functions, source, structures, enumerations)
            if actual != function.return_type:
                raise DiagnosticError("E0214", f"function '{function.name}' returns {function.return_type}, got {actual}", source, statement.location)
            flow = "return"
        elif isinstance(statement, (BreakStatement, ContinueStatement)):
            if loop_depth == 0:
                raise DiagnosticError("E0218", f"'{type(statement).__name__.removesuffix('Statement').lower()}' is only valid inside while", source, statement.location)
            flow = "break" if isinstance(statement, BreakStatement) else "continue"
        elif isinstance(statement, BlockStatement):
            flow = _analyze_statements(statement.statements, dict(scope), declared, functions, function, source, loop_depth, structures, enumerations)
        elif isinstance(statement, WhileStatement):
            condition = _analyze_expression(statement.condition, scope, functions, source, structures, enumerations)
            if condition != "bool":
                raise DiagnosticError("E0212", f"while condition must be bool, got {condition}", source, statement.condition.location)
            _analyze_statements(statement.body.statements, dict(scope), declared, functions, function, source, loop_depth + 1, structures, enumerations)
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
    if isinstance(expression, ArrayLiteral):
        return expression.elements
    if isinstance(expression, IndexExpression):
        return (expression.base, expression.index)
    if isinstance(expression, StructLiteral):
        return tuple(field.expression for field in expression.fields)
    if isinstance(expression, FieldExpression):
        return (expression.base,)
    if isinstance(expression, MatchExpression):
        return (expression.scrutinee, *(arm.expression for arm in expression.arms))
    return ()


def _analyze_expression(expression: Expression, scope: dict[str, str], functions: dict, source: str, structures=None, enumerations=None) -> str:
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
            if isinstance(node, EnumVariantExpression):
                enumeration = enumerations.get(node.enum_name)
                if enumeration is None:
                    raise DiagnosticError("E0234", f"unknown enumeration '{node.enum_name}'", source, node.location)
                if node.variant not in {variant.name for variant in enumeration.variants}:
                    raise DiagnosticError("E0234", f"enumeration '{node.enum_name}' has no variant '{node.variant}'", source, node.location)
                types[id(node)] = EnumType(node.enum_name)
                continue
            if not isinstance(node, (BinaryExpression, CallExpression, IfExpression, UnaryExpression, ArrayLiteral, IndexExpression, StructLiteral, FieldExpression, MatchExpression)):
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
                if _array_type(left) or _array_type(right):
                    raise DiagnosticError("E0211", f"operator '{node.operator}' does not support arrays in OCL 0.7", source, node.location)
                if str(left) in structures or str(right) in structures:
                    raise DiagnosticError("E0211", f"operator '{node.operator}' does not support structures in OCL 0.8", source, node.location)
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
            if _array_type(then_type):
                raise DiagnosticError("E0223", "array-valued if expressions are not supported in OCL 0.7", source, node.location)
            if str(then_type) in structures:
                raise DiagnosticError("E0231", "structure-valued if expressions are not supported in OCL 0.8", source, node.location)
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
        elif isinstance(node, ArrayLiteral):
            if not node.elements:
                raise DiagnosticError("E0224", "array literal must contain at least one element", source, node.location)
            element_types = [types[id(element)] for element in node.elements]
            if element_types[0] not in SUPPORTED_TYPES or any(item != element_types[0] for item in element_types[1:]):
                raise DiagnosticError("E0214", "array literal elements must have one scalar type", source, node.location)
            types[id(node)] = ArrayType(ScalarType(element_types[0]), len(node.elements))
        elif isinstance(node, IndexExpression):
            base = types[id(node.base)]
            index = types[id(node.index)]
            array = _array_type(base)
            if array is None:
                raise DiagnosticError("E0220", "indexed expression is not an array", source, node.location)
            if not isinstance(node.base, IdentifierExpression):
                raise DiagnosticError("E0223", "only a local array binding may be indexed in OCL 0.7", source, node.location)
            if index != "i32":
                raise DiagnosticError("E0221", f"array index must be i32, got {index}", source, node.index.location)
            if isinstance(node.index, IntegerLiteral) and not 0 <= node.index.value < array[1]:
                raise DiagnosticError("E0222", f"array index {node.index.value} is outside length {array[1]}", source, node.index.location)
            types[id(node)] = array[0]
        elif isinstance(node, StructLiteral):
            structure = structures.get(str(node.type_name))
            if structure is None:
                raise DiagnosticError("E0227", f"unknown structure type '{node.type_name}'", source, node.location)
            declared = {field.name: field for field in structure.fields}
            supplied = set()
            for field in node.fields:
                if field.name in supplied:
                    raise DiagnosticError("E0228", f"duplicate initializer for field '{field.name}'", source, field.location)
                supplied.add(field.name)
                if field.name not in declared:
                    raise DiagnosticError("E0229", f"structure '{structure.name}' has no field '{field.name}'", source, field.location)
                actual = types[id(field.expression)]
                required = declared[field.name].type_name
                if actual != required:
                    raise DiagnosticError("E0214", f"field '{field.name}' expects {required}, got {actual}", source, field.location)
            missing = [field.name for field in structure.fields if field.name not in supplied]
            if missing:
                raise DiagnosticError("E0228", f"missing initializer for field '{missing[0]}'", source, node.location)
            types[id(node)] = StructType(structure.name)
        elif isinstance(node, FieldExpression):
            if not isinstance(node.base, IdentifierExpression):
                raise DiagnosticError("E0231", "only a local structure binding may be accessed in OCL 0.8", source, node.location)
            base = types[id(node.base)]
            structure = structures.get(str(base))
            if structure is None:
                raise DiagnosticError("E0230", "field-access base is not a structure", source, node.location)
            fields = {field.name: field for field in structure.fields}
            if node.field not in fields:
                raise DiagnosticError("E0229", f"structure '{structure.name}' has no field '{node.field}'", source, node.location)
            types[id(node)] = fields[node.field].type_name
        elif isinstance(node, MatchExpression):
            scrutinee_type = types[id(node.scrutinee)]
            enumeration = enumerations.get(str(scrutinee_type))
            if enumeration is None:
                raise DiagnosticError("E0235", f"match requires an enumeration, got {scrutinee_type}", source, node.scrutinee.location)
            declared = {variant.name for variant in enumeration.variants}
            seen = set()
            result_type = None
            for arm in node.arms:
                if arm.enum_name != enumeration.name:
                    raise DiagnosticError("E0236", f"match arm must name enumeration '{enumeration.name}'", source, arm.location)
                if arm.variant not in declared:
                    raise DiagnosticError("E0234", f"enumeration '{enumeration.name}' has no variant '{arm.variant}'", source, arm.location)
                if arm.variant in seen:
                    raise DiagnosticError("E0236", f"duplicate match arm for '{enumeration.name}.{arm.variant}'", source, arm.location)
                seen.add(arm.variant)
                arm_type = types[id(arm.expression)]
                if result_type is None:
                    result_type = arm_type
                elif arm_type != result_type:
                    raise DiagnosticError("E0213", f"match arms must have the same type, got {result_type} and {arm_type}", source, arm.location)
            missing = [variant.name for variant in enumeration.variants if variant.name not in seen]
            if missing:
                raise DiagnosticError("E0237", f"match is not exhaustive; missing '{enumeration.name}.{missing[0]}'", source, node.location)
            types[id(node)] = result_type
        else:
            raise InternalCompilerError(f"unsupported expression node: {type(node).__name__}")
    return types[id(expression)]


def evaluate_constants(program: Program, source: str | None = None) -> dict[str, tuple[object, str]]:
    required_frames = len(program.constants) * 3 + MAX_EXPRESSION_DEPTH
    with reserved(required_frames):
        return _evaluate_constants(program, source)


def _evaluate_constants(program: Program, source: str | None = None) -> dict[str, tuple[object, str]]:
    """Return fully folded constant values after (or during) semantic analysis."""
    declarations = {constant.name: constant for constant in program.constants}
    enumerations = {enumeration.name: enumeration for enumeration in program.enumerations}
    values: dict[str, tuple[object, str]] = {}
    active: list[str] = []

    def fail(code, message, location):
        if source is None:
            raise InternalCompilerError(message)
        raise DiagnosticError(code, message, source, location)

    def wrap(value: int) -> int:
        value &= 0xFFFFFFFF
        return value - 0x100000000 if value >= 0x80000000 else value

    def resolve(name: str, location):
        if name in values:
            return values[name]
        if name in active:
            cycle = " -> ".join((*active[active.index(name):], name))
            fail("E0239", f"constant dependency cycle: {cycle}", location)
        try:
            declaration = declarations[name]
        except KeyError:
            fail("E0206", f"unknown constant '{name}'", location)
        active.append(name)
        value, type_name = evaluate(declaration.initializer)
        active.pop()
        values[name] = (value, declaration.type_name)
        return values[name]

    def evaluate(node):
        if isinstance(node, BooleanLiteral):
            return node.value, ScalarType("bool")
        if isinstance(node, IntegerLiteral):
            return node.value, ScalarType("i32")
        if isinstance(node, IdentifierExpression):
            return resolve(node.name, node.location)
        if isinstance(node, EnumVariantExpression):
            enumeration = enumerations[node.enum_name]
            ordinal = next(index for index, variant in enumerate(enumeration.variants) if variant.name == node.variant)
            return ordinal, EnumType(node.enum_name)
        if isinstance(node, UnaryExpression):
            operand, type_name = evaluate(node.operand)
            return ((not operand), ScalarType("bool")) if node.operator == "!" else (wrap(-operand), ScalarType("i32"))
        if isinstance(node, IfExpression):
            condition, _ = evaluate(node.condition)
            return evaluate(node.then_expression if condition else node.else_expression)
        if isinstance(node, MatchExpression):
            ordinal, enum_type = evaluate(node.scrutinee)
            enumeration = enumerations[str(enum_type)]
            selected = enumeration.variants[ordinal].name
            return evaluate(next(arm.expression for arm in node.arms if arm.variant == selected))
        if isinstance(node, BinaryExpression):
            left, left_type = evaluate(node.left)
            if node.operator == "&&" and not left:
                return False, ScalarType("bool")
            if node.operator == "||" and left:
                return True, ScalarType("bool")
            right, right_type = evaluate(node.right)
            if node.operator == "+": return wrap(left + right), ScalarType("i32")
            if node.operator == "-": return wrap(left - right), ScalarType("i32")
            if node.operator == "*": return wrap(left * right), ScalarType("i32")
            if node.operator in ("/", "%"):
                if right == 0 or (left == -2147483648 and right == -1):
                    fail("E0217", "invalid division in compile-time constant", node.location)
                quotient = (abs(left) // abs(right)) * (-1 if (left < 0) != (right < 0) else 1)
                return (quotient if node.operator == "/" else left - quotient * right), ScalarType("i32")
            if node.operator == "<": return left < right, ScalarType("bool")
            if node.operator == "<=": return left <= right, ScalarType("bool")
            if node.operator == ">": return left > right, ScalarType("bool")
            if node.operator == ">=": return left >= right, ScalarType("bool")
            if node.operator == "==": return left == right, ScalarType("bool")
            if node.operator == "!=": return left != right, ScalarType("bool")
            if node.operator == "&&": return bool(right), ScalarType("bool")
            if node.operator == "||": return bool(right), ScalarType("bool")
        fail("E0240", f"unsupported compile-time expression {type(node).__name__}", node.location)

    for constant in program.constants:
        resolve(constant.name, constant.location)
    return values
