# Changelog

## 0.1.0 - 2026-08-18

- Added source loading, lexer, parser, AST, semantic validation, diagnostics, LLVM IR generation, Clang integration, CLI, documentation, example, and automated tests.
- Added CRT-free Windows bootstrap linking so LLVM's bundled linker is sufficient for the 0.1 acceptance program.

### Fixed after independent review

- Integer literals longer than CPython's conversion limit now produce `E0203` instead of escaping as a raw `ValueError`.
- Codegen raises a controlled internal compiler error (exit code 70) instead of `IndexError` when handed a program that has not passed semantic analysis.
- Source files are read as `utf-8-sig`, so a byte-order mark no longer reaches the lexer as an invalid token.
- `OCL_CLANG` pointing at a nonexistent file is now an error rather than a silent fallback to another compiler.
- Expanded the test suite from 8 to 34 tests, covering every diagnostic code, rendered diagnostic output, and CLI exit codes. Each test was verified by restoring the defect it covers and confirming it fails.
- CI installs and verifies Clang, sets `OCL_REQUIRE_CLANG` so the native test cannot skip into a green run, and runs the section 18 acceptance workflow as an explicit step.
- Added Python 3.11/3.12 CI coverage across Windows, Linux, and macOS.
- Prevented Clang option injection through dash-prefixed source names and stopped `build` from overwriting source-adjacent LLVM IR.
- Normalized external Clang failures, renamed the AST node module to avoid shadowing Python's standard library, and added correct UTF-8 LLVM string escaping.
- Documented provisional symbol linkage and the verified scope of the Windows CRT-free entry mechanism.
- Unified the Clang-dependent test guard so `OCL_REQUIRE_CLANG` governs every such test, not only the native acceptance test.
- Added `tools/check_clang_version.py`, run in CI, which resolves Clang the same way `oclc build` does and enforces separate LLVM and Apple Clang version floors.
- Documented that exit code `1` also covers a failed native build or link, and that Clang's own exit status is never forwarded.
