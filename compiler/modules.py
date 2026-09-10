"""Root-relative module loading with iterative graph traversal and owned locations."""
from dataclasses import dataclass, replace
from pathlib import Path

from .diagnostics import DiagnosticError, SourceDocument
from .lexer import Token, TokenKind, lex
from .nodes import Program
from .parser import parse

MAX_MODULES = 256


@dataclass
class Module:
    path: Path
    source: str
    tokens: list[Token]
    imports: list[Token]


def _load(path: Path) -> Module:
    source = path.read_text(encoding='utf-8-sig')
    document = SourceDocument(str(path), source)
    try:
        tokens = lex(source)
    except DiagnosticError as error:
        error.filename = str(path)
        raise
    tokens = [replace(token, location=replace(token.location, document=document)) for token in tokens]
    imports = []
    index = 0
    while tokens[index].kind is TokenKind.IMPORT:
        opening = tokens[index]
        if index + 2 >= len(tokens) or tokens[index+1].kind is not TokenKind.IDENTIFIER or tokens[index+2].kind is not TokenKind.SEMICOLON:
            raise DiagnosticError('E0300', "expected 'import module_name;'", source, opening.location)
        imports.append(tokens[index+1])
        index += 3
    for token in tokens[index:]:
        if token.kind is TokenKind.IMPORT:
            raise DiagnosticError('E0300', 'imports must precede all declarations at the top level', source, token.location)
    return Module(path, source, tokens[index:], imports)


def load_program(entry: Path) -> tuple[Program, str]:
    try:
        entry = entry.resolve()
    except RuntimeError as error:
        raise ValueError(f'cannot resolve entry source: {error}') from None
    root = entry.parent
    first = _load(entry)
    modules = {entry: first}
    active = {entry}
    finished = set()
    # Each frame holds a module, its next edge, and its direct dependency set.
    stack = [(first, 0, set())]
    while stack:
        module, index, seen = stack[-1]
        if index == len(module.imports):
            active.remove(module.path)
            finished.add(module.path)
            stack.pop()
            continue
        reference = module.imports[index]
        stack[-1] = (module, index + 1, seen)
        attempted = root / (reference.lexeme + '.ocl')
        try:
            target = attempted.resolve()
        except (OSError, RuntimeError) as error:
            raise DiagnosticError('E0301', f"cannot resolve module '{attempted}': {error}", module.source, reference.location) from None
        if target.parent != root:
            raise DiagnosticError('E0305', f"module '{attempted}' resolves outside entry directory", module.source, reference.location)
        if target in seen:
            raise DiagnosticError('E0302', f"duplicate import '{reference.lexeme}'", module.source, reference.location)
        seen.add(target)
        if target in active:
            chain = ' -> '.join([frame[0].path.name for frame in stack] + [target.name])
            raise DiagnosticError('E0303', f'import cycle: {chain}', module.source, reference.location)
        if target in finished:
            continue
        if len(modules) >= MAX_MODULES:
            raise DiagnosticError('E0304', f'compilation may contain at most {MAX_MODULES} source files', module.source, reference.location)
        try:
            child = _load(target)
        except (OSError, UnicodeError) as error:
            raise DiagnosticError('E0301', f"cannot read module '{attempted}': {error}", module.source, reference.location) from None
        modules[target] = child
        active.add(target)
        stack.append((child, 0, set()))

    enum_names = {module.tokens[i+1].lexeme for module in modules.values()
                  for i, token in enumerate(module.tokens[:-1])
                  if token.kind is TokenKind.ENUM and module.tokens[i+1].kind is TokenKind.IDENTIFIER}
    programs = []
    for module in modules.values():
        program = parse(module.tokens, module.source, enum_names=enum_names)
        if module.path != entry:
            for function in program.functions:
                if function.name == 'main':
                    raise DiagnosticError('E0306', 'only the entry source may define main', module.source, function.location)
        programs.append(program)
    if not any(function.name == 'main' for function in programs[0].functions):
        raise DiagnosticError('E0204', 'program must define fn main() -> i32 in the entry source', first.source, first.tokens[-1].location)
    return Program(*(tuple(item for program in programs for item in getattr(program, attribute))
                     for attribute in ('functions', 'structures', 'enumerations', 'constants'))), first.source
