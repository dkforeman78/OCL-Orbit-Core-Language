"""Tests for the shared native-test helper itself.

Every native claim in the suite is made through `native_support`: which builds
run, whether they are bounded, and whether a nonzero exit was the deliberate
trap or raw undefined behaviour. Nothing else checks that this module keeps its
own promises, so a helper that quietly stopped honouring them would leave the
whole native suite green while verifying much less than it appears to.
"""

import ctypes
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

import native_support
from compiler import cli
from native_support import (
    assert_deterministic_trap,
    build_and_run,
    require_clang,
    run_executable,
)

VALID = "fn main() -> i32 {\n    return 42;\n}\n"


def _clang_present():
    return mock.patch.object(cli, "_clang", return_value="clang")


class BuildModeForwardingTests(unittest.TestCase):
    def test_build_and_run_forwards_exactly_the_requested_mode(self):
        # The dual-mode execution suite asks this helper for one mode and then
        # the other. If the argument were dropped or inverted, every "both
        # modes" test would still pass while covering a single mode twice.
        for release in (False, True):
            with self.subTest(release=release):
                with _clang_present(), mock.patch.object(native_support.subprocess, "run") as run:
                    run.side_effect = [
                        subprocess.CompletedProcess([], 0, "", ""),
                        subprocess.CompletedProcess([], 42),
                    ]
                    self.assertEqual(build_and_run(self, VALID, "mode", release=release), 42)
                build_command = run.call_args_list[0].args[0]
                self.assertEqual("--release" in build_command, release)
                self.assertEqual(build_command[2], "build")

    def test_a_failed_build_fails_the_calling_test(self):
        # Without this the helper would go on to run whatever is at the output
        # path — nothing, or a binary left by an earlier case.
        with _clang_present(), mock.patch.object(
            native_support.subprocess, "run",
            return_value=subprocess.CompletedProcess([], 1, "", "clang said no"),
        ):
            with self.assertRaises(AssertionError) as caught:
                build_and_run(self, VALID, "broken")
        self.assertIn("clang said no", str(caught.exception))


class ExecutionBoundTests(unittest.TestCase):
    def test_every_native_run_is_bounded(self):
        with _clang_present(), mock.patch.object(native_support.subprocess, "run") as run:
            run.side_effect = [
                subprocess.CompletedProcess([], 0, "", ""),
                subprocess.CompletedProcess([], 42),
            ]
            build_and_run(self, VALID, "bounded")
        self.assertIsNotNone(
            run.call_args_list[1].kwargs.get("timeout"),
            "the executable was run without a timeout; a lowering defect that "
            "produces an infinite loop would hang the suite instead of failing it",
        )

    def test_a_timeout_becomes_a_failure_naming_the_program(self):
        with mock.patch.object(native_support.subprocess, "run",
                               side_effect=subprocess.TimeoutExpired("prog", 1)):
            with self.assertRaises(AssertionError) as caught:
                run_executable(Path("looping.exe"), timeout=1)
        self.assertIn("looping.exe", str(caught.exception))
        self.assertIn("did not terminate", str(caught.exception))

    @unittest.skipUnless(os.name == "nt", "process error mode is a Windows concept")
    def test_the_windows_error_mode_is_restored(self):
        # Error mode is process-global and inherited. Leaving it changed would
        # silently alter how every later test's child process reports a crash.
        kernel32 = ctypes.windll.kernel32
        original = kernel32.GetErrorMode()
        try:
            kernel32.SetErrorMode(0x0001)
            with mock.patch.object(native_support.subprocess, "run",
                                   return_value=subprocess.CompletedProcess([], 0)):
                run_executable(Path("ignored.exe"))
            self.assertEqual(kernel32.GetErrorMode(), 0x0001)
        finally:
            kernel32.SetErrorMode(original)


class TrapSignatureTests(unittest.TestCase):
    def test_native_trap_signature_in_both_modes(self):
        source = ('fn divide(x: i32) -> i32 { return 1 / x; } '
                  'fn main() -> i32 { return divide(0); }')
        for release in (False, True):
            result = build_and_run(self, source, 'trap_signature', release=release)
            print(f'TRAP_SIGNATURE platform={sys.platform} release={release} exit={result}', flush=True)
            assert_deterministic_trap(self, result)

    def test_the_documented_trap_signature_is_accepted(self):
        for signature in native_support._TRAP_EXITS:
            with self.subTest(signature=signature):
                assert_deterministic_trap(self, signature)

    def test_a_raw_arithmetic_fault_is_rejected_as_undefined_behaviour(self):
        # This is the distinction the trap policy rests on: a defeated guard
        # lets the operands reach the raw division and the hardware faults,
        # which is still a nonzero exit.
        for signature in native_support._UB_FAULTS:
            with self.subTest(signature=signature):
                with self.assertRaises(self.failureException) as caught:
                    assert_deterministic_trap(self, signature)
                self.assertIn("undefined behaviour", str(caught.exception))

    def test_an_ordinary_nonzero_exit_is_not_a_trap(self):
        for code in (1, 42, 3):
            with self.subTest(code=code):
                with self.assertRaises(self.failureException):
                    assert_deterministic_trap(self, code)


class ClangRequirementTests(unittest.TestCase):
    def test_a_missing_toolchain_escalates_when_ci_demands_one(self):
        with mock.patch.object(cli, "_clang", return_value=None):
            with mock.patch.dict(os.environ, {"OCL_REQUIRE_CLANG": "1"}):
                try:
                    require_clang()
                except AssertionError:
                    pass
                except unittest.SkipTest:
                    self.fail("OCL_REQUIRE_CLANG did not turn a missing toolchain into a "
                              "failure; Clang-dependent coverage can vanish from a green run")
                else:
                    self.fail("require_clang accepted a missing toolchain")
            without = {k: v for k, v in os.environ.items() if k != "OCL_REQUIRE_CLANG"}
            with mock.patch.dict(os.environ, without, clear=True):
                with self.assertRaises(unittest.SkipTest):
                    require_clang()

    def test_a_present_toolchain_is_not_skipped(self):
        with _clang_present():
            require_clang()


if __name__ == "__main__":
    unittest.main()
