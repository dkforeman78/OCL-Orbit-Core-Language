# OCL — Orbit Core Language

OCL Compiler Prototype 0.1 proves the bootstrap path from `.ocl` source through a modular frontend and LLVM IR to a host-native executable.

## Prerequisites

- Python 3.11 or newer (the compiler has no third-party Python dependencies).
- LLVM/Clang 18 or newer for `build` and the native acceptance test. Put `clang` on `PATH`, install it at `C:\Program Files\LLVM\bin\clang.exe`, or set `OCL_CLANG` to its full path.
- A platform linker supported by Clang. The LLVM Windows installer includes
  `lld-link`, which is sufficient for Prototype 0.1.

The prototype is tested on Windows x86-64 and is designed to work on Linux/macOS hosts supported by Clang. Cross-compilation and ARM64 validation are roadmap work, not 0.1 claims.

## Use

```powershell
.\oclc.cmd check examples\hello.ocl
.\oclc.cmd emit-ir examples\hello.ocl
.\oclc.cmd emit-ir examples\hello.ocl -o hello.ll
.\oclc.cmd build examples\hello.ocl -o hello.exe
.\hello.exe
$LASTEXITCODE # 42
```

On non-Windows hosts, run `python3 oclc.py ...`. `build` leaves the generated `.ll` file beside the source for inspection.

## Tests

```powershell
python -m unittest discover -s tests -v
```

The native build/execute test skips when Clang is unavailable; all frontend and IR tests still run. A supported clean development environment includes Clang, where all tests must pass.

## Scope and limitations

Prototype 0.1 supports functions with no parameters, the `i32` type, a single return statement, and non-negative integer literals. See [the language specification](docs/OCL_LANGUAGE_SPEC.md) and [architecture overview](docs/ARCHITECTURE.md). Arithmetic and function calls are the next milestone.

There is intentionally no `.oxr`/`.ofx` generation, custom linker, stabilized OCL ABI, ownership model, package manager, or standard library yet. Native builds use the host format until the canonical Orbit executable specification is supplied.

Windows 0.1 executables are linked without the MSVC C runtime and enter directly
at `main`. This is safe for the current literal-return-only subset and avoids an
unnecessary Visual Studio dependency. A proper runtime entry point and C ABI
linking strategy must be designed before library calls or arguments are added.
