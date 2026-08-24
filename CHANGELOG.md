# Changelog

## 0.2.0 - Unreleased

- Added typed `i32` function parameters and parameter references.
- Added left-associative integer addition and function-call expressions.
- Added two-pass function resolution, argument-count validation, and new diagnostics for duplicate parameters, unknown names/functions, invalid arity, and invalid `main` parameters.
- Added LLVM lowering for parameters, calls, and arithmetic plus the `add(20, 22)` native acceptance program.

### Fixed after independent review

- Semantic analysis and LLVM lowering now walk expressions with an explicit stack, so deeply nested valid expressions compile instead of raising `RecursionError`.
- The parser bounds expression nesting at 256 levels and reports `E0101` rather than relying on the interpreter stack limit.
- Compiler-generated LLVM names are confined to a reserved namespace (`ocl.entry`, numeric temporaries), so a parameter named `entry` no longer produces IR that LLVM rejects.
- Documented Prototype 0.2 `i32` addition as defined two's-complement wrapping, with a native boundary test, while leaving the permanent profile-specific overflow policy open for design review.
- Restated the Windows CRT-free entry envelope in terms of what invalidates it, and refreshed stale 0.1 references in the 0.2 documentation.
- Added the missing trailing-comma rejection test for argument lists, and made semantic analysis raise `InternalCompilerError` rather than `TypeError` on an unrecognised node.

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
