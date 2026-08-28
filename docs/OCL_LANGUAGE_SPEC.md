# OCL Language Specification — Prototype 0.8

This document describes only the implemented 0.8 subset. It is not a stability promise for later OCL releases.

## Grammar

```ebnf
program          = { function | struct-declaration } , EOF ;
function         = "fn" , identifier , "(" , [ parameters ] , ")" , "->" , scalar-type , body ;
struct-declaration = "struct" , identifier , "{" , struct-field , { "," , struct-field } , [ "," ] , "}" ;
struct-field     = identifier , ":" , scalar-type ;
parameters       = parameter , { "," , parameter } ;
parameter        = identifier , ":" , scalar-type ;
scalar-type      = "i32" | "bool" ;
local-type       = scalar-type | array-type | identifier ;
array-type       = "[" , ( "i32" | "bool" ) , ";" , integer-literal , "]" ;
body             = block ;
block            = "{" , { statement } , "}" ;
statement        = let-statement | var-statement | assignment | index-assignment | field-assignment | while-statement
                 | break-statement | continue-statement | return-statement | block ;
let-statement    = "let" , identifier , ":" , local-type , "=" , expression , ";" ;
var-statement    = "var" , identifier , ":" , local-type , "=" , expression , ";" ;
assignment       = identifier , "=" , expression , ";" ;
index-assignment = identifier , "[" , expression , "]" , "=" , expression , ";" ;
field-assignment = identifier , "." , identifier , "=" , expression , ";" ;
while-statement  = "while" , expression , block ;
break-statement  = "break" , ";" ;
continue-statement = "continue" , ";" ;
return-statement = "return" , expression , ";" ;
expression       = logical-or ;
logical-or       = logical-and , { "||" , logical-and } ;
logical-and      = equality , { "&&" , equality } ;
equality         = comparison , { ( "==" | "!=" ) , comparison } ;
comparison       = sum , { ( "<" | "<=" | ">" | ">=" ) , sum } ;
sum              = term , { ( "+" | "-" ) , term } ;
term             = unary , { ( "*" | "/" | "%" ) , unary } ;
unary            = { "!" | "-" } , postfix ;
primary          = integer-literal | boolean-literal | array-literal | struct-literal | call | identifier
                 | "(" , expression , ")" | if-expression ;
array-literal    = "[" , [ expression , { "," , expression } ] , "]" ;
struct-literal   = identifier , "{" , struct-initializer , { "," , struct-initializer } , [ "," ] , "}" ;
struct-initializer = identifier , ":" , expression ;
postfix          = primary , { "[" , expression , "]" | "." , identifier } ;
boolean-literal  = "true" | "false" ;
if-expression    = "if" , expression , "{" , expression , "}"
                 , "else" , "{" , expression , "}" ;
call             = identifier , "(" , [ arguments ] , ")" ;
arguments        = expression , { "," , expression } ;
```

Identifiers contain ASCII letters, digits, and underscores and cannot begin with a digit. `break`, `continue`, `else`, `false`, `fn`, `if`, `let`, `return`, `struct`, `true`, `var`, and `while` are reserved keywords. Whitespace is insignificant. Indexing and field access bind tighter than unary operators. The remaining precedence from highest to lowest is unary `!`/`-`, multiplication/division/remainder, addition/subtraction, relational comparison, equality, `&&`, then `||`. Binary operators at the same precedence are left-associative. Parentheses override precedence. Comments are not part of 0.8.

## Semantics

- A program must define `main`.
- `let` introduces an immutable initialized binding. `var` introduces a mutable initialized binding; only `var` may be reassigned, and every assignment must preserve its declared type. Uninitialized declarations are not grammar.
- Local fixed-size arrays use `[T; N]`, where `T` is `i32` or `bool` and Prototype 0.7 bounds `N` to 1 through 256 and total array storage in one function to 2048 bytes. Array literals must be nonempty and exactly match the declared element type and length. Arrays are local-only: they cannot be parameters or results, nested, copied, or compared. Index expressions require `i32`; constant out-of-bounds indices are diagnostics, while computed out-of-bounds indices deterministically trap. Only `var` array elements may be assigned. The storage bounds and trap policy are provisional implementation choices.
- Structures are top-level named declarations containing 1 through 64 uniquely named `i32` or `bool` fields. Declarations are resolved independently of source order. Named-field literals must initialize every field exactly once; fields may be written in any order, and initializer expressions execute left-to-right in literal source order. Only local structure bindings exist, only `var` fields may be assigned, and structures cannot be nested, passed, returned, copied, or compared. Total local array and structure storage remains bounded to 2048 bytes per function. LLVM field order, padding, and alignment are bootstrap details rather than source ABI guarantees.
- Blocks are lexical scopes. A block-local name is unavailable after its block. Shadowing remains forbidden across an entire function, including nested blocks.
- `while` is a statement whose condition must be `bool`. Its body may execute zero or more times. `return` may appear in any block; every function must return on every path that reaches its end, and statements after an unconditional return are rejected as unreachable.
- `break` exits the nearest enclosing `while`; `continue` begins its next condition check. Both are rejected outside a loop, and following statements in the same block are unreachable.
- Parameters, locals, arguments, and function results may be `i32` or `bool`; parameter names must be unique within a function.
- Identifier expressions resolve to parameters or earlier local bindings. An initializer is analyzed before its local name enters scope, so self-reference and references to later locals are rejected.
- Local names must be unique within a function and cannot shadow parameters.
- Function calls may refer to functions declared later in the file.
- Calls must resolve to a declared function and supply exactly its declared number of arguments.
- `main` must have no parameters.
- `main` must return `i32`; there is no implicit conversion between `i32` and `bool`.
- Duplicate function names are rejected.
- Arithmetic operators require `i32` operands. Division truncates toward zero and remainder has the dividend's sign. Literal zero divisors are rejected with `E0217`; computed zero divisors and `i32::MIN / -1` deterministically trap at runtime. This trap policy is provisional for Prototype 0.6. Relational operators `<`, `<=`, `>`, and `>=` require `i32` operands and produce `bool`. Equality operators `==` and `!=` require matching operand types and produce `bool`.
- `if` is an expression. Its condition must be `bool`, both branches must have the same type, and only the selected branch executes. `else` is mandatory.
- `!` requires `bool`. `&&` and `||` require `bool` operands and short-circuit: the right operand is evaluated only when needed.
- **Prototype 0.6 `i32` arithmetic wraps.** Addition, subtraction, multiplication, and unary negation
  are evaluated modulo 2^32 in two's-complement representation,
  so `2147483647 + 1` is defined and equals
  `-2147483648`. The prototype never inherits C's undefined signed overflow.
  This records and tests the current implementation; it does **not** stabilize
  OCL's permanent overflow policy. Checked, wrapping, or saturating behavior may
  later differ by Safe/Systems/Bare profile, subject to design-authority review.
  Integer *literals* are separate and remain bounded by `i32` (`E0203`).
- Expressions may nest at most 256 levels deep; exceeding that is a diagnostic
  (`E0101`), never a compiler failure. Binary-operator chains are folded
  iteratively and cost no nesting depth, so the limit is reached through nested
  calls, parenthesized expressions, or nested `if` expressions. The
  limit is an implementation bound, not a language constant, and may rise. The
  bound is enforced independently of how deep the calling program's own stack is,
  so the same source always produces the same result.
- Statement blocks may likewise nest at most 256 levels; deeper source produces
  `E0102` rather than a host recursion failure.
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

Prototype 0.8 has no source-level pointers, dynamic allocation, ownership, references, globals, or concurrency, so it makes no permanent memory-model decision. Mutable locals, fixed arrays, and local structures lower to private stack slots; that is an implementation detail, not a source reference model. Integer literals are range checked, array accesses are guarded, and arithmetic uses the provisional, defined wrapping behavior described above. Structure layout, the final bounds and overflow policies, broader variable/control-flow model, textual syntax, OCL ABI, and executable format remain provisional.
