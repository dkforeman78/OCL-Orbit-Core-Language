# Compiler architecture

`oclc` uses a deliberately small pipeline with separate modules:

1. `lexer` converts UTF-8 source text into located tokens.
2. `parser` creates typed AST nodes.
3. `semantic` validates names, types, bodies, and literal ranges.
4. `codegen` lowers the validated AST to textual LLVM IR.
5. `cli` invokes Clang for LLVM compilation and host-native linking.

Direct AST-to-LLVM lowering is limited to the bootstrap. The 0.11 semantic pass first collects enum, structure, constant, and typed function declarations, then validates blocks in source order, allowing forward calls and forward type/constant use while making lexical visibility deterministic. Its iterative post-order expression walk acts as the prototype type checker for scalars, nominal enums, and local aggregate types. Internal types are structured `ScalarType`, `EnumType`, `ArrayType`, and `StructType` values that retain string compatibility for the provisional 0.x AST API. An Orbit IR layer can be inserted later without changing the lexer, parser, or command surface. Diagnostics carry stable-looking codes for tooling, but codes are provisional during 0.x.

Fixed arrays lower to LLVM `[N x T]` objects allocated in the function entry block. Element addresses use guarded `getelementptr inbounds`: an unsigned comparison rejects both indices at or above the length and negative `i32` indices before the pointer is formed. Constant invalid indices are rejected during semantic analysis. Arrays remain local-only so Prototype 0.7 does not establish aggregate ABI, copying, slice, pointer, or ownership semantics. The 256-element per-array and 2048-byte per-function caps are implementation bounds chosen to keep the CRT-free Windows bootstrap away from unsupported large-frame behavior and may change with the runtime design.

Structures lower to named LLVM `%ocl.struct.Name` types and local entry-block storage. Named literal fields are evaluated in source order and stored using their declaration index, so changing literal order never changes layout. Field reads and writes use constant-index `getelementptr inbounds`. LLVM's current field padding and alignment are used only to conservatively enforce the aggregate storage cap; neither layout nor an aggregate calling convention is a source-level guarantee in Prototype 0.8.

Prototype 0.9 enums are nominal source types represented provisionally as LLVM `i32`; declaration-order ordinals are not a source ABI promise. Exhaustive `match` lowers to an LLVM `switch`, one block per arm, an unreachable invalid-discriminant trap, and a typed `phi` merge. Only the selected arm executes. Payloads, wildcard and guarded arms, stable discriminants, and a C-facing enum representation remain deferred.

Prototype 0.10 constants are collected and type-checked before function bodies, then evaluated through a cycle-detecting dependency walk. Their folded scalar or enum values enter each function's value environment as immediate LLVM operands. They create no global object, address, initializer, symbol, or runtime ordering. The 256-constant cap bounds the bootstrap evaluator and is not a permanent language limit.

Prototype 0.11 integer types map directly to LLVM `i8`, `i16`, `i32`, and `i64`; signedness is retained in the source type and selects signed or unsigned comparisons, extension, division, and remainder. LLVM integer storage itself is signless. Narrowing conversions use `trunc`, widening uses `sext` or `zext` according to the source type, and equal-width signedness changes preserve the bit pattern. Arithmetic wraps at the selected width without `nsw` or `nuw`. Signed division guards zero and the type-specific minimum divided by negative one; unsigned division guards only zero. These choices specify Prototype 0.11 behavior without stabilizing a C ABI or the permanent profile-specific overflow policy.

Immutable `let` locals still lower directly to LLVM SSA operands. A
local whose initializer is a constant or parameter is an alias in the compiler's
value environment; a computed initializer names the resulting SSA temporary.
For `let`, no `alloca`, load, store, mutable storage, lifetime, or ABI behavior is introduced.

Prototype 0.6 `var` locals use type-specific stack slots allocated once in the
entry block. Assignments store and reads load; lexical scope is enforced before
lowering. `while` emits condition/body/exit blocks, while `&&` and `||` use
conditional branches and `phi` nodes so their right operands genuinely short-circuit.
These are function-local implementation details and do not change the external ABI.

`break` and `continue` lower against a stack of loop condition/exit labels, so
nesting always targets the nearest loop. Signed `/` and `%` emit explicit
zero-and-overflow guards before LLVM `sdiv`/`srem`; invalid paths call
`llvm.trap` and are unreachable, preventing LLVM poison from becoming accidental language behavior.

Boolean values lower to LLVM `i1`. An `if` expression creates deterministic,
compiler-reserved then/else/merge block labels, emits a conditional branch, and
joins the selected value with a typed `phi`. The iterative lowering state machine
records the actual predecessor block for each branch, so a nested `if` remains a
valid control-flow graph without recursive AST traversal. This is the first CFG
in the bootstrap backend, but it does not stabilize an intermediate IR or ABI.

Semantic analysis and expression lowering both walk expressions with an explicit
stack rather than by recursion, and emit deterministic LLVM SSA temporaries.
Expression depth is bounded only by the source, so recursing would let a large but
entirely valid program become a `RecursionError` instead of a binary. Recursive
descent in the parser is inherently recursive, so it caps nesting explicitly and
reports `E0101` rather than relying on the interpreter's stack limit.

The recursive statement walks in semantic analysis and lowering carry the same
obligation. `MAX_BLOCK_DEPTH` bounds how deeply blocks may nest, but the parser's
reservation ends with parsing, so those walks reserve their own stack through the
shared helper in `compiler/stack.py`. One `RLock` and one reservation helper serve
every phase, so concurrent compilations cannot restore each other's saved limits
out of order.

That cap is a parser-level count, but it is enforced by Python frames, so `parse`
reserves the stack the bound actually needs before parsing and restores the
previous limit afterwards. Without that, a caller who is already deep — an
embedding tool, a language server, a future self-hosted driver — would exhaust
the stack before the guard could fire, turning a documented diagnostic back into
a `RecursionError`. `FRAMES_PER_LEVEL` records the per-level cost of the
precedence and primary-expression chain; a test measures the real slope for both
parentheses and nested `if` expressions and
fails if a new precedence tier makes it stale. Because Python's recursion limit
is interpreter-global, parse calls are serialized while the temporary limit is
active so concurrent compiler invocations cannot restore limits out of order.

Every name codegen invents lives in a reserved namespace — either containing `.`,
such as the `ocl.entry` block label, or purely numeric, such as SSA temporaries.
The OCL lexer can produce neither, so a source identifier can never capture a
generated name. New generated names must keep that property.

LLVM and the host linker own object format, calling convention, target selection, optimization, and machine-code generation. This avoids prematurely defining the OCL ABI or the Orbit `.oxr` format.

On Windows, the bootstrap passes PE/COFF linker options through Clang, links
without the MSVC CRT, and selects `main` directly as the executable entry point.
Clang still chooses the native host target; OCL does not embed a Windows target
triple. This is not the permanent OCL runtime or ABI, and it is not a claim of
Windows ARM64 support.

It works because the Windows loader calls the entry function directly and, when it
returns, exits the initial thread with that return value, which terminates the
process with it. That is loader behaviour, not a contract. Rather than restate the
supported language subset every release, here is what actually invalidates the
approach — the CRT-free entry point must be redesigned before any of these appear:

- a stack frame larger than one page, since `__chkstk` is absent without the CRT;
- any static initializer, or other setup the CRT would normally run before `main`;
- any call into the C runtime, libc, or an imported system library;
- any need for `argc`/`argv`, or an exit path other than returning from `main`.

0.6 adds guarded division and nearest-loop control while staying inside that envelope:
frames are tens of bytes and the linked binary imports nothing. Verified on the
x86-64 host — `main` in `examples/loop_control.ocl`, the 0.6 acceptance program,
allocates `0x28` bytes and calls no imported symbol.
