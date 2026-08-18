# OCL Language Specification — Prototype 0.1

This document describes only the implemented bootstrap subset. It is not a stability promise for later OCL releases.

## Grammar

```ebnf
program          = { function } , EOF ;
function         = "fn" , identifier , "(" , ")" , "->" , type , body ;
type             = "i32" ;
body             = "{" , return-statement , "}" ;
return-statement = "return" , integer-literal , ";" ;
```

Identifiers contain ASCII letters, digits, and underscores and cannot begin with a digit. Whitespace is insignificant. Comments, parameters, calls, arithmetic, variables, and negative literals are not part of 0.1.

## Semantics

- A program must define `main`.
- Every function returns one non-negative `i32` literal.
- Duplicate function names are rejected.
- `main` is emitted using LLVM's default host calling convention. On Windows
  Prototype 0.1 links it directly as the CRT-free executable entry point. No
  broader OCL ABI or runtime contract is stabilized.
- Every source function is currently emitted as an externally visible LLVM
  definition with its source identifier as the symbol name. Linkage, mangling,
  visibility, cross-module uniqueness, and foreign-call behavior are provisional.
- The Windows loader invokes the CRT-free `main` entry directly and observes its
  returned `i32` as the process exit status on the verified x86-64 host. This is
  only a bootstrap mechanism; argument passing, initialization, teardown, and a
  permanent runtime entry contract remain undefined.
- Native output is a normal host executable. `.oxr` and `.ofx` are intentionally untouched until their canonical specification is available.

## Safety and compatibility status

Prototype 0.1 has no pointers, allocation, ownership, references, or concurrency, so it makes no permanent memory-model decision. Integer literals are range checked. The textual syntax, OCL ABI, and executable format remain provisional.
