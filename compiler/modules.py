"""Root-relative module loading with iterative graph traversal and owned locations."""
from dataclasses import dataclass, replace
from pathlib import Path

from .diagnostics import DiagnosticError, SourceDocument
from .lexer import Token, TokenKind, lex
from .nodes import Program
from .parser import parse

MAX_MODULES = 256


def _existing_identity(target: Path, known_paths) -> Path:
    """Coalesce case aliases without coalescing distinct hard-link entries.

    POSIX Path.resolve preserves spelling on case-insensitive macOS volumes.
    Confirm filesystem identity, then distinguish two real directory entries
    from two spellings of one entry. Never case-fold paths unconditionally.

    Compare spellings as strings, not as Path objects: WindowsPath equality and
    hashing are case-insensitive, so `math.ocl` and `Math.ocl` in a Windows
    case-sensitive directory would compare equal while naming different files.
    Resolution already canonicalizes an alias to its on-disk spelling on an
    ordinary Windows volume, so exact strings still coalesce real aliases there.
    """
    for known in known_paths:
        if str(target) == str(known):
            return known
        if (target.parent == known.parent
                and target.name.casefold() == known.name.casefold()
                and target.exists() and target.samefile(known)):
            names = {child.name for child in target.parent.iterdir()}
            if not (target.name in names and known.name in names):
                return known
    return target


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
    # Identity is the exact resolved spelling; see _existing_identity.
    modules = {str(entry): first}
    active = {str(entry)}
    finished = set()
    # Each frame holds a module, its next edge, and its direct dependency set.
    stack = [(first, 0, set())]
    while stack:
        module, index, seen = stack[-1]
        if index == len(module.imports):
            active.remove(str(module.path))
            finished.add(str(module.path))
            stack.pop()
            continue
        reference = module.imports[index]
        stack[-1] = (module, index + 1, seen)
        attempted = root / (reference.lexeme + '.ocl')
        try:
            target = attempted.resolve()
            target = _existing_identity(target, [loaded.path for loaded in modules.values()])
        except (OSError, RuntimeError) as error:
            raise DiagnosticError('E0301', f"cannot resolve module '{attempted}': {error}", module.source, reference.location) from None
        if target.parent != root:
            raise DiagnosticError('E0305', f"module '{attempted}' resolves outside entry directory", module.source, reference.location)
        key = str(target)
        if key in seen:
            raise DiagnosticError('E0302', f"duplicate import '{reference.lexeme}'", module.source, reference.location)
        seen.add(key)
        if key in active:
            chain = ' -> '.join([frame[0].path.name for frame in stack] + [target.name])
            raise DiagnosticError('E0303', f'import cycle: {chain}', module.source, reference.location)
        if key in finished:
            continue
        if len(modules) >= MAX_MODULES:
            raise DiagnosticError('E0304', f'compilation may contain at most {MAX_MODULES} source files', module.source, reference.location)
        try:
            child = _load(target)
        except (OSError, UnicodeError) as error:
            raise DiagnosticError('E0301', f"cannot read module '{attempted}': {error}", module.source, reference.location) from None
        modules[key] = child
        active.add(key)
        stack.append((child, 0, set()))

    enum_names = {module.tokens[i+1].lexeme for module in modules.values()
                  for i, token in enumerate(module.tokens[:-1])
                  if token.kind is TokenKind.ENUM and module.tokens[i+1].kind is TokenKind.IDENTIFIER}
    programs = []
    for module in modules.values():
        program = parse(module.tokens, module.source, enum_names=enum_names)
        if str(module.path) != str(entry):
            for function in program.functions:
                if function.name == 'main':
                    raise DiagnosticError('E0306', 'only the entry source may define main', module.source, function.location)
        programs.append(program)
    if not any(function.name == 'main' for function in programs[0].functions):
        raise DiagnosticError('E0204', 'program must define fn main() -> i32 in the entry source', first.source, first.tokens[-1].location)
    return Program(*(tuple(item for program in programs for item in getattr(program, attribute))
                     for attribute in ('functions', 'structures', 'enumerations', 'constants'))), first.source
