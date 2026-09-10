# Diagnostic output — Prototype 0.15

Use `oclc check source.ocl --diagnostic-format=json` (also supported by `build`
and `emit-ir`). Text remains the default. Use full long-option spellings; ambiguous
or abbreviated options are rejected. A valid format option before `--` selects
usage-error rendering even if argument parsing fails; the last format option wins.
Invalid format values produce a text usage error. Help remains ordinary help text.

On failure stderr contains one JSON object followed by a newline, with these fields:

```json
{"schema_version":1,"severity":"error","kind":"source","code":"E0001","message":"invalid token '@'","file":"source.ocl","location":{"offset":26,"line":1,"column":27},"source_line":"fn main() -> i32 { return @; }"}
```

Kinds are `source`, `usage`, `toolchain`, `input-output`, and `internal`.
Non-source errors have null code, location, and source_line. Usage errors also have
null file; other file fields name the input path, not an inferred external-tool
source location. External-tool output is a single escaped message, not parsed into
fabricated OCL diagnostics. Internal errors here mean detected compiler invariant
failures, not a promise to catch every possible Python exception.

Offsets are zero-based Unicode code-point indexes, and lines/columns are one-based
Unicode code-point positions. Tabs count as one source character. These are not
UTF-8 byte offsets, UTF-16 LSP positions, or terminal display-cell coordinates.
CLI source loading removes a UTF-8 BOM and normalizes newlines to LF before
compilation; positions refer to that normalized source. Direct API callers receive
positions in their supplied source string. Only LF advances the lexer's line count.
Text output expands tabs to four-column stops for caret placement; Unicode terminal
cell widths are not normalized. JSON retains unexpanded source text.

Successful check/build status and emitted LLVM IR remain on stdout unchanged;
there is no JSON success envelope. Success emits no diagnostic object. The compiler
continues to stop at its first error. Exit codes retain their 0.14 meanings:
0 success, 1 source/I/O/build failure, 2 usage error or missing Clang, 70 explicit
internal compiler error. Codes, schema and CLI remain provisional during 0.x.
