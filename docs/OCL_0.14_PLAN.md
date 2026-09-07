# OCL 0.14 — Release Builds and Optimization Verification

Status: scope approved; implementation complete, pending independent review.
Baseline: v0.13.0, merge commit 50683a8882717749e65a0b8a5f06a9fed4dca5fb.

## Purpose

Expose an optimized native build and prove that optimization preserves the implemented language. Requirements sections 2 and 14 call for LLVM optimization and release builds. The current CLI passes no optimization flag to Clang. With wrapping arithmetic, guarded division and shifts, bounds checks, loops, and lazy branches now implemented, optimizer-facing verification is a useful next step before expanding the runtime and memory model.

## Approved interface requested

```text
oclc build program.ocl                 # explicit -O0 baseline
oclc build --release program.ocl       # -O2 native build
oclc build program.ocl --release -o program.exe
oclc emit-ir program.ocl               # existing frontend LLVM IR
oclc check program.ocl                 # existing validation
```

- `--release` is valid only with `build`. Reject it for `check` and `emit-ir` before reading source or creating output, using the CLI's argparse usage-error convention.
- The default build explicitly selects `-O0`; release selects `-O2`. Both preserve current source validation, output naming, toolchain selection, native linking, and runtime semantics.
- `emit-ir` remains the compiler's frontend IR; this milestone adds no optimized-IR output command.
- No fast-math, overflow assumptions, sanitizer, LTO, native-CPU tuning, cross-compilation, stable ABI, or performance guarantee is introduced.
- Debug symbols and source-level debugging remain separate work. An unoptimized build is not advertised as a debugger-ready build.

## Behavior to preserve

1. Exact-width wrapping arithmetic, signed/unsigned comparisons and conversions, and signed division/remainder semantics.
2. Defined traps for computed invalid division, remainder, shift counts, and array indices. Assertions distinguish a deliberate trap from an arbitrary nonzero exit. Verify the existing trap signature on each supported hosted platform; investigate differences rather than weakening checks.
3. Lazy `if`, match arms, `&&`, and `||`: an unselected trapping expression must never execute. Do not use nonterminating programs as the only laziness oracle under optimization.
4. Correct loop mutation, nearest-loop break/continue behavior, aggregate initialization, and computed field/element access.
5. Existing compile-time diagnostics, CLI exit codes, source/output handling, and supported toolchain floor.

Optimization may remove a redundant guard or constant expression if behavior is preserved. Tests must assert execution results and traps, not demand a particular optimized IR shape or instruction count.

## Implementation plan

1. Add the CLI option and explicit optimization selection. Test both argument placements, flag rejection on other commands, Clang argument forwarding, and paths containing spaces.
2. Promote the native build/run helper currently borrowed from `Ocl12Tests` into shared test infrastructure. Keep executable timeouts, Clang-required behavior, and Windows trap handling intact. Migrate affected callers and make build mode explicit in the helper.
3. Add a bounded semantic differential suite that builds selected programs under both modes. Cover all eight integer types and valid arithmetic boundaries, all trap families, lazy branches, loop control, arrays, and structures. Include operands flowing through functions and mutable locals, alongside compile-time simplification cases.
4. Add `release.ocl`, a deterministic finite program exercising mixed arithmetic and control flow, returning 42 under both modes. Run all fourteen acceptance programs in both modes in every existing host/Python job.
5. Update README, CLI documentation, architecture, and unreleased changelog. Preserve the language grammar and mark optimization levels as provisional toolchain policy.
6. Publish a draft PR and obtain Claude's independent audit. Review should mutation-test CLI mode selection and runtime guard/CFG invariants, explicitly separating equivalent optimized results from defects. Resolve findings before release approval.

## Acceptance gate

- Preserve all 290 baseline tests and thirteen existing acceptance programs.
- Exercise default and release builds with supported Clang on Windows, Ubuntu, and macOS, across the existing Python matrix.
- Both modes produce 42 for all fourteen acceptance programs; differential programs agree with independent expected values, not merely with one another.
- Every invalid runtime operation covered by the suite produces the promised deterministic trap in both modes. Every valid boundary case remains valid.
- CLI tests catch a missing, inverted, or incorrectly forwarded optimization selection even when both binaries happen to return the same result.
- No unbounded native test execution, source-adjacent IR clobbering, new runtime dependency, or compiler traceback from malformed options/source.

## Scope boundary and decision

This milestone adds build tooling and verification, not source-language syntax, memory access, ownership, or a runtime ABI. Aggregate copying, floating point, pointers/references, modules, and C interoperability need their own scoped design.

Approve the build interface and the -O0/-O2 policy above before implementation. Independent review and final release approval remain separate gates.
