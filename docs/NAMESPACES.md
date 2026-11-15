# Namespaces and visibility: OCL 0.17 working implementation

Status: implemented locally, unreleased; independent review and owner acceptance
remain pending. This is prototype behavior, not a permanent ABI or language
stability promise. The approved design is [OCL_0.17_DESIGN.md](OCL_0.17_DESIGN.md).

## Activate in source

Put `module namespaces;` before imports/declarations in **each** participating
file. Comments and whitespace may precede the header. File input retains UTF-8
BOM support. A header may not be repeated or appear later in the file.

```text
// main.ocl
module namespaces;
import arithmetic;
fn main() -> i32 { return arithmetic::answer(); }

// arithmetic.ocl (a separate file)
module namespaces;
pub fn answer() -> i32 { return helper(); }
fn helper() -> i32 { return 42; }
```

No compiler switch, API argument, or environment variable selects this behavior.
Without a header, the file uses the existing [0.16 module rules](MODULES.md).
Legacy-to-namespace and namespace-to-legacy imports are both errors. Migration
must be explicit throughout the reachable graph; legacy declarations are not
automatically public exports. In legacy source, `module`, `namespaces`, and
`pub` keep their old identifier behavior. Opting in reserves `pub`.

## Lookup and exports

Functions, constants, structures, and enums are private unless preceded by `pub`.
Fields of public structs and variants of public enums are accessible together
with their type. There is no per-member privacy in this milestone.

`import arithmetic;` introduces `arithmetic` only in the importing file. It does
not re-export the dependency or make transitive dependencies accessible.
`arithmetic::answer()` selects that direct dependency, regardless of local names.
`::` is one contiguous token. `.` still selects a structure field or enum variant.
Types, literals, constants, and match patterns can be qualified too:
`model::Point`, `model::Point { x: 42 }`, `limits::MAX`, `model::Status.Ready`.

Unqualified lookup proceeds as follows:

1. Value expressions check in-scope parameters/earlier locals first. Calls do
   not call local values; they use the top-level value space as in 0.16.
2. An own-module declaration wins over imports, even if it has the wrong kind.
3. Otherwise consider public declarations of direct imports. A single candidate
   resolves; multiple candidates are ambiguous; none is unknown or inaccessible.
4. Check declaration kind after selecting the name. A constant and function of
   the same imported name are ambiguous, not selected by call syntax.

Types have a separate space from values. Structures/enums share the type space;
functions/constants share the value space. Duplicate own declarations fail.
Equal names in distinct modules are allowed and retain distinct identities.
Import order never breaks ambiguity. Existing local declaration order,
constant shadowing, and function-wide no-local-shadowing still apply.

## Privacy and public signatures

Semantic binding checks the referencing module and the declaration owner before
type analysis/constant evaluation/lowering. A qualified name cannot bypass a
private declaration. Unselected expression branches are checked too.

A public function can call its own private helpers. A public signature or
enum-typed constant cannot expose a private enum type. An imported public enum
used in an interface retains its original identity; this does not re-export it.
A consumer that needs to name that enum must import its owner directly.

Source privacy is not a sandbox or binary secrecy guarantee. Native symbol
visibility and the OCL ABI are not stabilized. Namespace symbols are resolved
at compile time; no runtime namespace objects or lookup tables are emitted.

## Diagnostics

All codes below are provisional 0.x codes, using the existing text/JSON schema.
Errors include the source file, line and column, including dependency locations.

| Code | Meaning |
| --- | --- |
| E0310 | Invalid, duplicate, or late namespace header |
| E0311 | Mixed legacy/namespace import graph |
| E0312 | Unknown module qualifier; add a direct import |
| E0313 | Unknown name/member in the required symbol space |
| E0314 | Private declaration is inaccessible |
| E0315 | Ambiguous imported name; qualify a listed candidate |
| E0316 | Duplicate declaration in one module's symbol space |
| E0317 | Selected declaration has the wrong kind |
| E0318 | Public declaration exposes a private type |

Existing type, assignment, main, depth, and loader diagnostics remain applicable.
Only the entry source defines `main`. Global file/constant/storage limits,
cycle detection, alias handling, and filesystem containment remain in force.

## Local use and verification

```text
python oclc.py check examples/namespaces/main.ocl
python oclc.py emit-ir examples/namespaces/main.ocl
python oclc.py build examples/namespaces/main.ocl -o namespace-example.exe
python oclc.py build examples/namespaces/main.ocl -o namespace-release.exe --release
python -m unittest discover -s tests
python tools/check_namespace_mutations.py
```

The example returns 42 in both build modes. The mutation checker changes code
only in isolated child-process memory; it never rewrites repository files.
It first requires its baseline tests to pass, then checks selected defects with
timeouts. Its selected mutations are evidence, not proof of all possible bugs.

Release/version labels remain at the existing baseline until separately approved.
No packages, nested namespaces, import aliases, re-exports, mixed-mode adapters,
dynamic loading, reflection, or Orbit/2 integration are added.
