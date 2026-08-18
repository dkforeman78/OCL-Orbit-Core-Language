# Compiler architecture

`oclc` uses a deliberately small pipeline with separate modules:

1. `lexer` converts UTF-8 source text into located tokens.
2. `parser` creates typed AST nodes.
3. `semantic` validates names, types, bodies, and literal ranges.
4. `codegen` lowers the validated AST to textual LLVM IR.
5. `cli` invokes Clang for LLVM compilation and host-native linking.

Direct AST-to-LLVM lowering is limited to the bootstrap. An Orbit IR layer can be inserted later without changing the lexer, parser, or command surface. Diagnostics carry stable-looking codes for tooling, but codes are provisional during 0.x.

LLVM and the host linker own object format, calling convention, target selection, optimization, and machine-code generation. This avoids prematurely defining the OCL ABI or the Orbit `.oxr` format.

On Windows, the 0.1 bootstrap links without the MSVC CRT and selects `main`
directly as the PE entry point. That narrowly supports the current no-argument,
literal-return acceptance program. It is not the permanent OCL runtime or ABI.
