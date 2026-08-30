# Changelog

## 0.11.0 - Unreleased

- Added signed `i8`, `i16`, and `i64` and unsigned `u8`, `u16`, `u32`, and `u64` alongside `i32`.
- Added explicit integer conversion with `as`; implicit widening and signedness conversion remain forbidden.
- Added exact-width wrapping arithmetic, signed and unsigned comparisons, and guarded signed/unsigned division and remainder.
- Extended functions, locals, constants, arrays, and structure fields to all fixed-width integer types.
- Added the `integers.ocl` native acceptance program.

## 0.10.0 - 2026-08-30

- Added top-level typed compile-time constants for `i32`, `bool`, and declared enum types.
- Added declaration-order-independent constant references with cycle, duplicate, type, and invalid-expression diagnostics.
- Added compile-time arithmetic, comparison, equality, logic, `if`, and exhaustive `match` evaluation using the prototype's defined integer semantics.
- Folded constants directly into LLVM operands without global storage, runtime initialization, or linker symbols.
- Added the `constants.ocl` native acceptance program.

### Fixed after independent review

- Constant folding no longer recurses over an initializer's AST. Binary chains are folded iteratively by the parser, so an initializer's depth is bounded only by the source; a 1000-term chain in a `const` raised an uncaught `RecursionError` while the identical expression in a function body compiled, because the analysis and lowering walks are already iterative. `if`, `match`, `&&` and `||` still evaluate only the operand they select.
- Covered compile-time wrapping. Without it an overflowing constant folds to an out-of-range value and is emitted as a literal LLVM silently truncates.
- Covered compile-time short-circuiting. The skipped operand would raise `E0217` if it were folded, so accepting those programs is the only observable evidence that the operand is skipped.


## 0.9.0 - 2026-08-29

- Added top-level nominal enumerations with one through 256 unit variants.
- Added enum values in parameters, results, locals, assignments, and strict same-enum equality.
- Added exhaustive `match` expressions with duplicate, unknown, mismatched, and missing-arm diagnostics.
- Lowered enums provisionally as LLVM `i32` values and matches as `switch` control flow with typed `phi` merges.
- Added the `enums.ocl` native acceptance program.

### Fixed after independent review

- Covered the non-enumeration `match` scrutinee guard. Without it the analyzer keeps a `None` enumeration and reads `.variants` off it, so `match n { }` on an `i32` kills the compiler with an `AttributeError` instead of reporting `E0235`.
- Covered the invalid-discriminant trap that the architecture overview documents as part of the lowering. Exhaustiveness makes that block unreachable for any well-typed program, so nothing but the emitted shape can hold it in place; both retargeting the switch default and deleting the trap call left the suite green.


## 0.8.0 - 2026-08-28

- Added top-level named structures with `i32` and `bool` fields.
- Added named-field structure literals, field reads, and mutable field assignment.
- Added declaration-order-independent type resolution and diagnostics for duplicate structures, invalid declarations, and incomplete or invalid literals.
- Added backward-compatible structured internal type objects for scalars, arrays, and named structures.
- Added named LLVM structure lowering with entry-block local storage while leaving layout and aggregate ABI semantics provisional.
- Added the `structures.ocl` native acceptance program.

### Fixed after independent review

- Covered the emitted named-structure field order. `getelementptr` indices are declaration positions, so the LLVM type must list fields in the same order; reversing it left all 205 tests green because every structure in the suite had exactly two fields, which survives a reversal by accident. A three-field `{ bool, bool, i32 }` faults with an access violation under the same defect.
- Covered the unknown-field guard on the field-*assignment* path. Without it `p.z = 9` reaches codegen and the field lookup raises `KeyError`, so the compiler dies instead of reporting `E0229`.
- Covered structure storage accounting. Neither tail padding nor per-field alignment was asserted: ignoring tail padding let a function declare 3272 bytes against the 2048 ceiling, and ignoring interior alignment let it declare 3072. That ceiling is what keeps a frame inside the page the CRT-free entry point depends on.


## 0.7.0 - 2026-08-28

- Added local fixed-size `[T; N]` arrays of `i32` and `bool`, array literals, indexing, and mutable element assignment.
- Added compile-time diagnostics for invalid array shapes and constant out-of-bounds indices.
- Added deterministic runtime traps for computed out-of-bounds indices, including negative indices.
- Added provisional 256-element per-array and 2048-byte per-function limits while the bootstrap uses its CRT-free Windows entry model.
- Added the `arrays.ocl` native acceptance program.

### Fixed after independent review

- Covered the index-*assignment* path's guards, which repeat the read path's checks but had no tests of their own. Removing them yields, in turn: a `TypeError` traceback out of the compiler when the target is not an array, a bool index silently lowered as the integer 1 and written to element 1, and a constant out-of-bounds write downgraded from a diagnostic to a runtime trap.
- Covered left-to-right evaluation of array-literal elements. Reversing the emission order keeps every stored value correct, so nothing that checks values alone can see it, yet element initializers may call functions and the order is observable.


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
