from .nodes import Program
from .codegen import generate_llvm_ir
from .lexer import lex, TokenKind
from .diagnostics import DiagnosticError
from .parser import parse
from .semantic import analyze


def compile_source(source: str, source_name: str = "input.ocl") -> tuple[Program, str]:
    tokens = lex(source)
    for token in tokens:
        if token.kind is TokenKind.IMPORT:
            raise DiagnosticError('E0300', 'imports require the compile_file API or a CLI file command', source, token.location)
    program = parse(tokens, source)
    analyze(program, source)
    return program, generate_llvm_ir(program, source_name)


def compile_file(path) -> tuple[Program, str]:
    from pathlib import Path
    from .modules import load_program
    path = Path(path)
    if path.suffix.lower() != '.ocl':
        raise ValueError('input file must use the .ocl extension')
    try:
        program, source = load_program(path)
        analyze(program, source)
        return program, generate_llvm_ir(program, path.name)
    except DiagnosticError as error:
        if error.filename is None or error.filename == str(path.resolve()):
            error.filename = str(path)
        raise
