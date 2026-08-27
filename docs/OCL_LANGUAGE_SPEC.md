# OCL Language Specification — Prototype 0.3

This document describes only the implemented 0.3 subset. It is not a stability promise for later OCL releases.

## Grammar

```ebnf
program          = { function } , EOF ;
function         = "fn" , identifier , "(" , [ parameters ] , ")" , "->" , type , body ;
parameters       = parameter , { "," , parameter } ;
parameter        = identifier , ":" , type ;
type             = "i32" ;
body             = "{" , { let-statement } , return-statement , "}" ;
let-statement    = "let" , identifier , ":" , type , "=" , expression , ";" ;
return-statement = "return" , expression , ";" ;
expression       = term , { ( "+" | "-" ) , term } ;
term             = primary , { "*" , primary } ;
primary          = integer-literal | call | identifier | "(" , expression , ")" ;
call             = identifier , "(" , [ arguments ] , ")" ;
arguments        = expression , { "," , expression } ;
```

Identifiers contain ASCII letters, digits, and underscores and cannot begin with a digit. `fn`, `let`, and `return` are reserved keywords. Whitespace is insignificant. Multiplication has higher precedence than addition and subtraction. Operators at the same precedence are left-associative. Parentheses override precedence. Comments and negative literals are not part of 0.3; `-` is binary subtraction only.

## Semantics

- A program must define `main`.
- Every function contains zero or more immutable local bindings followed by exactly one `return` statement. A local is introduced by `let name: i32 = expression;` and cannot be reassigned.
- Parameters and arguments are `i32`; parameter names must be unique within a function.
- Identifier expressions resolve to parameters or earlier local bindings. An initializer is analyzed before its local name enters scope, so self-reference and references to later locals are rejected.
- Local names must be unique within a function and cannot shadow parameters. OCL 0.3 has one function-wide local scope and no shadowing.
- Function calls may refer to functions declared later in the file.
- Calls must resolve to a declared function and supply exactly its declared number of arguments.
- `main` must have no parameters.
- Duplicate function names are rejected.
- **Prototype 0.3 `i32` arithmetic wraps.** Addition, subtraction, and
  multiplication are evaluated modulo 2^32 in two's-complement representation,
  so `2147483647 + 1` is defined and equals
  `-2147483648`. The prototype never inherits C's undefined signed overflow.
  This records and tests the current implementation; it does **not** stabilize
  OCL's permanent overflow policy. Checked, wrapping, or saturating behavior may
  later differ by Safe/Systems/Bare profile, subject to design-authority review.
  Integer *literals* are separate and remain bounded by `i32` (`E0203`).
- Expressions may nest at most 256 levels deep; exceeding that is a diagnostic
  (`E0101`), never a compiler failure. Binary-operator chains are folded
  iteratively and cost no nesting depth, so the limit is reached through nested
  calls or parenthesized expressions. The
  limit is an implementation bound, not a language constant, and may rise. The
  bound is enforced independently of how deep the calling program's own stack is,
  so the same source always produces the same result.
- `main` is emitted using LLVM's default host calling convention. On Windows it is
  linked directly as the CRT-free executable entry point. No broader OCL ABI or
  runtime contract is stabilized.
- Every source function is currently emitted as an externally visible LLVM
  definition with its source identifier as the symbol name. Linkage, mangling,
  visibility, cross-module uniqueness, and foreign-call behavior are provisional.
- Names the compiler generates in LLVM IR are confined to a reserved namespace:
  they either contain `.` (such as the `ocl.entry` block label) or are purely
  numeric (SSA temporaries). An OCL identifier can be neither, so no source name
  can ever collide with a generated one.
- The Windows loader invokes the CRT-free `main` entry directly and observes its
  returned `i32` as the process exit status on the verified x86-64 host. This is
  only a bootstrap mechanism; argument passing, initialization, teardown, and a
  permanent runtime entry contract remain undefined.
- Native output is a normal host executable. `.oxr` and `.ofx` are intentionally untouched until their canonical specification is available.

## Safety and compatibility status

Prototype 0.3 has no pointers, allocation, ownership, references, mutation, or concurrency, so it makes no permanent memory-model decision. Integer literals are range checked, and arithmetic uses the provisional, defined wrapping behavior described above. The final overflow policy, broader variable model, textual syntax, OCL ABI, and executable format remain provisional.
