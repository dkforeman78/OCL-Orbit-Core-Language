from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .diagnostics import DiagnosticError, InternalCompilerError
from .driver import compile_source


def _clang() -> str | None:
    configured = os.environ.get("OCL_CLANG")
    if configured:
        # An explicit pin must not silently fall back to a different toolchain.
        if not Path(configured).is_file():
            raise ValueError(f"OCL_CLANG is set to {configured!r}, which is not a file")
        return configured
    candidates = list(filter(None, (shutil.which("clang"), shutil.which("clang.exe"))))
    if os.name == "nt":
        candidates.append(r"C:\Program Files\LLVM\bin\clang.exe")
    return next((item for item in candidates if Path(item).is_file()), None)


def _read_and_compile(path: Path) -> str:
    if path.suffix.lower() != ".ocl":
        raise ValueError("input file must use the .ocl extension")
    # utf-8-sig so a byte-order mark, which several Windows editors write by
    # default, does not reach the lexer as an invalid token.
    source = path.read_text(encoding="utf-8-sig")
    return compile_source(source, path.name)[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="oclc", description="Orbit Core Language compiler prototype")
    parser.add_argument("command", choices=("check", "emit-ir", "build"))
    parser.add_argument("source", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args(argv)
    try:
        ir = _read_and_compile(args.source)
        if args.command == "check":
            print(f"checked {args.source}")
            return 0
        if args.command == "emit-ir":
            if args.output:
                args.output.write_text(ir, encoding="utf-8")
            else:
                print(ir, end="")
            return 0

        clang = _clang()
        if not clang:
            print("error: Clang was not found; install LLVM/Clang or set OCL_CLANG to clang's full path", file=sys.stderr)
            return 2
        output = args.output or args.source.with_suffix(".exe" if os.name == "nt" else "")
        # Build IR is an intermediate, not a user artifact. Keeping it in a
        # temporary directory prevents source-adjacent .ll clobbering and also
        # ensures a source named like "-warning.ocl" cannot become a Clang flag.
        with tempfile.TemporaryDirectory(prefix="oclc-") as directory:
            ir_path = Path(directory).resolve() / "module.ll"
            ir_path.write_text(ir, encoding="utf-8")
            command = [clang, str(ir_path)]
            # Prototype 0.1 has no runtime or C-library calls. On Windows,
            # linking directly to main keeps the bootstrap independent of the
            # MSVC CRT. These are PE/COFF linker flags, selected only by host OS.
            if os.name == "nt":
                command.extend(("-nostdlib", "-Wl,/entry:main", "-Wl,/subsystem:console"))
            command.extend(("-o", str(output)))
            result = subprocess.run(command, text=True, capture_output=True)
        if result.returncode:
            print(result.stderr, file=sys.stderr, end="")
            # External tool exit values are not part of oclc's public exit-code
            # contract and must not collide with reserved compiler codes.
            return 1
        print(f"built {output}")
        return 0
    except DiagnosticError as error:
        print(error.render(str(args.source)), file=sys.stderr)
        return 1
    except InternalCompilerError as error:
        print(f"internal compiler error: {error}", file=sys.stderr)
        print("this is a compiler bug; please report it with the source that triggered it", file=sys.stderr)
        return 70
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
