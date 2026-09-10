from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .diagnostics import DiagnosticError, InternalCompilerError, diagnostic_record
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


class DiagnosticArgumentParser(argparse.ArgumentParser):
    json_errors = False

    def error(self, message):
        if self.json_errors:
            print(json.dumps(diagnostic_record("usage", message)), file=sys.stderr)
            self.exit(2)
        super().error(message)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = DiagnosticArgumentParser(prog="oclc", description="Orbit Core Language compiler prototype")
    # Select error rendering even when argparse cannot finish parsing. Respect
    # the end-of-options marker and the last explicit format option.
    for index, word in enumerate(argv):
        if word == "--":
            break
        if word.startswith("--diagnostic-format="):
            parser.json_errors = word.split("=", 1)[1] == "json"
        elif word == "--diagnostic-format" and index + 1 < len(argv):
            parser.json_errors = argv[index + 1] == "json"
    parser.allow_abbrev = False
    parser.add_argument("command", choices=("check", "emit-ir", "build"))
    parser.add_argument("source", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    parser.add_argument("--release", action="store_true", help="build native output with -O2 optimization")
    parser.add_argument("--diagnostic-format", choices=("text", "json"), default="text",
                        help="render errors as text (default) or one JSON object per line on stderr")
    args = parser.parse_args(argv)
    if args.release and args.command != "build":
        parser.error("--release is only valid with build")
    def report(kind, message, text):
        if args.diagnostic_format == "json":
            print(json.dumps(diagnostic_record(kind, message, filename=str(args.source))), file=sys.stderr)
        else:
            print(text, file=sys.stderr)
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
            message = "Clang was not found; install LLVM/Clang or set OCL_CLANG to clang's full path"
            report("toolchain", message, "error: " + message)
            return 2
        output = args.output or args.source.with_suffix(".exe" if os.name == "nt" else "")
        # Build IR is an intermediate, not a user artifact. Keeping it in a
        # temporary directory prevents source-adjacent .ll clobbering and also
        # ensures a source named like "-warning.ocl" cannot become a Clang flag.
        with tempfile.TemporaryDirectory(prefix="oclc-") as directory:
            ir_path = Path(directory).resolve() / "module.ll"
            ir_path.write_text(ir, encoding="utf-8")
            command = [clang, str(ir_path), "-O2" if args.release else "-O0"]
            # The current prototype has no runtime or C-library calls. On Windows,
            # linking directly to main keeps the bootstrap independent of the
            # MSVC CRT. These are PE/COFF linker flags, selected only by host OS.
            if os.name == "nt":
                command.extend(("-nostdlib", "-Wl,/entry:main", "-Wl,/subsystem:console"))
            command.extend(("-o", str(output)))
            result = subprocess.run(command, text=True, capture_output=True)
        if result.returncode:
            report("toolchain", result.stderr or result.stdout or "Clang failed without diagnostic output", result.stderr)
            # External tool exit values are not part of oclc's public exit-code
            # contract and must not collide with reserved compiler codes.
            return 1
        print(f"built {output}")
        return 0
    except DiagnosticError as error:
        if args.diagnostic_format == "json":
            print(json.dumps(error.as_dict(str(args.source))), file=sys.stderr)
        else:
            print(error.render(str(args.source)), file=sys.stderr)
        return 1
    except InternalCompilerError as error:
        report("internal", str(error), f"internal compiler error: {error}\nthis is a compiler bug; please report it with the source that triggered it")
        return 70
    except (OSError, ValueError) as error:
        report("input-output", str(error), f"error: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
