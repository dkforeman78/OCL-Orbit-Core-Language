import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from compiler.codegen import generate_llvm_ir
from compiler.diagnostics import DiagnosticError
from compiler.driver import compile_source
from compiler.lexer import lex
from compiler.parser import parse


VALID = "fn main() -> i32 {\n    return 42;\n}\n"
ROOT = Path(__file__).parents[1]


class CompilerTests(unittest.TestCase):
    def assertDiagnostic(self, source: str, text: str) -> None:
        with self.assertRaises(DiagnosticError) as caught:
            compile_source(source)
        self.assertIn(text, caught.exception.message)

    def test_valid_minimal_program(self):
        program, _ = compile_source(VALID)
        self.assertEqual(program.functions[0].name, "main")

    def test_invalid_token(self):
        self.assertDiagnostic(VALID.replace("42", "@42"), "invalid token")

    def test_missing_function_body(self):
        self.assertDiagnostic("fn main() -> i32", "expected '{'")

    def test_invalid_return_syntax(self):
        self.assertDiagnostic("fn main() -> i32 { return 42 }", "expected ';'")

    def test_invalid_declared_type(self):
        self.assertDiagnostic(VALID.replace("i32", "i64"), "unknown type")

    def test_successful_llvm_ir_generation(self):
        ir = generate_llvm_ir(parse(lex(VALID), VALID))
        self.assertIn("define i32 @main()", ir)
        self.assertIn("ret i32 42", ir)

    def test_cli_emits_ir(self):
        result = subprocess.run([sys.executable, str(ROOT / "oclc.py"), "emit-ir", str(ROOT / "examples" / "hello.ocl")], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ret i32 42", result.stdout)

    def test_native_build_and_exit_value(self):
        from compiler.cli import _clang
        if not _clang():
            self.skipTest("LLVM/Clang is not installed on this host")
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / ("hello.exe" if os.name == "nt" else "hello")
            result = subprocess.run([sys.executable, str(ROOT / "oclc.py"), "build", str(ROOT / "examples" / "hello.ocl"), "-o", str(executable)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(subprocess.run([str(executable)]).returncode, 42)


if __name__ == "__main__":
    unittest.main()
