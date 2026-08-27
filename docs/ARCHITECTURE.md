# Compiler architecture

`oclc` uses a deliberately small pipeline with separate modules:

1. `lexer` converts UTF-8 source text into located tokens.
2. `parser` creates typed AST nodes.
3. `semantic` validates names, types, bodies, and literal ranges.
4. `codegen` lowers the validated AST to textual LLVM IR.
5. `cli` invokes Clang for LLVM compilation and host-native linking.

Direct AST-to-LLVM lowering is limited to the bootstrap. The 0.3 semantic pass first collects function signatures, then validates bodies in source order, allowing forward calls while making local-binding visibility deterministic. An Orbit IR layer can be inserted later without changing the lexer, parser, or command surface. Diagnostics carry stable-looking codes for tooling, but codes are provisional during 0.x.

Prototype 0.3 locals are immutable and lower directly to LLVM SSA operands. A
local whose initializer is a constant or parameter is an alias in the compiler's
value environment; a computed initializer names the resulting SSA temporary.
No `alloca`, load, store, mutable storage, lifetime, or ABI behavior is introduced.

Semantic analysis and expression lowering both walk expressions with an explicit
stack rather than by recursion, and emit deterministic LLVM SSA temporaries.
Expression depth is bounded only by the source, so recursing would let a large but
entirely valid program become a `RecursionError` instead of a binary. Recursive
descent in the parser is inherently recursive, so it caps nesting explicitly and
reports `E0101` rather than relying on the interpreter's stack limit.

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

0.3 adds immutable SSA locals and arithmetic while staying inside that envelope:
frames are tens of bytes and the linked binary imports nothing. Verified on the
x86-64 host — `main` in `examples/add.ocl` allocates `0x28` bytes and calls no
imported symbol.
