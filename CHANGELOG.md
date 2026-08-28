# Changelog

## 0.6.0 - 2026-08-28

- Added nearest-loop `break;` and `continue;` with unreachable-statement and outside-loop diagnostics.
- Added unary integer negation, signed division, and signed remainder, including the `i32` minimum literal.
- Added source diagnostics for literal zero divisors and deterministic runtime traps for computed zero divisors and `i32::MIN / -1` overflow.
- Added guarded LLVM `sdiv`/`srem` control flow and the `loop_control.ocl` native acceptance program.

### Fixed after independent review

- The trap tests now assert the deterministic trap signature rather than only a nonzero exit. A defeated guard lets operands reach `sdiv`/`srem`, and the hardware faults with its own status (`0xC0000094` divide-by-zero, `0xC0000095` overflow) instead of `llvm.trap`'s `0xC000001D`; three separate guard defects were invisible because every one of those is "not zero".
- Covered division by `-1` with a non-`i32::MIN` dividend, and `i32::MIN / 1`. Widening the overflow test from the operand *pair* to either operand made all division by `-1` trap, breaking working arithmetic while the suite stayed green.
- Covered the phi predecessor after a guarded division. The value is produced in the division's safe block, not the enclosing branch's block; naming the branch label yields IR LLVM rejects, and nothing placed a division inside an `if` branch.
- Covered unary minus on a runtime operand. Literal negation is folded in the parser, so the codegen path was never exercised, and replacing the negation with an identity left the suite green.


## 0.5.0 - 2026-08-28

- Added initialized mutable `var` bindings and type-checked reassignment while preserving immutable SSA `let` bindings.
- Added lexical statement blocks, `while` statements, and early `return` with fallthrough and unreachable-code diagnostics.
- Added short-circuiting `&&` and `||` plus unary Boolean `!` with conventional precedence.
- Added LLVM entry-block storage, loads/stores, loop CFGs, and phi-based short-circuit lowering without changing the external ABI.
- Added the `repeat.ocl` native acceptance program and extended all six CI jobs.

### Fixed after independent review

- Every native test run is now time-bounded. A defect in loop or short-circuit lowering emits IR LLVM accepts and a binary that never terminates; the loop acceptance test had no timeout, so such a defect hung the suite instead of failing it, and would have spun a CI job until the platform killed it. Eight of nine native runs were unbounded.
- Semantic analysis and LLVM lowering now reserve the Python stack their recursive statement walks require. The parser's reservation is released when parsing ends, so block nesting at the documented limit raised a `RecursionError` for any caller roughly 750 frames deep, while equivalent expression nesting stayed safe.
- Added `compiler/stack.py` so one lock and one reservation helper serve every phase that changes the interpreter-global recursion limit.
- Covered four invariants that no test held down: a `var` initializer is actually stored (dropping it left every slot undef and the suite stayed green); sibling blocks cannot reuse a name (allowing it emits duplicate `alloca` names LLVM rejects); a `return` inside a loop body does not discharge the function's obligation (treating it as one produced an internal compiler error); and a returning loop body emits no back edge.
- Added a frames-per-block-level test mirroring the expression one, so the statement walks' reservation cannot silently go stale.

## 0.4.0 - 2026-08-27

- Added the `bool` type and `true`/`false` literals for parameters, locals, function results, and expressions.
- Added typed equality and signed integer comparison operators with conventional precedence.
- Added expression-oriented `if condition { expression } else { expression }` with mandatory, type-matched branches.
- Upgraded semantic analysis to type-check expressions, local initializers, calls, and function returns without recursive AST walks.
- Added LLVM `i1`, conditional-branch, generated-block, and `phi` lowering without changing the provisional ABI or runtime envelope.
- Extended parser stack-reservation measurement to nested `if` expressions and added the `decisions.ocl` native acceptance program.

### Fixed after independent review

- Covered the nested-`if` phi-predecessor invariant in the `else` branch, not only the `then` branch. Naming the else label instead of the block the value was produced in emits IR that LLVM rejects, and the suite was previously green under that defect.
- Replaced the weak predecessor assertion, which included a vacuous `assertIn("[", ir)`, with exact phi-line comparisons for then-nesting, else-nesting, and both.
- Added a native truth table over nested `if` expressions, since IR shape alone cannot distinguish a correct phi from a valid but semantically wrong one.

## 0.3.0 - 2026-08-27

- Added immutable typed local bindings with declaration-order visibility and no shadowing.
- Added binary subtraction, multiplication, parenthesized expressions, and conventional arithmetic precedence.
- Lowered locals directly through an LLVM SSA value environment without introducing mutable storage or ABI changes.
- Extended provisional wrapping `i32` arithmetic semantics and expression-depth protection to the new expression forms.
- Added the `local.ocl` native acceptance program and expanded parser, semantic, IR, stress, evaluation-order, and native tests.

### Fixed after independent review

- `parse` now reserves the Python stack its documented depth bound requires, so a deeply nested expression yields `E0101` rather than a `RecursionError` when the compiler is embedded in a caller with its own deep stack. 0.3's added precedence tier had halved the previous margin.
- Added a test that measures the real per-level frame cost, so a future precedence tier cannot silently erode the guard.
- Added boundary tests at exactly the documented depth limit, which was previously untested from either side.
- Serialized the interpreter-global recursion-limit reservation and added tests for concurrent parse exclusion, same-thread reentrancy, and restoration after diagnostics.
- `E0210` now distinguishes a local that shadows a parameter from a redeclared local, instead of reporting both as "already declared".

## 0.2.0 - 2026-08-26

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
