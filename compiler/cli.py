from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .diagnostics import DiagnosticError
from .driver import compile_source


def _clang() -> str | None:
    configured = os.environ.get("OCL_CLANG")
    candidates = [configured] if configured else []
    candidates.extend(filter(None, (shutil.which("clang"), shutil.which("clang.exe"))))
    if os.name == "nt":
        candidates.append(r"C:\Program Files\LLVM\bin\clang.exe")
    return next((item for item in candidates if item and Path(item).is_file()), None)


def _read_and_compile(path: Path) -> str:
    if path.suffix.lower() != ".ocl":
        raise ValueError("input file must use the .ocl extension")
    source = path.read_text(encoding="utf-8")
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
        ir_path = args.source.with_suffix(".ll")
        ir_path.write_text(ir, encoding="utf-8")
        command = [clang, str(ir_path)]
        # Prototype 0.1 has no runtime or C-library calls. On Windows, linking
        # directly to main keeps the bootstrap independent of the MSVC CRT.
        if os.name == "nt":
            command.extend(("-nostdlib", "-Wl,/entry:main", "-Wl,/subsystem:console"))
        command.extend(("-o", str(output)))
        result = subprocess.run(command, text=True, capture_output=True)
        if result.returncode:
            print(result.stderr, file=sys.stderr, end="")
            return result.returncode
        print(f"built {output}")
        return 0
    except DiagnosticError as error:
        print(error.render(str(args.source)), file=sys.stderr)
        return 1
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
