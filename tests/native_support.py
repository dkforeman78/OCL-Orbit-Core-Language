import ctypes
import os
import signal
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

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
    # Observed under both -O0 and -O2 on the supported hosted runners:
    # macOS reports SIGTRAP; Ubuntu reports SIGILL. Do not accept arbitrary
    # aborts as deliberate traps. A different host must validate its signature.
    _TRAP_EXITS = {-signal.SIGTRAP} if sys.platform == "darwin" else {-signal.SIGILL}
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


def build_and_run(case, source: str, name: str = "case", *, release: bool = False) -> int:
    require_clang()
    with tempfile.TemporaryDirectory(prefix="ocl native ") as directory:
        path = Path(directory) / f"{name}.ocl"
        path.write_text(source, encoding="utf-8")
        executable = Path(directory) / (f"{name}.exe" if os.name == "nt" else name)
        command = [sys.executable, str(ROOT / "oclc.py"), "build", str(path), "-o", str(executable)]
        if release:
            command.append("--release")
        result = subprocess.run(command, capture_output=True, text=True, timeout=60)
        case.assertEqual(result.returncode, 0, result.stderr)
        return run_executable(executable, timeout=10)

