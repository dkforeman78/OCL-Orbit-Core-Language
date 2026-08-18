from .ast import Program
from .codegen import generate_llvm_ir
from .lexer import lex
from .parser import parse
from .semantic import analyze


def compile_source(source: str, source_name: str = "input.ocl") -> tuple[Program, str]:
    program = parse(lex(source), source)
    analyze(program, source)
    return program, generate_llvm_ir(program, source_name)
