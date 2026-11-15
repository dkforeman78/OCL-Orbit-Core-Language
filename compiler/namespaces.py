"""Source activation and semantic binding for the opt-in 0.17 module model.

Binding operates on AST nodes, never rewritten source. The resulting names are
injective, owner-qualified identities consumed by the existing type checker and
backend. Expression rewriting is iterative, including long binary chains.
"""
from dataclasses import dataclass, fields, replace

from . import nodes as n
from .diagnostics import DiagnosticError
from .lexer import TokenKind
from .types import ArrayType, EnumType, StructType, INTEGER_TYPES


class ReferenceName(str):
    def __new__(cls, text, location):
        value = super().__new__(cls, text)
        value.member_location = location
        return value


class QualifiedName(str):
    def __new__(cls, qualifier, member, qualifier_location, member_location):
        value = super().__new__(cls, f'{qualifier}::{member}')
        value.qualifier_location = qualifier_location
        value.member_location = member_location
        return value


def prepare(tokens, source):
    """Consume one leading contextual header; leave legacy identifiers alone."""
    enabled = False
    if tokens[0].kind is TokenKind.IDENTIFIER and tokens[0].lexeme == 'module':
        if (len(tokens) < 4 or tokens[1].lexeme != 'namespaces'
                or tokens[2].kind is not TokenKind.SEMICOLON):
            raise DiagnosticError('E0310', "expected leading 'module namespaces;'", source, tokens[0].location)
        enabled = True
        tokens = tokens[3:]
    for i, token in enumerate(tokens[:-1]):
        if (token.kind is TokenKind.IDENTIFIER and token.lexeme == 'module'
                and tokens[i + 1].kind is TokenKind.IDENTIFIER):
            raise DiagnosticError('E0310', 'module header must occur once, before imports and declarations', source, token.location)
    if enabled:
        tokens = [replace(t, kind=TokenKind.PUB) if t.kind is TokenKind.IDENTIFIER
                  and t.lexeme == 'pub' else t for t in tokens]
    return tokens, enabled


@dataclass
class Unit:
    program: n.Program
    source: str
    imports: dict[str, str]


class Binder:
    def __init__(self, units, entry):
        self.units = units
        self.entry = entry
        self.tables = {}
        for owner, unit in units.items():
            table = {'value': {}, 'type': {}}
            for category, declarations in (
                    ('value', unit.program.functions + unit.program.constants),
                    ('type', unit.program.structures + unit.program.enumerations)):
                for declaration in sorted(declarations, key=lambda item: item.location.offset):
                    if declaration.name in table[category]:
                        first = table[category][declaration.name]
                        self.error(owner, 'E0316', f"duplicate {category} '{declaration.name}'; first declared at "
                                   f'{first.location.line}:{first.location.column}', declaration.location)
                    table[category][declaration.name] = declaration
            self.tables[owner] = table
        # Index direct imports once. Resolution never rescans a transitive graph
        # or depends on declaration/import order to choose a candidate.
        self.imported = {}
        for owner, unit in units.items():
            index = {}
            for qualifier, target in sorted(unit.imports.items()):
                for category, table in self.tables[target].items():
                    for name, declaration in table.items():
                        candidates, private = index.setdefault((category, name), ({}, []))
                        if declaration.public:
                            candidates.setdefault((target, name), (qualifier, target, declaration))
                        else:
                            private.append(qualifier)
            self.imported[owner] = index

    def error(self, owner, code, message, location):
        raise DiagnosticError(code, message, self.units[owner].source, location)

    def resolve(self, owner, name, category, location, optional=False):
        # TypeRef retains the original name object (and qualified locations).
        name = getattr(name, 'name', name)
        location = getattr(name, 'member_location', location)
        imports = self.units[owner].imports
        if '::' in name:
            qualifier, member = name.split('::', 1)
            if qualifier not in imports:
                self.error(owner, 'E0312', f"unknown module qualifier '{qualifier}'; add a direct import",
                           getattr(name, 'qualifier_location', location))
            target = imports[qualifier]
            declaration = self.tables[target][category].get(member)
            if declaration is None:
                if optional:
                    return None
                self.error(owner, 'E0313', f"module '{qualifier}' has no {category} '{member}'", location)
            if not declaration.public:
                self.error(owner, 'E0314', f"'{name}' is private to '{target}'; use a public API", location)
            return target, declaration
        own = self.tables[owner][category].get(name)
        if own is not None:
            return owner, own
        candidates, private = self.imported[owner].get((category, name), ({}, []))
        if len(candidates) > 1:
            names = ', '.join(f'{q}::{name}' for q, _, _ in sorted(candidates.values(), key=lambda item: item[0]))
            self.error(owner, 'E0315', f"ambiguous name '{name}'; qualify one of: {names}", location)
        if candidates:
            _, target, declaration = next(iter(candidates.values()))
            return target, declaration
        if private:
            self.error(owner, 'E0314', f"'{name}' is private in {', '.join(private)}; use a public API", location)
        if optional:
            return None
        self.error(owner, 'E0313', f"unknown {category} '{name}'; declare it or import its defining module", location)

    def symbol(self, owner, declaration):
        if owner == self.entry and isinstance(declaration, n.Function) and declaration.name == 'main':
            return 'main'
        return f'ocl.ns.{owner.encode("utf-8").hex()}.{declaration.name}'

    def typed(self, owner, type_name, location, exposed=False):
        location = getattr(getattr(type_name, 'name', type_name), 'member_location', location)
        if str(type_name) in INTEGER_TYPES or type_name == 'bool':
            return type_name
        if isinstance(type_name, ArrayType):
            return type_name  # Arrays still permit scalar elements only.
        target, declaration = self.resolve(owner, type_name, 'type', location)
        if exposed and not declaration.public:
            self.error(owner, 'E0318', f"public declaration exposes private type '{declaration.name}'; make the type public or the declaration private", location)
        cls = EnumType if isinstance(declaration, n.EnumDeclaration) else StructType
        return cls(self.symbol(target, declaration))

    def value(self, owner, name, location, expected):
        target, declaration = self.resolve(owner, name, 'value', location)
        if not isinstance(declaration, expected):
            self.error(owner, 'E0317', f"'{name}' is a {type(declaration).__name__}, expected {expected.__name__}",
                       getattr(name, 'member_location', location))
        return self.symbol(target, declaration)

    def expression(self, owner, root, scope):
        results = {}
        stack = [(root, False)]
        while stack:
            node, done = stack.pop()
            # Dotted enum references are parsed without a global enum-name set.
            if isinstance(node, n.FieldExpression) and isinstance(node.base, n.IdentifierExpression) and node.base.name not in scope:
                found = self.resolve(owner, node.base.name, 'type', node.base.location, optional=True)
                if found is not None:
                    target, declaration = found
                    if not isinstance(declaration, n.EnumDeclaration):
                        self.error(owner, 'E0317', f"'{node.base.name}' is not an enum", node.location)
                    results[id(node)] = n.EnumVariantExpression(self.symbol(target, declaration), node.field, node.location)
                    continue
            children = []
            for field in fields(node):
                if field.name == 'location':
                    continue
                value = getattr(node, field.name)
                if isinstance(value, tuple):
                    children.extend(v for v in value if hasattr(v, 'location'))
                elif hasattr(value, 'location'):
                    children.append(value)
            if not done:
                stack.append((node, True))
                stack.extend((child, False) for child in reversed(children))
                continue
            changes = {}
            for field in fields(node):
                if field.name == 'location':
                    continue
                value = getattr(node, field.name)
                if isinstance(value, tuple):
                    changes[field.name] = tuple(results[id(v)] if hasattr(v, 'location') else v for v in value)
                elif hasattr(value, 'location'):
                    changes[field.name] = results[id(value)]
            if isinstance(node, n.IdentifierExpression) and node.name not in scope:
                changes['name'] = self.value(owner, node.name, node.location, n.ConstDeclaration)
            elif isinstance(node, n.CallExpression):
                changes['callee'] = self.value(owner, node.callee, node.location, n.Function)
            elif isinstance(node, (n.StructLiteral, n.CastExpression)):
                changes['type_name'] = self.typed(owner, node.type_name, node.location)
            elif isinstance(node, (n.EnumVariantExpression, n.MatchArm)):
                target, declaration = self.resolve(owner, node.enum_name, 'type', node.location)
                if not isinstance(declaration, n.EnumDeclaration):
                    self.error(owner, 'E0317', f"'{node.enum_name}' is not an enum", node.location)
                changes['enum_name'] = self.symbol(target, declaration)
            results[id(node)] = replace(node, **changes)
        return results[id(root)]

    def statements(self, owner, statements, scope):
        # Explicit frames avoid consuming the caller's Python recursion budget.
        frames = [(iter(statements), set(scope), [], None)]
        while frames:
            iterator, local, output, parent = frames[-1]
            statement = next(iterator, None)
            if statement is None:
                frames.pop()
                result = tuple(output)
                if not frames:
                    return result
                if isinstance(parent, n.WhileStatement):
                    parent = replace(parent, body=replace(parent.body, statements=result))
                else:
                    parent = replace(parent, statements=result)
                frames[-1][2].append(parent)
                continue
            if isinstance(statement, n.BlockStatement):
                frames.append((iter(statement.statements), set(local), [], statement))
                continue
            if isinstance(statement, n.WhileStatement):
                statement = replace(statement, condition=self.expression(owner, statement.condition, local))
                frames.append((iter(statement.body.statements), set(local), [], statement))
                continue
            changes = {}
            for field in ('expression', 'initializer', 'index'):
                if hasattr(statement, field):
                    changes[field] = self.expression(owner, getattr(statement, field), local)
            if isinstance(statement, (n.LetStatement, n.VarStatement)):
                changes['type_name'] = self.typed(owner, statement.type_name, statement.location)
                local.add(statement.name)
            elif isinstance(statement, (n.AssignmentStatement, n.IndexAssignmentStatement, n.FieldAssignmentStatement)) and statement.name not in local:
                changes['name'] = self.value(owner, statement.name, statement.location, n.ConstDeclaration)
            output.append(replace(statement, **changes))

    def bind(self):
        functions, structures, enumerations, constants = [], [], [], []
        for owner, unit in self.units.items():
            for declaration in unit.program.enumerations:
                enumerations.append(replace(declaration, name=self.symbol(owner, declaration)))
            for declaration in unit.program.structures:
                structures.append(replace(declaration, name=self.symbol(owner, declaration)))
            for declaration in unit.program.constants:
                constants.append(replace(declaration, name=self.symbol(owner, declaration),
                    type_name=self.typed(owner, declaration.type_name, declaration.location, declaration.public),
                    initializer=self.expression(owner, declaration.initializer, set())))
            for function in unit.program.functions:
                parameters = tuple(replace(p, type_name=self.typed(owner, p.type_name, p.location, function.public)) for p in function.parameters)
                functions.append(replace(function, name=self.symbol(owner, function), parameters=parameters,
                    return_type=self.typed(owner, function.return_type, function.location, function.public),
                    body=self.statements(owner, function.body, {p.name for p in parameters})))
        return n.Program(tuple(functions), tuple(structures), tuple(enumerations), tuple(constants))


def bind(units, entry):
    """Semantic namespace/access phase, followed by ordinary type analysis."""
    return Binder(units, entry).bind()
