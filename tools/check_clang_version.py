"""Verify that the Clang oclc will actually invoke meets the documented floor.

CI runs this after locating Clang. It checks the same binary `oclc build` would
resolve, so a runner image that quietly changes or loses its toolchain fails the
build instead of silently weakening the acceptance test.

Apple Clang carries Apple's own version numbering rather than upstream LLVM's, so
the two are held to separate, separately documented floors.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from compiler.cli import _clang  # noqa: E402

LLVM_MINIMUM = 18
APPLE_MINIMUM = 15

_VERSION = re.compile(
    r"(?P<vendor>[^\n]*?)\bclang version\s+(?P<major>\d+)(?:\.(?P<minor>\d+))?",
    re.IGNORECASE,
)


class ToolchainError(Exception):
    """The located Clang is unusable or too old."""


def parse_clang_version(text: str) -> tuple[str, int, int]:
    """Return (vendor, major, minor) from `clang --version` output.

    Vendor is "apple" for Apple Clang and "llvm" for everything else, including
    distribution builds such as Ubuntu's, which follow upstream numbering.
    """
    match = _VERSION.search(text)
    if not match:
        raise ToolchainError(f"could not parse a Clang version from: {text.strip()[:120]!r}")
    vendor = "apple" if "apple" in match.group("vendor").lower() else "llvm"
    return vendor, int(match.group("major")), int(match.group("minor") or 0)


def minimum_for(vendor: str) -> int:
    return APPLE_MINIMUM if vendor == "apple" else LLVM_MINIMUM


def check_version_text(text: str) -> str:
    """Validate `clang --version` output, returning a human-readable summary."""
    vendor, major, minor = parse_clang_version(text)
    floor = minimum_for(vendor)
    label = "Apple Clang" if vendor == "apple" else "LLVM Clang"
    if major < floor:
        raise ToolchainError(
            f"{label} {major}.{minor} is older than the required {label} {floor}. "
            "Update the toolchain, or update the documented floor if this is intended."
        )
    return f"{label} {major}.{minor} satisfies the {label} {floor} minimum"


def main() -> int:
    clang = _clang()
    if not clang:
        print("error: Clang was not found; oclc cannot build native executables", file=sys.stderr)
        return 1
    try:
        result = subprocess.run([clang, "--version"], capture_output=True, text=True)
    except OSError as error:
        print(f"error: could not run {clang}: {error}", file=sys.stderr)
        return 1
    if result.returncode:
        print(f"error: {clang} --version failed:\n{result.stderr}", file=sys.stderr)
        return 1
    print(f"using {clang}")
    print(result.stdout.strip().splitlines()[0] if result.stdout.strip() else "(no version output)")
    try:
        print(check_version_text(result.stdout))
    except ToolchainError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
