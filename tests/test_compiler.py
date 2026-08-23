import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from compiler import cli
from compiler.codegen import _escape_llvm_string, generate_llvm_ir
from compiler.diagnostics import DiagnosticError, InternalCompilerError, SourceLocation
from compiler.driver import compile_source
from compiler.nodes import BinaryExpression, CallExpression, Function, IdentifierExpression, IntegerLiteral, Program, ReturnStatement
from compiler.semantic import analyze

sys.path.insert(0, str(Path(__file__).parents[1] / "tools"))
import check_clang_version  # noqa: E402


VALID = "fn main() -> i32 {\n    return 42;\n}\n"
ROOT = Path(__file__).parents[1]


def require_clang() -> None:
    """Guard every Clang-dependent test identically.

    Skipping is a local convenience. CI sets OCL_REQUIRE_CLANG so that a missing
    toolchain fails instead, and no Clang-dependent coverage can quietly drop out
    of a green run.
    """
    from compiler.cli import _clang

    if _clang():
        return
    if os.environ.get("OCL_REQUIRE_CLANG"):
        raise AssertionError("OCL_REQUIRE_CLANG is set but Clang was not found")
    raise unittest.SkipTest("LLVM/Clang is not installed on this host")


class DiagnosticAssertions(unittest.TestCase):
    def assertDiagnostic(self, source: str, code: str, text: str) -> DiagnosticError:
        """Assert compilation fails with a specific error code and message."""
        with self.assertRaises(DiagnosticError) as caught:
            compile_source(source)
        self.assertEqual(caught.exception.code, code)
        self.assertIn(text, caught.exception.message)
        return caught.exception


class FrontendTests(DiagnosticAssertions):
    def test_valid_minimal_program(self):
        program, _ = compile_source(VALID)
        self.assertEqual(program.functions[0].name, "main")
        self.assertEqual(program.functions[0].return_type, "i32")
        self.assertEqual(program.functions[0].body[0].expression.value, 42)

    def test_invalid_token(self):
        self.assertDiagnostic(VALID.replace("42", "@42"), "E0001", "invalid token")

    def test_missing_function_body(self):
        self.assertDiagnostic("fn main() -> i32", "E0100", "expected '{'")

    def test_invalid_return_syntax(self):
        self.assertDiagnostic("fn main() -> i32 { return 42 }", "E0100", "expected ';'")

    def test_missing_function_name(self):
        self.assertDiagnostic("fn () -> i32 { return 1; }", "E0100", "expected function name")

    def test_parameter_requires_a_type(self):
        self.assertDiagnostic("fn main(a) -> i32 { return 1; }", "E0100", "expected ':'")

    def test_parameter_list_rejects_trailing_comma(self):
        self.assertDiagnostic("fn add(a: i32,) -> i32 { return a; } fn main() -> i32 { return 0; }", "E0100", "expected parameter after ','")

    def test_negative_literal_is_rejected(self):
        # 0.1 has no unary minus, and '-' must not be silently absorbed by '->'.
        self.assertDiagnostic("fn main() -> i32 { return -1; }", "E0001", "invalid token")


class SemanticTests(DiagnosticAssertions):
    def test_invalid_declared_type(self):
        self.assertDiagnostic(VALID.replace("i32", "i64"), "E0200", "unknown type")

    def test_duplicate_function_name(self):
        source = "fn main() -> i32 { return 1; }\nfn main() -> i32 { return 2; }\n"
        error = self.assertDiagnostic(source, "E0201", "duplicate function 'main'")
        self.assertEqual(error.location.line, 2)

    def test_empty_body_is_rejected(self):
        self.assertDiagnostic("fn main() -> i32 { }", "E0202", "exactly one return statement")

    def test_two_return_statements_are_rejected(self):
        self.assertDiagnostic("fn main() -> i32 { return 1; return 2; }", "E0202", "exactly one return statement")

    def test_i32_upper_bound_is_accepted(self):
        program, ir = compile_source("fn main() -> i32 { return 2147483647; }")
        self.assertEqual(program.functions[0].body[0].expression.value, 2147483647)
        self.assertIn("ret i32 2147483647", ir)

    def test_literal_one_past_i32_is_rejected(self):
        self.assertDiagnostic("fn main() -> i32 { return 2147483648; }", "E0203", "does not fit in i32")

    def test_very_long_literal_is_a_diagnostic_not_a_crash(self):
        # Regression: int() raises ValueError past CPython's digit limit, which
        # used to escape the compiler as a raw Python error.
        source = "fn main() -> i32 { return " + "9" * 5000 + "; }"
        self.assertDiagnostic(source, "E0203", "does not fit in i32")

    def test_leading_zeros_do_not_trip_the_length_guard(self):
        _, ir = compile_source("fn main() -> i32 { return 000000000000042; }")
        self.assertIn("ret i32 42", ir)

    def test_extremely_many_leading_zeros_do_not_crash(self):
        source = "fn main() -> i32 { return " + "0" * 5000 + "42; }"
        _, ir = compile_source(source)
        self.assertIn("ret i32 42", ir)

    def test_missing_main_is_rejected(self):
        self.assertDiagnostic("fn helper() -> i32 { return 1; }", "E0204", "must define fn main")

    def test_empty_source_is_rejected(self):
        self.assertDiagnostic("", "E0204", "must define fn main")

    def test_helper_functions_are_allowed_alongside_main(self):
        _, ir = compile_source("fn helper() -> i32 { return 7; }\nfn main() -> i32 { return 42; }\n")
        self.assertIn("define i32 @helper()", ir)
        self.assertIn("define i32 @main()", ir)

    def test_duplicate_parameter_is_rejected(self):
        self.assertDiagnostic(
            "fn add(a: i32, a: i32) -> i32 { return a; } fn main() -> i32 { return 0; }",
            "E0205", "duplicate parameter 'a'",
        )

    def test_unknown_identifier_is_rejected(self):
        self.assertDiagnostic("fn main() -> i32 { return missing; }", "E0206", "unknown identifier 'missing'")

    def test_unknown_function_is_rejected(self):
        self.assertDiagnostic("fn main() -> i32 { return missing(1); }", "E0207", "unknown function 'missing'")

    def test_wrong_argument_count_is_rejected(self):
        source = "fn add(a: i32, b: i32) -> i32 { return a + b; } fn main() -> i32 { return add(1); }"
        self.assertDiagnostic(source, "E0208", "expects 2 argument(s), got 1")

    def test_main_must_not_have_parameters(self):
        self.assertDiagnostic("fn main(value: i32) -> i32 { return value; }", "E0209", "must not declare parameters")

    def test_parameter_type_must_be_i32(self):
        source = "fn identity(value: i64) -> i32 { return value; } fn main() -> i32 { return 0; }"
        self.assertDiagnostic(source, "E0200", "unknown type 'i64'")

    def test_forward_function_call_is_resolved(self):
        source = "fn main() -> i32 { return answer(); } fn answer() -> i32 { return 42; }"
        _, ir = compile_source(source)
        self.assertIn("call i32 @answer()", ir)


class Ocl02Tests(unittest.TestCase):
    SOURCE = (
        "fn add(a: i32, b: i32) -> i32 {\n"
        "    return a + b;\n"
        "}\n\n"
        "fn main() -> i32 {\n"
        "    return add(20, 22);\n"
        "}\n"
    )

    def test_second_milestone_ast(self):
        program, _ = compile_source(self.SOURCE)
        add, main = program.functions
        self.assertEqual([(item.name, item.type_name) for item in add.parameters], [("a", "i32"), ("b", "i32")])
        addition = add.body[0].expression
        self.assertIsInstance(addition, BinaryExpression)
        self.assertIsInstance(addition.left, IdentifierExpression)
        self.assertIsInstance(addition.right, IdentifierExpression)
        call = main.body[0].expression
        self.assertIsInstance(call, CallExpression)
        self.assertEqual([argument.value for argument in call.arguments], [20, 22])

    def test_second_milestone_llvm_ir(self):
        _, ir = compile_source(self.SOURCE, "add.ocl")
        self.assertIn("define i32 @add(i32 %a, i32 %b)", ir)
        self.assertIn("%0 = add i32 %a, %b", ir)
        self.assertIn("%0 = call i32 @add(i32 20, i32 22)", ir)
        self.assertIn("ret i32 %0", ir)

    def test_addition_is_left_associative(self):
        source = "fn sum(a: i32, b: i32) -> i32 { return a + b + 1; } fn main() -> i32 { return sum(20, 21); }"
        _, ir = compile_source(source)
        self.assertIn("%0 = add i32 %a, %b", ir)
        self.assertIn("%1 = add i32 %0, 1", ir)

    def test_nested_call_arguments(self):
        source = "fn id(a: i32) -> i32 { return a; } fn main() -> i32 { return id(id(42)); }"
        _, ir = compile_source(source)
        self.assertIn("%0 = call i32 @id(i32 42)", ir)
        self.assertIn("%1 = call i32 @id(i32 %0)", ir)

    def test_second_milestone_native_exit_value(self):
        require_clang()
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / ("add.exe" if os.name == "nt" else "add")
            result = subprocess.run(
                [sys.executable, str(ROOT / "oclc.py"), "build", str(ROOT / "examples" / "add.ocl"), "-o", str(executable)],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(subprocess.run([str(executable)]).returncode, 42)


class DiagnosticRenderingTests(unittest.TestCase):
    """Diagnostics are a product requirement, so the rendered output is asserted."""

    def _render(self, source: str) -> str:
        with self.assertRaises(DiagnosticError) as caught:
            compile_source(source)
        return caught.exception.render("hello.ocl")

    def test_rendered_diagnostic_has_every_required_part(self):
        rendered = self._render("fn main() -> i32 {\n    return @;\n}\n")
        self.assertEqual(
            rendered,
            "error[E0001]: invalid token '@'\n"
            " --> hello.ocl:2:12\n"
            "\n"
            "2 |     return @;\n"
            "  |            ^",
        )

    def test_caret_aligns_under_the_offending_column(self):
        rendered = self._render("fn main() -> i32 {\n    return @;\n}\n")
        snippet, caret = rendered.splitlines()[-2:]
        self.assertEqual(snippet.index("@"), caret.index("^"))

    def test_location_tracks_lines_and_columns(self):
        with self.assertRaises(DiagnosticError) as caught:
            compile_source("fn main() -> i32 {\n\n\n    return 42\n}\n")
        # The ';' is expected where '}' was found, at the start of line 5.
        self.assertEqual(caught.exception.location.line, 5)
        self.assertEqual(caught.exception.location.column, 1)

    def test_render_survives_a_location_past_the_end_of_source(self):
        error = DiagnosticError("E0999", "synthetic", "fn main()", SourceLocation(0, 99, 1))
        rendered = error.render("x.ocl")
        self.assertIn("error[E0999]: synthetic", rendered)
        self.assertIn("x.ocl:99:1", rendered)


class CodegenTests(unittest.TestCase):
    def test_successful_llvm_ir_generation(self):
        _, ir = compile_source(VALID)
        self.assertIn("define i32 @main()", ir)
        self.assertIn("ret i32 42", ir)

    def test_codegen_rejects_an_unanalyzed_program(self):
        # Regression: codegen used to raise IndexError on a body that semantic
        # analysis would have rejected.
        location = SourceLocation(0, 1, 1)
        program = Program((Function("main", "i32", (), location),))
        with self.assertRaises(InternalCompilerError):
            generate_llvm_ir(program)

    def test_analyze_is_what_makes_codegen_safe(self):
        location = SourceLocation(0, 1, 1)
        body = (ReturnStatement(IntegerLiteral(1, location), location),) * 2
        program = Program((Function("main", "i32", body, location),))
        with self.assertRaises(DiagnosticError) as caught:
            analyze(program, "")
        self.assertEqual(caught.exception.code, "E0202")

    def test_source_filename_is_escaped_as_utf8_bytes(self):
        escaped = _escape_llvm_string('quote" slash\\ newline\n snowman-☃.ocl')
        self.assertEqual(escaped, r"quote\22 slash\5C newline\0A snowman-\E2\98\83.ocl")
        _, ir = compile_source(VALID, 'quote"\\\n.ocl')
        self.assertIn(r'source_filename = "quote\22\5C\0A.ocl"', ir)


class CliTests(unittest.TestCase):
    def _run(self, *args: str, env: dict | None = None, cwd: str | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(ROOT / "oclc.py"), *args],
            capture_output=True, text=True, env=env, cwd=cwd,
        )

    def _write(self, directory: str, name: str, source: str, encoding: str = "utf-8") -> str:
        path = Path(directory) / name
        path.write_text(source, encoding=encoding)
        return str(path)

    def test_check_succeeds_on_valid_source(self):
        result = self._run("check", str(ROOT / "examples" / "hello.ocl"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("checked", result.stdout)

    def test_check_reports_a_diagnostic_and_exits_one(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self._write(directory, "bad.ocl", "fn main() -> i32 { return @; }")
            result = self._run("check", source)
        self.assertEqual(result.returncode, 1)
        self.assertIn("error[E0001]", result.stderr)
        self.assertIn("bad.ocl:1:27", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_cli_emits_ir(self):
        result = self._run("emit-ir", str(ROOT / "examples" / "hello.ocl"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ret i32 42", result.stdout)

    def test_emit_ir_writes_to_the_output_path(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "hello.ll"
            result = self._run("emit-ir", str(ROOT / "examples" / "hello.ocl"), "-o", str(out))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("ret i32 42", out.read_text(encoding="utf-8"))

    def test_byte_order_mark_is_accepted(self):
        # Several Windows editors write a BOM by default; it must not reach the lexer.
        with tempfile.TemporaryDirectory() as directory:
            source = self._write(directory, "bom.ocl", VALID, encoding="utf-8-sig")
            result = self._run("emit-ir", source)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ret i32 42", result.stdout)

    def test_wrong_extension_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self._write(directory, "hello.txt", VALID)
            result = self._run("check", source)
        self.assertEqual(result.returncode, 1)
        self.assertIn(".ocl extension", result.stderr)

    def test_missing_file_is_reported_cleanly(self):
        result = self._run("check", str(ROOT / "does_not_exist.ocl"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("error:", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_pinned_clang_that_does_not_exist_is_an_error(self):
        # A pinned toolchain must never silently fall back to another compiler.
        env = dict(os.environ, OCL_CLANG=str(ROOT / "no" / "such" / "clang"))
        with tempfile.TemporaryDirectory() as directory:
            result = self._run(
                "build", str(ROOT / "examples" / "hello.ocl"),
                "-o", str(Path(directory) / "out"), env=env,
            )
        self.assertEqual(result.returncode, 1)
        self.assertIn("OCL_CLANG", result.stderr)

    def test_build_does_not_clobber_source_adjacent_ir(self):
        require_clang()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(self._write(directory, "hello.ocl", VALID))
            neighboring_ir = source.with_suffix(".ll")
            neighboring_ir.write_text("user-owned", encoding="utf-8")
            output = Path(directory) / ("hello.exe" if os.name == "nt" else "hello")
            result = self._run("build", str(source), "-o", str(output))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(neighboring_ir.read_text(encoding="utf-8"), "user-owned")

    def test_dash_prefixed_source_cannot_become_a_clang_argument(self):
        require_clang()
        with tempfile.TemporaryDirectory() as directory:
            self._write(directory, "-warning.ocl", VALID)
            output = Path(directory) / ("out.exe" if os.name == "nt" else "out")
            result = self._run("build", "./-warning.ocl", "-o", str(output), cwd=directory)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(output.is_file())

    def test_external_clang_exit_code_is_normalized(self):
        completed = subprocess.CompletedProcess([], 70, "", "synthetic clang failure\n")
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(cli, "_clang", return_value=sys.executable), mock.patch.object(cli.subprocess, "run", return_value=completed):
            source = self._write(directory, "hello.ocl", VALID)
            output = str(Path(directory) / "out")
            with mock.patch("sys.stderr") as stderr:
                result = cli.main(["build", source, "-o", output])
        self.assertEqual(result, 1)
        stderr.write.assert_called()


class ToolchainCheckTests(unittest.TestCase):
    """The CI toolchain gate is pure enough to test without three compilers installed."""

    UPSTREAM = "clang version 18.1.8 (https://github.com/llvm/llvm-project 3b5b5c1)\nTarget: x86_64-pc-windows-msvc\n"
    UBUNTU = "Ubuntu clang version 18.1.3 (1ubuntu1)\nTarget: x86_64-pc-linux-gnu\n"
    APPLE = "Apple clang version 16.0.0 (clang-1600.0.26.6)\nTarget: arm64-apple-darwin23.6.0\n"

    def test_upstream_and_distribution_builds_are_llvm_numbered(self):
        self.assertEqual(check_clang_version.parse_clang_version(self.UPSTREAM), ("llvm", 18, 1))
        self.assertEqual(check_clang_version.parse_clang_version(self.UBUNTU), ("llvm", 18, 1))

    def test_apple_clang_is_recognised_as_its_own_vendor(self):
        # Apple's numbering is not upstream's; conflating them would apply the
        # wrong floor to macOS.
        self.assertEqual(check_clang_version.parse_clang_version(self.APPLE), ("apple", 16, 0))

    def test_each_vendor_gets_its_documented_floor(self):
        self.assertEqual(check_clang_version.minimum_for("llvm"), 18)
        self.assertEqual(check_clang_version.minimum_for("apple"), 15)

    def test_supported_versions_pass(self):
        for text in (self.UPSTREAM, self.UBUNTU, self.APPLE):
            self.assertIn("satisfies", check_clang_version.check_version_text(text))

    def test_an_old_upstream_clang_is_rejected(self):
        with self.assertRaises(check_clang_version.ToolchainError) as caught:
            check_clang_version.check_version_text("clang version 17.0.6\n")
        self.assertIn("older than", str(caught.exception))

    def test_an_old_apple_clang_is_rejected(self):
        with self.assertRaises(check_clang_version.ToolchainError):
            check_clang_version.check_version_text("Apple clang version 14.0.3 (clang-1403.0.22.14.1)\n")

    def test_an_apple_clang_below_the_llvm_floor_still_passes(self):
        # Guards the whole point of the split: Apple Clang 16 must not be judged
        # against the LLVM 18 floor.
        self.assertIn("Apple Clang 16", check_clang_version.check_version_text(self.APPLE))

    def test_unparseable_output_is_an_error_not_a_crash(self):
        with self.assertRaises(check_clang_version.ToolchainError):
            check_clang_version.check_version_text("gcc (GCC) 13.2.0\n")

    def test_the_check_runs_against_this_host(self):
        require_clang()
        with contextlib.redirect_stdout(io.StringIO()) as captured:
            self.assertEqual(check_clang_version.main(), 0)
        self.assertIn("satisfies", captured.getvalue())


class NativeTests(unittest.TestCase):
    def test_native_build_and_exit_value(self):
        require_clang()
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / ("hello.exe" if os.name == "nt" else "hello")
            result = subprocess.run(
                [sys.executable, str(ROOT / "oclc.py"), "build",
                 str(ROOT / "examples" / "hello.ocl"), "-o", str(executable)],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(subprocess.run([str(executable)]).returncode, 42)


if __name__ == "__main__":
    unittest.main()
