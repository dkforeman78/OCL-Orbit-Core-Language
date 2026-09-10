# Modules — Prototype 0.16

Place `import arithmetic;` before declarations to load `arithmetic.ocl` beside
the entry source. Dependencies resolve against this same entry directory, independent
of the shell working directory. `import` is a reserved keyword. Names must be single
identifiers; paths, aliases, dotted imports and packages are not supported yet.

Reachable modules form one flat compilation unit: functions, types and constants
can refer across files, with existing collision and nominal-type rules. Every file
contains complete declarations. Only the entry file may define `main`. There are no
namespace/visibility or stable ABI commitments in this prototype.

Resolution is iterative and deterministic, in source import order. Diamonds load
each canonical path once. Duplicate direct imports and cycles are diagnostics.
Up to 256 unique resolved files, including the entry file, are allowed. Symlinks
escaping the resolved entry directory are rejected; this is a resolver policy, not
a filesystem sandbox. Hard links are distinct paths. Case-sensitive hosts retain
case-sensitive path identity. Each file is read as UTF-8 with BOM removal and newline
normalization. Unreachable files are not read.

`compile_file(path)` returns a combined Program and LLVM IR. `compile_source(text)`
does not access the filesystem and rejects imports with E0300. CLI check, emit-ir,
and both build modes use the same file resolver and invoke Clang once per build.

Source locations carry an immutable source document. Errors retain per-file offsets,
lines, columns and snippets in both text and JSON. The entry filename keeps the
caller-supplied spelling; dependency filenames use resolved absolute paths. Missing
or unreadable dependency errors point at the importing name and include the attempted
path. Errors lexing or parsing a readable dependency point inside that dependency.

Diagnostic categories: E0300 invalid import placement/syntax or wrong API; E0301
dependency resolution/read failure; E0302 duplicate direct import; E0303 cycle;
E0304 module-count limit; E0305 escaped directory; E0306 imported main. Codes remain
provisional. Existing semantic diagnostics apply to combined declarations.

Example: `oclc build --release examples/modules/main.ocl` builds the module example,
whose result is 42. The same example is exercised by check/emit-ir and both native
build modes in the test suite on every hosted platform.
