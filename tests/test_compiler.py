import contextlib
import ctypes
import io
import os
import signal
import subprocess
import sys
import tempfile
import threading
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

from compiler import cli, parser as parser_module
from compiler.codegen import _escape_llvm_string, generate_llvm_ir
from compiler.diagnostics import DiagnosticError, InternalCompilerError, SourceLocation
from compiler.driver import compile_source
from compiler.lexer import lex
from compiler.parser import parse
from compiler.nodes import AssignmentStatement, BinaryExpression, BlockStatement, BooleanLiteral, BreakStatement, CallExpression, ContinueStatement, Function, IdentifierExpression, IfExpression, IntegerLiteral, LetStatement, Program, ReturnStatement, UnaryExpression, VarStatement, WhileStatement
from compiler.semantic import analyze

sys.path.insert(0, str(Path(__file__).parents[1] / "tools"))
import check_clang_version  # noqa: E402


VALID = "fn main() -> i32 {\n    return 42;\n}\n"
ROOT = Path(__file__).parents[1]
_EXECUTABLE_RUN_LOCK = threading.Lock()


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



def run_executable(path, timeout: float = 20.0) -> int:
    """Run a built OCL program and return its exit code.

    Bounded on purpose. A defect in loop or short-circuit lowering yields IR that
    LLVM happily accepts and a binary that never terminates; an unbounded run
    turns that into a hung suite rather than a failing test, and in CI into a job
    that spins until the platform kills it.
    """
    # Windows Error Reporting can hold a deliberately trapping child open while
    # it waits for crash UI, hiding the status this helper exists to inspect.
    # Error mode is process-global and inherited, so serialize and restore it.
    with _EXECUTABLE_RUN_LOCK:
        previous_error_mode = None
        if os.name == "nt":
            previous_error_mode = ctypes.windll.kernel32.SetErrorMode(0x0002)
        try:
            return subprocess.run([str(path)], timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            raise AssertionError(
                f"{Path(path).name} did not terminate within {timeout}s; "
                "control flow lowering probably produced an infinite loop"
            ) from None
        finally:
            if previous_error_mode is not None:
                ctypes.windll.kernel32.SetErrorMode(previous_error_mode)



# A deliberate llvm.trap and undefined behaviour that merely happens to fault are
# both "nonzero exit", so asserting only that cannot tell them apart. They do
# carry distinct signatures, and the spec promises the deterministic one.
if os.name == "nt":
    _TRAP_EXITS = {0xC000001D}                      # STATUS_ILLEGAL_INSTRUCTION
    _UB_FAULTS = {
        0xC0000094: "integer divide by zero",
        0xC0000095: "integer overflow",
    }
    def _exit_signature(code: int) -> int:
        return code & 0xFFFFFFFF
else:
    # LLVM lowers llvm.trap to SIGTRAP on macOS and commonly SIGILL (or an
    # abort fallback) elsewhere. Raw integer division faults remain SIGFPE.
    _TRAP_EXITS = {-signal.SIGTRAP, -signal.SIGILL, -signal.SIGABRT}
    _UB_FAULTS = {-signal.SIGFPE: "arithmetic fault"}
    def _exit_signature(code: int) -> int:
        return code


def assert_deterministic_trap(case, exit_code: int) -> None:
    """Assert the program stopped via the documented trap, not via raw UB.

    A defeated guard lets the operands reach `sdiv`/`srem`, and the hardware
    faults with its own status. That is still a nonzero exit, so a test that
    only checks "not zero" passes while the trap policy has actually been lost.
    """
    signature = _exit_signature(exit_code)
    if signature in _UB_FAULTS:
        case.fail(
            f"program stopped with {_UB_FAULTS[signature]} (0x{signature:08X}), not the "
            "deterministic trap: the guard did not fire and the operands reached the "
            "raw division, which is undefined behaviour"
        )
    case.assertIn(
        signature, _TRAP_EXITS,
        f"expected a deterministic trap, got exit signature 0x{signature:08X}",
    )


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

    def test_argument_list_rejects_trailing_comma(self):
        # L2: the symmetric case to the parameter list above.
        source = "fn id(a: i32) -> i32 { return a; }\nfn main() -> i32 { return id(1,); }\n"
        self.assertDiagnostic(source, "E0100", "expected argument after ','")

    def test_argument_list_rejects_trailing_comma_after_several(self):
        source = "fn add(a: i32, b: i32) -> i32 { return a + b; }\nfn main() -> i32 { return add(1, 2,); }\n"
        self.assertDiagnostic(source, "E0100", "expected argument after ','")

    def test_negative_literal_is_supported_from_06(self):
        _, ir = compile_source("fn main() -> i32 { return -1 + 43; }")
        self.assertIn("add i32 -1, 43", ir)


class SemanticTests(DiagnosticAssertions):
    def test_invalid_declared_type(self):
        self.assertDiagnostic(VALID.replace("i32", "i64"), "E0200", "unknown type")

    def test_duplicate_function_name(self):
        source = "fn main() -> i32 { return 1; }\nfn main() -> i32 { return 2; }\n"
        error = self.assertDiagnostic(source, "E0201", "duplicate function 'main'")
        self.assertEqual(error.location.line, 2)

    def test_empty_body_is_rejected(self):
        self.assertDiagnostic("fn main() -> i32 { }", "E0202", "without returning")

    def test_two_return_statements_are_rejected(self):
        self.assertDiagnostic("fn main() -> i32 { return 1; return 2; }", "E0216", "unreachable")

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
            self.assertEqual(run_executable(executable), 42)


class Ocl03Tests(DiagnosticAssertions):
    SOURCE = (
        "fn calculate(a: i32, b: i32) -> i32 {\n"
        "    let product: i32 = a * b;\n"
        "    let adjusted: i32 = product - 2;\n"
        "    return adjusted + (2 * 2);\n"
        "}\n\n"
        "fn main() -> i32 {\n"
        "    return calculate(5, 8);\n"
        "}\n"
    )

    def test_local_computation_ast(self):
        program, _ = compile_source(self.SOURCE)
        calculate = program.functions[0]
        self.assertEqual(len(calculate.body), 3)
        self.assertIsInstance(calculate.body[0], LetStatement)
        self.assertEqual((calculate.body[0].name, calculate.body[0].type_name), ("product", "i32"))
        self.assertIsInstance(calculate.body[-1], ReturnStatement)

    def test_local_computation_llvm_ir(self):
        _, ir = compile_source(self.SOURCE, "local.ocl")
        self.assertIn("%0 = mul i32 %a, %b", ir)
        self.assertIn("%1 = sub i32 %0, 2", ir)
        self.assertIn("%2 = mul i32 2, 2", ir)
        self.assertIn("%3 = add i32 %1, %2", ir)
        self.assertNotIn("alloca", ir)
        self.assertNotIn("store", ir)

    def test_multiplication_has_higher_precedence(self):
        _, ir = compile_source("fn main() -> i32 { return 2 + 3 * 4; }")
        operations = [line.strip() for line in ir.splitlines() if " = " in line and "i32" in line]
        self.assertEqual(operations, ["%0 = mul i32 3, 4", "%1 = add i32 2, %0"])

    def test_parentheses_override_precedence(self):
        _, ir = compile_source("fn main() -> i32 { return (2 + 3) * 4; }")
        operations = [line.strip() for line in ir.splitlines() if " = " in line and "i32" in line]
        self.assertEqual(operations, ["%0 = add i32 2, 3", "%1 = mul i32 %0, 4"])

    def test_addition_and_subtraction_are_left_associative(self):
        _, ir = compile_source("fn main() -> i32 { return 20 - 3 + 25; }")
        self.assertIn("%0 = sub i32 20, 3", ir)
        self.assertIn("%1 = add i32 %0, 25", ir)

    def test_earlier_local_is_visible(self):
        _, ir = compile_source("fn main() -> i32 { let first: i32 = 40; let answer: i32 = first + 2; return answer; }")
        self.assertIn("%0 = add i32 40, 2", ir)
        self.assertIn("ret i32 %0", ir)

    def test_local_cannot_reference_itself(self):
        self.assertDiagnostic("fn main() -> i32 { let answer: i32 = answer; return answer; }", "E0206", "unknown identifier 'answer'")

    def test_local_cannot_reference_a_later_local(self):
        source = "fn main() -> i32 { let first: i32 = second; let second: i32 = 42; return first; }"
        self.assertDiagnostic(source, "E0206", "unknown identifier 'second'")

    def test_duplicate_local_is_rejected(self):
        source = "fn main() -> i32 { let value: i32 = 1; let value: i32 = 2; return value; }"
        self.assertDiagnostic(source, "E0210", "already declared")

    def test_local_cannot_shadow_parameter(self):
        # Distinct rule from a duplicate local, so the message names the
        # parameter: "already declared" would send the reader hunting for a
        # `let` that does not exist.
        source = "fn f(value: i32) -> i32 { let value: i32 = 1; return value; } fn main() -> i32 { return f(42); }"
        error = self.assertDiagnostic(source, "E0210", "already declared")
        self.assertIn("'value'", error.message)

    def test_duplicate_local_message_says_local(self):
        source = "fn main() -> i32 { let a: i32 = 1; let a: i32 = 2; return a; }"
        error = self.assertDiagnostic(source, "E0210", "already declared")
        self.assertNotIn("parameter", error.message)

    def test_local_type_must_be_i32(self):
        self.assertDiagnostic("fn main() -> i32 { let value: i64 = 42; return value; }", "E0200", "unknown type 'i64'")

    def test_return_must_be_the_final_statement(self):
        source = "fn main() -> i32 { return 1; let value: i32 = 42; }"
        self.assertDiagnostic(source, "E0216", "unreachable")

    def test_missing_local_initializer_is_rejected(self):
        self.assertDiagnostic("fn main() -> i32 { let value: i32; return value; }", "E0100", "expected '='")

    def test_local_binding_does_not_replace_the_required_return(self):
        self.assertDiagnostic("fn main() -> i32 { let value: i32 = 42; }", "E0202", "without returning")

    def test_missing_closing_parenthesis_is_rejected(self):
        self.assertDiagnostic("fn main() -> i32 { return (40 + 2; }", "E0100", "expected ')'")

    def test_reassignment_is_not_part_of_ocl_03(self):
        source = "fn main() -> i32 { let value: i32 = 1; value = 42; return value; }"
        self.assertDiagnostic(source, "E0215", "immutable")

    def test_native_local_computation_returns_42(self):
        require_clang()
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / ("local.exe" if os.name == "nt" else "local")
            result = subprocess.run(
                [sys.executable, str(ROOT / "oclc.py"), "build", str(ROOT / "examples" / "local.ocl"), "-o", str(executable)],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(run_executable(executable), 42)


class Ocl04Tests(DiagnosticAssertions):
    SOURCE = (
        "fn choose(value: i32, limit: i32) -> i32 {\n"
        "    let within_limit: bool = value <= limit;\n"
        "    return if within_limit { value * 2 } else { limit };\n"
        "}\n\n"
        "fn main() -> i32 {\n"
        "    let below: i32 = choose(21, 42);\n"
        "    let above: i32 = choose(84, 42);\n"
        "    return below + above - 42;\n"
        "}\n"
    )

    def test_decisions_ast(self):
        program, _ = compile_source(self.SOURCE)
        choose = program.functions[0]
        self.assertEqual(choose.body[0].type_name, "bool")
        self.assertIsInstance(choose.body[0].initializer, BinaryExpression)
        decision = choose.body[-1].expression
        self.assertIsInstance(decision, IfExpression)
        self.assertIsInstance(decision.condition, IdentifierExpression)

    def test_boolean_literals_ast(self):
        program, _ = compile_source("fn main() -> i32 { return if true { 42 } else { 0 }; }")
        condition = program.functions[0].body[-1].expression.condition
        self.assertIsInstance(condition, BooleanLiteral)
        self.assertTrue(condition.value)

    def test_decisions_llvm_ir(self):
        _, ir = compile_source(self.SOURCE, "decisions.ocl")
        self.assertIn("%0 = icmp sle i32 %value, %limit", ir)
        self.assertIn("br i1 %0, label %ocl.if.then.0, label %ocl.if.else.0", ir)
        self.assertIn("ocl.if.then.0:", ir)
        self.assertIn("ocl.if.else.0:", ir)
        self.assertIn("ocl.if.merge.0:", ir)
        self.assertIn("phi i32", ir)

    def test_every_comparison_operator_lowers(self):
        predicates = {"<": "slt", "<=": "sle", ">": "sgt", ">=": "sge", "==": "eq", "!=": "ne"}
        for operator, predicate in predicates.items():
            with self.subTest(operator=operator):
                source = f"fn main() -> i32 {{ return if 1 {operator} 2 {{ 42 }} else {{ 0 }}; }}"
                _, ir = compile_source(source)
                self.assertIn(f"icmp {predicate} i32 1, 2", ir)

    def test_bool_function_parameter_and_return_lower_as_i1(self):
        source = "fn identity(value: bool) -> bool { return value; } fn main() -> i32 { return if identity(true) { 42 } else { 0 }; }"
        _, ir = compile_source(source)
        self.assertIn("define i1 @identity(i1 %value)", ir)
        self.assertIn("call i1 @identity(i1 1)", ir)

    def test_bool_equality_is_supported(self):
        _, ir = compile_source("fn main() -> i32 { return if true != false { 42 } else { 0 }; }")
        self.assertIn("icmp ne i1 1, 0", ir)

    def test_precedence_is_arithmetic_then_relational_then_equality(self):
        _, ir = compile_source("fn main() -> i32 { return if 1 + 2 < 4 == true { 42 } else { 0 }; }")
        operations = [line.strip() for line in ir.splitlines() if line.strip().startswith("%")]
        self.assertEqual(operations[:3], [
            "%0 = add i32 1, 2",
            "%1 = icmp slt i32 %0, 4",
            "%2 = icmp eq i1 %1, 1",
        ])

    def _phi_lines(self, source: str) -> list[str]:
        _, ir = compile_source(source)
        return [line.strip() for line in ir.splitlines() if "= phi " in line]

    def test_nested_if_in_then_uses_nested_merge_as_phi_predecessor(self):
        # A branch that itself contains control flow no longer reaches the merge
        # from the branch's own label, so the phi must name the nested merge.
        phis = self._phi_lines("fn main() -> i32 { return if true { if false { 0 } else { 42 } } else { 1 }; }")
        self.assertEqual(phis, [
            "%0 = phi i32 [0, %ocl.if.then.1], [42, %ocl.if.else.1]",
            "%1 = phi i32 [%0, %ocl.if.merge.1], [1, %ocl.if.else.0]",
        ])

    def test_nested_if_in_else_uses_nested_merge_as_phi_predecessor(self):
        # The mirror of the case above. Naming the else *label* rather than the
        # block the value was produced in yields IR that LLVM rejects with
        # "PHI node entries do not match predecessors!".
        phis = self._phi_lines("fn main() -> i32 { return if false { 1 } else { if true { 42 } else { 0 } }; }")
        self.assertEqual(phis, [
            "%0 = phi i32 [42, %ocl.if.then.1], [0, %ocl.if.else.1]",
            "%1 = phi i32 [1, %ocl.if.then.0], [%0, %ocl.if.merge.1]",
        ])

    def test_nested_if_in_both_branches_names_both_nested_merges(self):
        source = ("fn f(a: bool, b: bool, c: bool) -> i32 { "
                  "return if a { if b { 1 } else { 2 } } else { if c { 3 } else { 4 } }; } "
                  "fn main() -> i32 { return f(true, true, true); }")
        phis = self._phi_lines(source)
        self.assertEqual(phis[-1], "%2 = phi i32 [%0, %ocl.if.merge.1], [%1, %ocl.if.merge.2]")

    def test_bool_arithmetic_is_rejected(self):
        self.assertDiagnostic("fn main() -> i32 { return true + false; }", "E0211", "requires i32 operands")

    def test_bool_relational_comparison_is_rejected(self):
        self.assertDiagnostic("fn main() -> i32 { return if true < false { 42 } else { 0 }; }", "E0211", "requires i32 operands")

    def test_equality_requires_matching_types(self):
        self.assertDiagnostic("fn main() -> i32 { return if true == 1 { 42 } else { 0 }; }", "E0211", "matching operand types")

    def test_if_condition_must_be_bool(self):
        self.assertDiagnostic("fn main() -> i32 { return if 1 { 42 } else { 0 }; }", "E0212", "condition must be bool")

    def test_if_branches_must_match(self):
        self.assertDiagnostic("fn main() -> i32 { return if true { 42 } else { false }; }", "E0213", "same type")

    def test_local_initializer_type_must_match(self):
        self.assertDiagnostic("fn main() -> i32 { let value: bool = 42; return 0; }", "E0214", "expects bool, got i32")

    def test_function_return_type_must_match(self):
        source = "fn predicate() -> bool { return 42; } fn main() -> i32 { return 0; }"
        self.assertDiagnostic(source, "E0214", "returns bool, got i32")

    def test_call_argument_type_must_match(self):
        source = "fn predicate(value: bool) -> bool { return value; } fn main() -> i32 { return if predicate(1) { 42 } else { 0 }; }"
        self.assertDiagnostic(source, "E0214", "expects bool, got i32")

    def test_main_must_still_return_i32(self):
        self.assertDiagnostic("fn main() -> bool { return true; }", "E0214", "main function must return i32")

    def test_if_requires_else(self):
        self.assertDiagnostic("fn main() -> i32 { return if true { 42 }; }", "E0100", "expected 'else'")

    def test_bang_was_not_a_unary_operator_before_05(self):
        _, ir = compile_source("fn main() -> i32 { return if !false { 42 } else { 0 }; }")
        self.assertIn("xor i1 0, true", ir)

    def test_nested_if_chain_compiles_without_recursive_semantic_or_codegen_walks(self):
        depth = 200
        expression = "42"
        for _ in range(depth):
            expression = f"if true {{ {expression} }} else {{ 0 }}"
        _, ir = compile_source(f"fn main() -> i32 {{ return {expression}; }}")
        self.assertEqual(ir.count("phi i32"), depth)

    def test_excessive_if_nesting_is_a_diagnostic(self):
        depth = 5000
        expression = "if true { " * depth + "42" + " } else { 0 }" * depth
        self.assertDiagnostic(f"fn main() -> i32 {{ return {expression}; }}", "E0101", "nested too deeply")

    def test_if_nesting_obeys_the_exact_documented_boundary(self):
        limit = parser_module.MAX_EXPRESSION_DEPTH
        expression = "42"
        for _ in range(limit - 1):
            expression = f"if true {{ {expression} }} else {{ 0 }}"
        _, ir = compile_source(f"fn main() -> i32 {{ return {expression}; }}")
        self.assertEqual(ir.count("phi i32"), limit - 1)
        expression = f"if true {{ {expression} }} else {{ 0 }}"
        self.assertDiagnostic(f"fn main() -> i32 {{ return {expression}; }}", "E0101", "nested too deeply")

    def test_multiple_if_expressions_get_unique_generated_blocks(self):
        source = ("fn main() -> i32 { let first: i32 = if true { 40 } else { 0 }; "
                  "let second: i32 = if false { 0 } else { 2 }; return first + second; }")
        _, ir = compile_source(source)
        labels = [line for line in ir.splitlines() if line.startswith("ocl.if.") and line.endswith(":")]
        self.assertEqual(len(labels), 6)
        self.assertEqual(len(set(labels)), 6)

    def test_nested_if_branches_select_the_right_value_natively(self):
        # IR shape assertions cannot tell a correct phi from a valid but
        # semantically wrong one, so the whole truth table is executed.
        require_clang()
        source = (
            "fn pick(a: bool, b: bool, c: bool) -> i32 { "
            "return if a { if b { 11 } else { 22 } } else { if c { 33 } else { 44 } }; } "
            "fn main() -> i32 { return pick(A, B, C); }"
        )
        expected = {
            ("true", "true", "true"): 11, ("true", "true", "false"): 11,
            ("true", "false", "true"): 22, ("true", "false", "false"): 22,
            ("false", "true", "true"): 33, ("false", "true", "false"): 44,
            ("false", "false", "true"): 33, ("false", "false", "false"): 44,
        }
        with tempfile.TemporaryDirectory() as directory:
            for (a, b, c), want in expected.items():
                with self.subTest(a=a, b=b, c=c):
                    program = source.replace("A", a).replace("B", b).replace("C", c)
                    path = Path(directory) / "pick.ocl"
                    path.write_text(program, encoding="utf-8")
                    executable = Path(directory) / ("pick.exe" if os.name == "nt" else "pick")
                    result = subprocess.run(
                        [sys.executable, str(ROOT / "oclc.py"), "build", str(path), "-o", str(executable)],
                        capture_output=True, text=True,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(run_executable(executable), want)

    def test_native_decision_returns_42(self):
        require_clang()
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / ("decisions.exe" if os.name == "nt" else "decisions")
            result = subprocess.run(
                [sys.executable, str(ROOT / "oclc.py"), "build", str(ROOT / "examples" / "decisions.ocl"), "-o", str(executable)],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(run_executable(executable), 42)


class Ocl05Tests(DiagnosticAssertions):
    SOURCE = ("fn main() -> i32 { var total: i32 = 0; var count: i32 = 0; "
              "while count < 6 && !(count == 99) { total = total + 7; count = count + 1; } return total; }")

    def test_mutation_and_while_ast(self):
        program, _ = compile_source(self.SOURCE)
        body = program.functions[0].body
        self.assertIsInstance(body[0], VarStatement)
        self.assertIsInstance(body[2], WhileStatement)
        self.assertIsInstance(body[2].body, BlockStatement)
        self.assertIsInstance(body[2].body.statements[0], AssignmentStatement)

    def test_mutable_storage_and_loop_cfg_lower(self):
        _, ir = compile_source(self.SOURCE)
        self.assertIn("%ocl.var.total = alloca i32", ir)
        self.assertIn("ocl.while.condition.0:", ir)
        self.assertIn("ocl.while.body.0:", ir)
        self.assertIn("ocl.while.exit.0:", ir)
        self.assertIn("store i32", ir)
        self.assertIn("load i32", ir)

    def test_logical_operators_short_circuit_with_phi(self):
        _, ir = compile_source("fn main() -> i32 { return if true || false && false { 42 } else { 0 }; }")
        self.assertEqual(ir.count("phi i1"), 2)
        self.assertIn("phi i1 [1, %ocl.entry], [%0, %ocl.logic.merge.1]", ir)

    def test_short_circuit_skips_the_right_operand_natively(self):
        require_clang()
        source = ("fn never() -> bool { return never(); } "
                  "fn main() -> i32 { return if true || never() { 42 } else { 0 }; }")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "short_circuit.ocl"
            path.write_text(source, encoding="utf-8")
            executable = Path(directory) / ("short_circuit.exe" if os.name == "nt" else "short_circuit")
            result = subprocess.run([sys.executable, str(ROOT / "oclc.py"), "build", str(path), "-o", str(executable)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(run_executable(executable, timeout=5), 42)

    def test_unary_not_requires_bool(self):
        self.assertDiagnostic("fn main() -> i32 { return if !1 { 42 } else { 0 }; }", "E0211", "bool operand")

    def test_excessive_unary_nesting_is_a_diagnostic(self):
        source = "fn main() -> i32 { return if " + "!" * 5000 + "false { 42 } else { 0 }; }"
        self.assertDiagnostic(source, "E0101", "nested too deeply")

    def test_logical_operators_require_bool(self):
        self.assertDiagnostic("fn main() -> i32 { return if true && 1 { 42 } else { 0 }; }", "E0211", "bool operands")

    def test_assignment_requires_var(self):
        self.assertDiagnostic("fn main() -> i32 { let x: i32 = 1; x = 2; return x; }", "E0215", "immutable")

    def test_assignment_type_must_match(self):
        self.assertDiagnostic("fn main() -> i32 { var x: i32 = 1; x = false; return x; }", "E0214", "expects i32")

    def test_while_condition_must_be_bool(self):
        self.assertDiagnostic("fn main() -> i32 { while 1 { return 1; } return 42; }", "E0212", "while condition")

    def test_block_local_is_not_visible_after_block(self):
        self.assertDiagnostic("fn main() -> i32 { { let x: i32 = 42; } return x; }", "E0206", "unknown identifier")

    def test_shadowing_remains_forbidden_in_nested_blocks(self):
        self.assertDiagnostic("fn main() -> i32 { let x: i32 = 1; { let x: i32 = 2; } return x; }", "E0210", "no shadowing")

    def test_early_return_in_block_terminates_function(self):
        _, ir = compile_source("fn main() -> i32 { { return 42; } }")
        self.assertIn("ret i32 42", ir)

    def test_function_fallthrough_is_rejected(self):
        self.assertDiagnostic("fn main() -> i32 { var x: i32 = 1; }", "E0202", "without returning")

    def test_excessive_block_nesting_is_a_diagnostic(self):
        source = "fn main() -> i32 { " + "{" * 5000 + "return 42;" + "}" * 5000 + " }"
        self.assertDiagnostic(source, "E0102", "block is nested too deeply")

    def test_var_initializer_is_actually_stored(self):
        # Dropping the initializer store leaves the slot undef. The acceptance
        # program still returned 42 under that defect because the stack happened
        # to read as zero, so this asserts the store in the IR and picks a
        # non-zero value natively where undef cannot pass by luck.
        _, ir = compile_source("fn main() -> i32 { var x: i32 = 7; return x; }")
        self.assertIn("  store i32 7, ptr %ocl.var.x", ir)

    def test_var_initial_value_survives_to_runtime(self):
        require_clang()
        source = "fn main() -> i32 { var x: i32 = 42; return x; }"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "init.ocl"
            path.write_text(source, encoding="utf-8")
            executable = Path(directory) / ("init.exe" if os.name == "nt" else "init")
            result = subprocess.run(
                [sys.executable, str(ROOT / "oclc.py"), "build", str(path), "-o", str(executable)],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(run_executable(executable), 42)

    def test_sibling_blocks_cannot_reuse_a_name(self):
        # Function-wide uniqueness is what keeps generated `alloca` names unique.
        # Allowing sibling reuse emits two slots called %ocl.var.x, which LLVM
        # rejects as "multiple definition of local value".
        self.assertDiagnostic(
            "fn main() -> i32 { { var x: i32 = 1; } { var x: i32 = 2; } return 42; }",
            "E0210", "no shadowing",
        )

    def test_return_inside_a_loop_does_not_satisfy_the_function(self):
        # A while body may run zero times, so its return cannot discharge the
        # function's obligation. Treating it as one lets the program through to
        # codegen, which then raises an internal compiler error.
        self.assertDiagnostic(
            "fn main() -> i32 { while true { return 42; } }",
            "E0202", "can reach the end without returning",
        )

    def test_returning_loop_body_emits_no_back_edge(self):
        _, ir = compile_source(
            "fn f(go: bool) -> i32 { while go { return 7; } return 42; }"
            "fn main() -> i32 { return f(false); }"
        )
        body = ir.split("ocl.while.body.0:")[1].split("ocl.while.exit.0:")[0]
        self.assertIn("ret i32 7", body)
        self.assertNotIn("br label %ocl.while.condition.0", body)

    def test_native_repetition_returns_42(self):
        require_clang()
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / ("repeat.exe" if os.name == "nt" else "repeat")
            result = subprocess.run([sys.executable, str(ROOT / "oclc.py"), "build", str(ROOT / "examples" / "repeat.ocl"), "-o", str(executable)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(run_executable(executable), 42)


class Ocl06Tests(DiagnosticAssertions):
    SOURCE = (ROOT / "examples" / "loop_control.ocl").read_text(encoding="utf-8")

    def test_loop_control_ast(self):
        program, _ = compile_source(self.SOURCE)
        outer = program.functions[0].body[2]
        self.assertIsInstance(outer, WhileStatement)
        inner = outer.body.statements[1]
        self.assertIsInstance(inner.body.statements[0], BreakStatement)
        self.assertIsInstance(outer.body.statements[2], ContinueStatement)

    def test_nested_loop_control_targets_the_nearest_loop(self):
        _, ir = compile_source(self.SOURCE)
        inner_body = ir.split("ocl.while.body.1:")[1].split("ocl.while.exit.1:")[0]
        self.assertIn("br label %ocl.while.exit.1", inner_body)
        outer_after_inner = ir.split("ocl.while.exit.1:")[1].split("ocl.while.exit.0:")[0]
        self.assertIn("br label %ocl.while.condition.0", outer_after_inner)

    def test_break_and_continue_require_a_loop(self):
        self.assertDiagnostic("fn main() -> i32 { break; }", "E0218", "only valid inside while")
        self.assertDiagnostic("fn main() -> i32 { continue; }", "E0218", "only valid inside while")

    def test_statement_after_loop_control_is_unreachable(self):
        self.assertDiagnostic("fn main() -> i32 { while true { break; return 1; } return 42; }", "E0216", "after break")
        self.assertDiagnostic("fn main() -> i32 { while true { continue; return 1; } return 42; }", "E0216", "after continue")

    def test_unary_minus_requires_i32(self):
        self.assertDiagnostic("fn main() -> i32 { return if -true { 1 } else { 0 }; }", "E0211", "i32 operand")

    def test_i32_min_literal_is_supported(self):
        _, ir = compile_source("fn main() -> i32 { return -2147483648; }")
        self.assertIn("ret i32 -2147483648", ir)

    def test_too_negative_literal_is_rejected(self):
        self.assertDiagnostic("fn main() -> i32 { return -2147483649; }", "E0203", "does not fit")

    def test_literal_zero_divisors_are_rejected(self):
        self.assertDiagnostic("fn main() -> i32 { return 42 / 0; }", "E0217", "division by zero")
        self.assertDiagnostic("fn main() -> i32 { return 42 % -0; }", "E0217", "division by zero")

    def test_division_and_remainder_emit_guarded_signed_operations(self):
        _, ir = compile_source("fn f(x: i32, y: i32) -> i32 { return x / y + x % y; } fn main() -> i32 { return f(84, 2); }")
        self.assertEqual(ir.count("call void @llvm.trap()"), 2)
        self.assertIn("sdiv i32", ir)
        self.assertIn("srem i32", ir)
        self.assertIn("icmp eq i32 %y, 0", ir)
        self.assertIn("icmp eq i32 %x, -2147483648", ir)

    def test_computed_zero_and_min_overflow_take_the_trap_path(self):
        for expression in ("42 / zero(0)", "-2147483648 / -1", "-2147483648 % -1"):
            with self.subTest(expression=expression):
                source = f"fn zero(x: i32) -> i32 {{ return x; }} fn main() -> i32 {{ return {expression}; }}"
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "trap.ocl"
                    path.write_text(source, encoding="utf-8")
                    executable = Path(directory) / ("trap.exe" if os.name == "nt" else "trap")
                    result = subprocess.run([sys.executable, str(ROOT / "oclc.py"), "build", str(path), "-o", str(executable)], capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    assert_deterministic_trap(self, run_executable(executable, timeout=5))

    def _build_and_run(self, source: str, name: str = "case") -> int:
        require_clang()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / f"{name}.ocl"
            path.write_text(source, encoding="utf-8")
            executable = Path(directory) / (f"{name}.exe" if os.name == "nt" else name)
            result = subprocess.run(
                [sys.executable, str(ROOT / "oclc.py"), "build", str(path), "-o", str(executable)],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return run_executable(executable, timeout=10)

    def test_dividing_by_minus_one_is_not_a_trap(self):
        # Only i32::MIN / -1 overflows. Widening the overflow test to "either
        # operand matches" makes every division by -1 trap, which breaks working
        # arithmetic rather than protecting it.
        self.assertEqual(
            self._build_and_run(
                "fn neg(x: i32) -> i32 { return x; } "
                "fn main() -> i32 { return (-42) / neg(-1); }", "divneg"), 42)
        self.assertEqual(
            self._build_and_run(
                "fn neg(x: i32) -> i32 { return x; } "
                "fn main() -> i32 { return 42 + ((-84) % neg(-1)); }", "remneg"), 42)

    def test_minimum_divided_by_one_is_not_a_trap(self):
        # The guard must key on the pair, not on i32::MIN alone.
        self.assertEqual(
            self._build_and_run(
                "fn id(x: i32) -> i32 { return x; } "
                "fn main() -> i32 { return ((-2147483648) / id(1)) + 2147483647 + 43; }", "minone"), 42)

    def test_division_inside_a_branch_keeps_the_phi_predecessor(self):
        # After the guard, the value is produced in the division's safe block,
        # not in the branch's own block. A phi naming the branch label is IR
        # LLVM rejects with "PHI node entries do not match predecessors!".
        _, ir = compile_source(
            "fn f(a: bool, x: i32, y: i32) -> i32 { return if a { x / y } else { 0 }; } "
            "fn main() -> i32 { return f(true, 84, 2); }")
        phi = next(line.strip() for line in ir.splitlines() if "= phi " in line)
        self.assertIn("%ocl.division.safe.", phi)
        self.assertNotIn("%ocl.if.then.", phi)

    def test_division_inside_a_branch_runs_natively(self):
        self.assertEqual(
            self._build_and_run(
                "fn f(a: bool, x: i32, y: i32) -> i32 { return if a { x / y } else { 0 }; } "
                "fn main() -> i32 { return f(true, 84, 2); }", "divbranch"), 42)

    def test_unary_minus_negates_a_runtime_value(self):
        # Literal negation is folded in the parser, so only a computed operand
        # exercises the codegen path.
        _, ir = compile_source("fn f(x: i32) -> i32 { return -x; } fn main() -> i32 { return 42; }")
        self.assertIn("sub i32 0, %x", ir)
        self.assertEqual(
            self._build_and_run(
                "fn main() -> i32 { var a: i32 = 5; return (-a) + 47; }", "negrt"), 42)

    def test_signed_division_and_remainder_run_natively(self):
        require_clang()
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / ("loop_control.exe" if os.name == "nt" else "loop_control")
            result = subprocess.run([sys.executable, str(ROOT / "oclc.py"), "build", str(ROOT / "examples" / "loop_control.ocl"), "-o", str(executable)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(run_executable(executable), 42)


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
        self.assertEqual(caught.exception.code, "E0216")

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


class ExpressionDepthTests(DiagnosticAssertions):
    """H1: valid expressions must never reach a Python RecursionError."""

    def test_long_addition_chain_compiles(self):
        # Addition chains parse iteratively but build a deep tree that semantic
        # analysis and lowering have to walk without recursing.
        terms = 5000
        _, ir = compile_source("fn main() -> i32 { return " + "+".join(["1"] * terms) + "; }")
        self.assertEqual(ir.count("add i32"), terms - 1)

    def test_very_long_addition_chain_still_compiles(self):
        terms = 20000
        _, ir = compile_source("fn main() -> i32 { return " + "+".join(["1"] * terms) + "; }")
        self.assertEqual(ir.count("add i32"), terms - 1)

    def test_very_long_multiplication_chain_still_compiles(self):
        terms = 20000
        _, ir = compile_source("fn main() -> i32 { return " + "*".join(["1"] * terms) + "; }")
        self.assertEqual(ir.count("mul i32"), terms - 1)

    def test_deep_parentheses_within_the_limit_compile(self):
        depth = 200
        source = "fn main() -> i32 { return " + "(" * depth + "42" + ")" * depth + "; }"
        _, ir = compile_source(source)
        self.assertIn("ret i32 42", ir)

    def test_the_documented_depth_limit_is_exactly_the_boundary(self):
        # The limit is a published number, so both sides of it are asserted.
        limit = parser_module.MAX_EXPRESSION_DEPTH
        at_limit = "fn main() -> i32 { return " + "(" * (limit - 1) + "42" + ")" * (limit - 1) + "; }"
        _, ir = compile_source(at_limit)
        self.assertIn("ret i32 42", ir)
        past_limit = "fn main() -> i32 { return " + "(" * limit + "42" + ")" * limit + "; }"
        self.assertDiagnostic(past_limit, "E0101", "nested too deeply")

    def test_max_depth_expression_survives_a_deep_caller_stack(self):
        # The guard promises a diagnostic rather than a RecursionError. That
        # promise depends on Python frames still being available, so it must hold
        # when the compiler is embedded rather than only at the CLI's shallow
        # baseline stack.
        limit = parser_module.MAX_EXPRESSION_DEPTH
        source = "fn main() -> i32 { return " + "(" * (limit - 1) + "42" + ")" * (limit - 1) + "; }"

        def deepen(remaining, action):
            if remaining == 0:
                return action()
            return deepen(remaining - 1, action)

        previous_limit = sys.getrecursionlimit()
        for caller_depth in (0, 200, 400):
            with self.subTest(caller_depth=caller_depth):
                _, ir = deepen(caller_depth, lambda: compile_source(source))
                self.assertIn("ret i32 42", ir)
                self.assertEqual(sys.getrecursionlimit(), previous_limit)

    def test_frames_per_nesting_level_matches_the_parser(self):
        # FRAMES_PER_LEVEL is what reserves enough stack for the documented
        # depth bound. Adding a precedence tier changes it, and a stale value
        # would quietly shrink the guard's margin until it stopped working.
        def peak_frames(depth, form):
            if form == "parentheses":
                expression = "(" * depth + "42" + ")" * depth
            else:
                expression = "if true { " * depth + "42" + " } else { 0 }" * depth
            source = "fn main() -> i32 { return " + expression + "; }"
            peak = [0]

            def probe(frame, event, arg):
                if event == "call":
                    current, walker = 0, frame
                    while walker is not None:
                        current += 1
                        walker = walker.f_back
                    peak[0] = max(peak[0], current)
                return None

            sys.setprofile(probe)
            try:
                compile_source(source)
            finally:
                sys.setprofile(None)
            return peak[0]

        # The slope between two depths, so the fixed cost of the driver and the
        # enclosing function does not distort the per-level figure.
        near, far = 40, 80
        for form in ("parentheses", "if"):
            with self.subTest(form=form):
                slope = (peak_frames(far, form) - peak_frames(near, form)) / (far - near)
                self.assertLessEqual(
                    slope, parser_module.FRAMES_PER_LEVEL,
                    f"{form} parsing costs {slope:.2f} frames per nesting level but "
                    f"FRAMES_PER_LEVEL is {parser_module.FRAMES_PER_LEVEL}; raise it so "
                    "the depth guard keeps reserving enough stack",
                )

    def test_frames_per_block_level_matches_the_statement_walkers(self):
        # The sibling of the expression-frames test, for the recursive statement
        # walks. A stale constant would shrink the reservation those walks depend
        # on until deeply blocked source became a RecursionError again.
        def peak_frames(depth, phase):
            source = "fn main() -> i32 { " + "{ " * depth + "return 42; " + "} " * depth + "}"
            program = parse(lex(source), source)
            peak = [0]

            def probe(frame, event, arg):
                if event == "call":
                    current, walker = 0, frame
                    while walker is not None:
                        current += 1
                        walker = walker.f_back
                    peak[0] = max(peak[0], current)
                return None

            sys.setprofile(probe)
            try:
                phase(program, source)
            finally:
                sys.setprofile(None)
            return peak[0]

        phases = {
            "semantic": lambda program, source: analyze(program, source),
            "codegen": lambda program, source: generate_llvm_ir(program),
        }
        near, far = 40, 80
        for name, phase in phases.items():
            with self.subTest(phase=name):
                slope = (peak_frames(far, phase) - peak_frames(near, phase)) / (far - near)
                self.assertLessEqual(
                    slope, parser_module.FRAMES_PER_BLOCK_LEVEL,
                    f"{name} costs {slope:.2f} frames per block level but "
                    f"FRAMES_PER_BLOCK_LEVEL is {parser_module.FRAMES_PER_BLOCK_LEVEL}; "
                    "raise it so the statement walks keep reserving enough stack",
                )

    def test_deep_blocks_survive_a_deep_caller_stack(self):
        # The parser reserves stack for expression nesting, but that reservation
        # is released when parsing ends. The statement walks run afterwards and
        # must reserve their own, or block nesting at the documented limit becomes
        # a RecursionError for an embedded caller.
        depth = parser_module.MAX_BLOCK_DEPTH - 1
        source = "fn main() -> i32 { " + "{ " * depth + "return 42; " + "} " * depth + "}"

        def deepen(remaining, action):
            if remaining == 0:
                return action()
            return deepen(remaining - 1, action)

        # 800 is past the point where the unreserved walks exhaust the default
        # limit, so this fails if either reservation is removed.
        for caller_depth in (0, 400, 800):
            with self.subTest(caller_depth=caller_depth):
                _, ir = deepen(caller_depth, lambda: compile_source(source))
                self.assertIn("ret i32 42", ir)

    def test_excessive_nesting_diagnoses_from_a_deep_caller_stack(self):
        source = "fn main() -> i32 { return " + "(" * 5000 + "42" + ")" * 5000 + "; }"

        def deepen(remaining, action):
            if remaining == 0:
                return action()
            return deepen(remaining - 1, action)

        with self.assertRaises(DiagnosticError) as caught:
            deepen(400, lambda: compile_source(source))
        self.assertEqual(caught.exception.code, "E0101")

    def test_recursion_limit_is_restored_after_a_diagnostic(self):
        # Force the reservation branch, then prove finally restores the exact
        # prior value when parsing exits through E0101 rather than success.
        original_limit = sys.getrecursionlimit()
        forced_limit = 200
        source = "fn main() -> i32 { return " + "(" * 5000 + "42" + ")" * 5000 + "; }"
        try:
            sys.setrecursionlimit(forced_limit)
            with self.assertRaises(DiagnosticError) as caught:
                compile_source(source)
            self.assertEqual(caught.exception.code, "E0101")
            self.assertEqual(sys.getrecursionlimit(), forced_limit)
        finally:
            sys.setrecursionlimit(original_limit)

    def test_overlapping_parse_calls_are_serialized(self):
        # Parser.parse is held while the recursion limit may differ from its
        # baseline. A second thread must not enter until the first releases it,
        # or their save/restore operations could interleave.
        first_entered = threading.Event()
        second_started = threading.Event()
        second_entered = threading.Event()
        release_first = threading.Event()
        invocation = [0]
        invocation_lock = threading.Lock()
        errors: list[BaseException] = []
        original_parse = parser_module.Parser.parse

        def controlled_parse(parser):
            with invocation_lock:
                invocation[0] += 1
                position = invocation[0]
            if position == 1:
                first_entered.set()
                if not release_first.wait(5):
                    raise AssertionError("timed out waiting to release first parse")
            else:
                second_entered.set()
            return original_parse(parser)

        def compile_in_thread(started=None):
            if started is not None:
                started.set()
            try:
                compile_source(VALID)
            except BaseException as error:
                errors.append(error)

        with mock.patch.object(parser_module.Parser, "parse", controlled_parse):
            first = threading.Thread(target=compile_in_thread)
            second = threading.Thread(target=compile_in_thread, args=(second_started,))
            first.start()
            self.assertTrue(first_entered.wait(5), "first parse never entered")
            second.start()
            self.assertTrue(second_started.wait(5), "second compiler call never started")
            self.assertFalse(second_entered.wait(1), "overlapping parse entered while the first held the reservation")
            release_first.set()
            first.join(5)
            second.join(5)

        self.assertFalse(first.is_alive() or second.is_alive(), "compiler thread did not finish")
        self.assertEqual(errors, [])
        self.assertTrue(second_entered.is_set(), "second parse never ran after the first completed")

    def test_recursion_limit_lock_is_reentrant(self):
        # A future nested same-thread parse must not deadlock. This also catches
        # an accidental downgrade from RLock to Lock without needing to hang.
        lock = parser_module._RECURSION_LIMIT_LOCK
        self.assertTrue(lock.acquire(timeout=1))
        nested = False
        try:
            nested = lock.acquire(blocking=False)
            self.assertTrue(nested, "parser recursion-limit lock must be reentrant")
        finally:
            if nested:
                lock.release()
            lock.release()

    def test_excessive_parentheses_are_a_diagnostic_not_a_crash(self):
        depth = 5000
        source = "fn main() -> i32 { return " + "(" * depth + "42" + ")" * depth + "; }"
        self.assertDiagnostic(source, "E0101", "nested too deeply")

    def test_deeply_nested_calls_within_the_limit_compile(self):
        depth = 200
        source = ("fn id(a: i32) -> i32 { return a; }\nfn main() -> i32 { return "
                  + "id(" * depth + "1" + ")" * depth + "; }\n")
        _, ir = compile_source(source)
        self.assertEqual(ir.count("call i32 @id"), depth)

    def test_excessive_nesting_is_a_diagnostic_not_a_crash(self):
        depth = 5000
        source = ("fn id(a: i32) -> i32 { return a; }\nfn main() -> i32 { return "
                  + "id(" * depth + "1" + ")" * depth + "; }\n")
        self.assertDiagnostic(source, "E0101", "nested too deeply")

    def test_deep_nesting_through_the_cli_reports_cleanly(self):
        # The user-visible failure must be a diagnostic, never a traceback.
        depth = 5000
        source = ("fn id(a: i32) -> i32 { return a; }\nfn main() -> i32 { return "
                  + "id(" * depth + "1" + ")" * depth + "; }\n")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "deep.ocl"
            path.write_text(source, encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(ROOT / "oclc.py"), "check", str(path)],
                capture_output=True, text=True,
            )
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn("RecursionError", result.stderr)
        self.assertIn("error[E0101]", result.stderr)

    def test_long_chain_through_the_cli_does_not_crash(self):
        source = "fn main() -> i32 { return " + "+".join(["1"] * 5000) + "; }"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chain.ocl"
            path.write_text(source, encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(ROOT / "oclc.py"), "check", str(path)],
                capture_output=True, text=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Traceback", result.stderr)


class GeneratedIdentifierTests(unittest.TestCase):
    """M1: compiler-generated LLVM names must not collide with user identifiers."""

    RESERVED_LIKE = ("entry", "ocl", "add", "main", "i32", "v", "t0")

    def test_parameter_named_entry_lowers_to_valid_ir(self):
        _, ir = compile_source("fn f(entry: i32) -> i32 { return entry; }\nfn main() -> i32 { return f(42); }\n")
        self.assertIn("i32 %entry", ir)

    def test_generated_block_label_is_namespaced_away_from_user_names(self):
        # The entry block must live in a namespace no OCL identifier can reach.
        _, ir = compile_source(VALID)
        label = next(line for line in ir.splitlines() if line.endswith(":"))
        self.assertIn(".", label, "generated label must use a '.' the OCL lexer cannot produce")

    def test_no_user_identifier_can_collide_with_a_generated_name(self):
        for name in self.RESERVED_LIKE:
            with self.subTest(name=name):
                source = (f"fn f({name}: i32) -> i32 {{ return {name}; }}\n"
                          f"fn main() -> i32 {{ return f(42); }}\n")
                _, ir = compile_source(source)
                self.assertIn(f"i32 %{name}", ir)


class EvaluationOrderTests(unittest.TestCase):
    """Operands lower left to right, and the IR must show it."""

    def test_sibling_calls_are_emitted_left_to_right(self):
        # Leaf operands emit no instructions, so only sibling calls can reveal
        # the order. It is a semantic commitment the moment anything has effects.
        source = ("fn l(a: i32) -> i32 { return a; }\n"
                  "fn r(a: i32) -> i32 { return a; }\n"
                  "fn main() -> i32 { return l(1) + r(2); }\n")
        _, ir = compile_source(source)
        body = [line.strip() for line in ir.splitlines() if "call" in line or "add" in line]
        self.assertEqual(body, [
            "%0 = call i32 @l(i32 1)",
            "%1 = call i32 @r(i32 2)",
            "%2 = add i32 %0, %1",
        ])

    def test_call_arguments_are_emitted_left_to_right(self):
        source = ("fn l(a: i32) -> i32 { return a; }\n"
                  "fn r(a: i32) -> i32 { return a; }\n"
                  "fn two(a: i32, b: i32) -> i32 { return a + b; }\n"
                  "fn main() -> i32 { return two(l(1), r(2)); }\n")
        _, ir = compile_source(source)
        body = [line.strip() for line in ir.splitlines() if "call" in line]
        self.assertEqual(body, [
            "%0 = call i32 @l(i32 1)",
            "%1 = call i32 @r(i32 2)",
            "%2 = call i32 @two(i32 %0, i32 %1)",
        ])

    def test_local_initializers_are_emitted_in_source_order(self):
        source = ("fn id(a: i32) -> i32 { return a; }\n"
                  "fn main() -> i32 { let first: i32 = id(1); "
                  "let second: i32 = id(2); return first + second + 39; }\n")
        _, ir = compile_source(source)
        calls = [line.strip() for line in ir.splitlines() if "call i32 @id" in line]
        self.assertEqual(calls, ["%0 = call i32 @id(i32 1)", "%1 = call i32 @id(i32 2)"])


class UnknownNodeTests(unittest.TestCase):
    """I2: both passes must fail as an internal error, never a bare exception."""

    @dataclass(frozen=True)
    class _Unsupported:
        location: SourceLocation

    def _program(self):
        location = SourceLocation(0, 1, 1)
        node = self._Unsupported(location)
        return Program((Function("main", "i32", (ReturnStatement(node, location),), location),))

    def test_semantic_rejects_an_unknown_node_as_an_internal_error(self):
        with self.assertRaises(InternalCompilerError):
            analyze(self._program(), "")

    def test_codegen_rejects_an_unknown_node_as_an_internal_error(self):
        with self.assertRaises(InternalCompilerError):
            generate_llvm_ir(self._program())


class OverflowSemanticsTests(unittest.TestCase):
    """M2: i32 arithmetic is defined as two's-complement wrapping."""

    def test_addition_does_not_request_poison_on_overflow(self):
        # nsw/nuw would make overflow undefined; OCL defines it as wrapping.
        _, ir = compile_source("fn main() -> i32 { return 2147483647 + 1; }")
        self.assertIn("add i32 2147483647, 1", ir)
        self.assertNotIn("nsw", ir)
        self.assertNotIn("nuw", ir)

    def test_overflowing_addition_is_accepted(self):
        program, _ = compile_source("fn main() -> i32 { return 2147483647 + 1; }")
        self.assertEqual(len(program.functions), 1)

    def test_subtraction_and_multiplication_do_not_request_poison(self):
        _, ir = compile_source("fn main() -> i32 { return (0 - 1) * 2147483647; }")
        self.assertIn("sub i32 0, 1", ir)
        self.assertIn("mul i32", ir)
        self.assertNotIn("nsw", ir)
        self.assertNotIn("nuw", ir)

    def test_literal_boundary_is_still_enforced(self):
        # Wrapping applies to arithmetic, not to literals, which stay bounded.
        _, ir = compile_source("fn main() -> i32 { return 2147483647; }")
        self.assertIn("ret i32 2147483647", ir)
        with self.assertRaises(DiagnosticError) as caught:
            compile_source("fn main() -> i32 { return 2147483648; }")
        self.assertEqual(caught.exception.code, "E0203")


class NativeTests(unittest.TestCase):
    def test_wrapping_arithmetic_produces_the_documented_value(self):
        # M2 boundary test. 2147483647 + 2147483647 wraps to -2, and -2 + 44 is
        # 42, so the documented semantics are observable as a real exit code on
        # every platform rather than only in the IR.
        require_clang()
        source = "fn main() -> i32 { return 2147483647 + 2147483647 + 44; }\n"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wrap.ocl"
            path.write_text(source, encoding="utf-8")
            executable = Path(directory) / ("wrap.exe" if os.name == "nt" else "wrap")
            result = subprocess.run(
                [sys.executable, str(ROOT / "oclc.py"), "build", str(path), "-o", str(executable)],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(run_executable(executable), 42)

    def test_parameter_named_entry_builds_and_runs(self):
        require_clang()
        source = "fn f(entry: i32) -> i32 { return entry; }\nfn main() -> i32 { return f(42); }\n"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "entry.ocl"
            path.write_text(source, encoding="utf-8")
            executable = Path(directory) / ("entry.exe" if os.name == "nt" else "entry")
            result = subprocess.run(
                [sys.executable, str(ROOT / "oclc.py"), "build", str(path), "-o", str(executable)],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(run_executable(executable), 42)

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
            self.assertEqual(run_executable(executable), 42)


if __name__ == "__main__":
    unittest.main()
