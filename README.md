# OCL — Orbit Core Language

OCL Compiler Prototype 0.8 adds local named structures and field access.

## Prerequisites

- Python 3.11 or newer (the compiler has no third-party Python dependencies).
- LLVM/Clang 18 or newer for `build` and the native acceptance test, or Apple
  Clang 15 or newer on macOS — Apple Clang uses Apple's own version numbering, so
  it is held to a separate floor. Put `clang` on `PATH`, install it at
  `C:\Program Files\LLVM\bin\clang.exe`, or set `OCL_CLANG` to its full path. If
  `OCL_CLANG` is set but does not name a file, `build` fails rather than falling
  back to another compiler. Run `python tools/check_clang_version.py` to confirm
  the toolchain oclc will use.
- A platform linker supported by Clang. The LLVM Windows installer includes
  `lld-link`, which is sufficient for Prototype 0.8.

The prototype is tested in CI on Windows, Linux, and macOS with Python 3.11 and
3.12. Windows x86-64 is the primary development host. Cross-compilation and
ARM64 validation are roadmap work, not 0.8 claims.

## Use

```powershell
.\oclc.cmd check examples\hello.ocl
.\oclc.cmd emit-ir examples\hello.ocl
.\oclc.cmd emit-ir examples\hello.ocl -o hello.ll
.\oclc.cmd build examples\hello.ocl -o hello.exe
.\hello.exe
$LASTEXITCODE # 42

.\oclc.cmd build examples\add.ocl -o add.exe
.\add.exe
$LASTEXITCODE # 42

.\oclc.cmd build examples\local.ocl -o local.exe
.\local.exe
$LASTEXITCODE # 42

.\oclc.cmd build examples\decisions.ocl -o decisions.exe
.\decisions.exe
$LASTEXITCODE # 42

.\oclc.cmd build examples\repeat.ocl -o repeat.exe
.\repeat.exe
$LASTEXITCODE # 42

.\oclc.cmd build examples\loop_control.ocl -o loop_control.exe
.\loop_control.exe
.\oclc.cmd build examples\arrays.ocl -o arrays.exe
.\arrays.exe
.\oclc.cmd build examples\structures.ocl -o structures.exe
.\structures.exe
$LASTEXITCODE # 42
```

On non-Windows hosts, run `python3 oclc.py ...`. `build` uses temporary LLVM IR
and does not alter a source-adjacent `.ll` file. Use `emit-ir` when you want to
inspect or retain the generated IR.

Source files are read as UTF-8 and a leading byte-order mark is accepted.

Exit codes: `0` success; `1` a diagnostic, a bad invocation, or a failed native
build or link; `2` Clang not found; `70` an internal compiler error (a bug —
please report it). Clang's own exit status is deliberately not forwarded, so it
cannot collide with a reserved compiler code.

## Tests

```powershell
python -m unittest discover -s tests -v
```

The Clang-dependent tests — the native build/execute acceptance test, the two
build-behaviour tests, and the toolchain check — skip when Clang is unavailable;
all frontend and IR tests still run. A supported clean development environment
includes Clang, where all tests must pass. Set `OCL_REQUIRE_CLANG=1` to turn
every one of those skips into a failure — CI does this, so no Clang-dependent
coverage can silently disappear from a green run.

CI additionally runs `python tools/check_clang_version.py`, which resolves Clang
the same way `oclc build` does and rejects a toolchain below the documented
minimum.

## Scope and limitations

Prototype 0.8 adds local named structures, named-field literals, field reads, and mutable field assignment to the 0.7 language. See [the language specification](docs/OCL_LANGUAGE_SPEC.md) and [architecture overview](docs/ARCHITECTURE.md).

There is intentionally no type inference, uninitialized variable, aggregate parameters or returns, nested aggregates, aggregate copying or equality, slices, methods, stable structure layout, `else if`, `for`, labeled loop control, floating point, global storage, `.oxr`/`.ofx` generation, custom linker, stabilized OCL ABI, ownership model, package manager, or standard library yet. Native builds use the host format until the canonical Orbit executable specification is supplied.

Windows executables are linked without the MSVC C runtime and enter directly at
`main`, which avoids an unnecessary Visual Studio dependency. This holds for
0.8's functions, locals, guarded arithmetic, loop control, bounded local arrays, and local structures, and the
conditions that would invalidate it —
frames larger than a page, static initializers, any C runtime or system-library
call, or a need for `argc`/`argv` — are listed in
[the architecture overview](docs/ARCHITECTURE.md). A proper runtime entry point
and C ABI linking strategy must be designed before any of those appear.
The Windows-only linker flags assume Clang's PE/COFF-compatible linker interface;
they do not select or stabilize a target triple. Clang selects the native host
target. Windows x86-64 is verified; Windows ARM64 is not yet a 0.8 claim.
