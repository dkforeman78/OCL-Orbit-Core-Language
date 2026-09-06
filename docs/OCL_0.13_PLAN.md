# OCL 0.13 — Integer Literals and Source Comments

Status: scope approved; implementation complete and awaiting independent review.
Baseline: released v0.12.0, commit d595017ff169762f08fce11ecb089fa740864c0d.

## Purpose

Make integer masks and bit patterns readable after 0.12, and allow explanations inside OCL source. This is a bounded language-fundamentals milestone aligned with requirements sections 4, 6, 13, and 24.

## Proposed syntax and behavior

- Decimal literals remain supported, including leading zeroes; leading zeroes do not imply octal.
- Add lowercase `0x` hexadecimal and `0b` binary prefixes. Hexadecimal digits accept either case; uppercase prefixes are rejected.
- Permit `_` only between two valid digits in a number, for example `1_000`, `0x7fff_ffff`, and `0b0010_1010`. Reject separators next to a prefix, at the end, or repeated.
- Reject malformed numeric words as diagnostics: missing digits, digits outside the radix, and unsupported suffixes. Do not split `0b102` or `42u8` into apparently valid tokens.
- Every unsuffixed literal remains an i32 value regardless of radix. Existing unary-minus handling permits i32::MIN; positive values above i32::MAX are rejected. `0xffff_ffff` is not implicitly negative or unsigned. Explicit conversions retain their existing rules.
- Apply notation consistently to expression literals and array-length literals, retaining existing array-size bounds.
- Add `//` comments through newline or EOF and non-nesting `/* ... */` comments. The first `*/` closes a block comment; another `/*` inside it is ordinary comment text. Diagnose an unterminated block comment at its opening delimiter.
- Comments separate tokens as whitespace would: `ab/* note */cd` remains two identifiers, and `</* note */<` does not become a shift operator. Division and multiplication retain their normal meaning outside comments.
- Preserve original source offsets and line/column locations, including across multiline comments, CRLF source, and Unicode comment text.

All new syntax is provisional 0.x syntax. There is no integer suffix, contextual literal typing, octal notation, string or character literal, documentation-comment system, preprocessing, ABI, ownership, or execution-profile expansion in this milestone.

## Proposed acceptance program

```text
// A readable bit-mask example; expected exit status: 42.
fn main() -> i32 {
    let mask: i32 = 0x00_ff;
    /* Keep the low byte of the pattern. */
    return 0b0010_1010 & mask;
}
```

## Implementation sequence

1. Add lexer handling for numeric forms and comments with precise diagnostics. Scan iteratively; retain source spelling and locations.
2. Centralize bounded literal decoding for expressions, unary minimum handling, and array lengths. Check magnitude without allowing huge decimal input to escape as a host integer-conversion exception. Long valid runs of leading zeroes should still work.
3. Add frontend, diagnostic, constant-evaluation, and native tests. Equivalent old/new literal spellings and comment insertion must preserve generated behavior.
4. Add `literals.ocl` to every existing host/Python acceptance job; update the language specification, README, architecture where needed, and unreleased changelog.
5. Open a draft implementation PR, run the hosted matrix, and request Claude's independent audit. Resolve findings before final release preparation.

## Acceptance and review focus

- Exercise decimal, hexadecimal, and binary values at zero, i32::MAX, i32::MIN through unary minus, and both out-of-range boundaries.
- Verify conversions of representable literals to all eight integer types still agree between constant evaluation and runtime.
- Exercise malformed prefixes, separators, suffixes, radix digits, enormous literals, and long leading-zero sequences; all invalid input must produce diagnostics without tracebacks.
- Exercise array lengths written in each radix, including zero and the existing upper boundary.
- Test comments at EOF, between tokens, across lines, containing operator-like text, and adjacent to division/multiplication. Assert diagnostic locations after comments and at unterminated openings.
- Compare behavior and LLVM IR for equivalent decimal/radix programs and programs with/without comments; execute the thirteenth acceptance program with exit status 42.
- Mutation-test radix selection, separator validation, numeric bounds, comment termination, and source-position tracking. Confirm existing depth guards remain accurate.
- Preserve all 277 baseline tests and all twelve existing acceptance programs. Require supported Clang and passing hosted jobs.

## Approval requested

Approve the feature scope and the explicit choices above: i32 literals in every radix, lowercase prefixes, separators only between digits, and non-nesting block comments. Implementation follows scope approval; the milestone retains the independent-review and release gates.
