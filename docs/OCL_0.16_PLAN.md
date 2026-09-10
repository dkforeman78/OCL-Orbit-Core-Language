# OCL 0.16 — Modules and Imports

Status: implementation preparation under the instruction to proceed through the roadmap.
Depends on the 0.15 diagnostics work; 0.15 review fixes must be integrated before release.

## Bounded first module model

```text
// main.ocl
import math;
fn main() -> i32 { return add(20, 22); }

// math.ocl
fn add(a: i32, b: i32) -> i32 { return a + b; }
```

An import is `import IDENTIFIER ;`, at the top level before declarations. It resolves
to IDENTIFIER.ocl beside the entry source file. Imports in dependencies use that
same root. There are no quoted paths, dotted names, aliases, directories, package
search paths, environment search paths, or network resolution in this milestone.

Every reachable file contributes declarations to one flat compilation unit. Existing
unqualified calls and type/constant references may resolve across files regardless
of declaration order. Existing namespace collision rules apply across the unit;
an import never silently replaces a declaration. Namespace qualification and
visibility are reserved for 0.17. These rules are provisional bootstrap semantics,
not a permanent export or symbol-mangling contract.

The entry file alone defines `main`. Imported files cannot define it. Every file
must contain complete declarations; imports cannot splice fragments of a function
or type across files. Only reachable files are read.

## Resolution and bounds

- Read UTF-8/BOM source with the existing newline normalization.
- Resolve paths against the entry directory independently of the process working directory.
- Traverse imports deterministically in source order, using an explicit stack.
- Compile each resolved file once, including diamond-shaped dependency graphs.
- Reject duplicate direct imports, cycles, missing/unreadable modules, and imports after declarations with located diagnostics.
- Report a cycle's import chain. Reject imported `main` at its declaration.
- Bound the graph to 256 files including the entry file. Count unique canonical files, not import edges. Test at and beyond the bound.
- Resolve filesystem aliases before identity checks; reject module symlinks that resolve outside the entry directory. Do not describe this policy as an operating-system sandbox.
- Preserve each file's logical source identity; do not depend on filesystem enumeration order.

## Compiler integration

Add a resolver/source-bundle layer between source loading and parsing. Keep import
discovery, graph traversal, source ownership, and compilation distinct. Parse complete
files independently, with an explicit declaration-discovery phase where cross-file
enum names are needed to disambiguate existing syntax. Then analyze and lower one
combined program to LLVM and invoke Clang once.

Attach or map every token/AST location to the owning source. Parser, semantic, and
lowering diagnostics must render the correct filename, local line/column, offset,
and source snippet in both text and JSON. Do not concatenate files without preserving
boundaries and source mappings. Dependency I/O failures identify the importing site
and the attempted module path.

Keep `compile_source` usable without filesystem access. A source containing imports
through that API must give a clear diagnostic directing callers to the file/bundle
API, rather than reading the current directory implicitly. CLI check, emit-ir, and
build use the same resolver; release/default modes compile the same source graph.

## Acceptance and review

1. Preserve the single-file suite and all fourteen existing acceptance programs in both modes.
2. Add a multi-file example returning 42 under check, emit-ir, default build, and release build as applicable; check/emit-ir do not execute programs.
3. Test cross-file functions, constants, enums, and structures; forward references; duplicate declarations; same-name nominal types; and imported-main rejection.
4. Test chains, diamonds, duplicate direct imports, cycles, missing files, invalid UTF-8, paths with spaces, different working directories, and graph limits. Filesystem-specific cases must have explicit host handling.
5. Inject lexer, parser, and semantic errors in dependencies and assert complete text/JSON source identity, including CRLF/BOM normalization and Unicode comments.
6. Test that unused files are not loaded, partial declarations cannot cross file boundaries, and failure does not overwrite output artifacts.
7. Preserve existing expression and statement depth protections and verify long import chains do not consume Python recursion depth.
8. Claude independently mutation-tests resolution, deduplication, cycles, source mappings, declaration discovery, and both CLI modes before release.

## Deferred

Namespaces, visibility modifiers, aliases, separate object compilation, incremental
caches, packages, library installation, stable linkage, C interoperability, and new
runtime facilities are outside 0.16. No language memory or ABI model is established.
