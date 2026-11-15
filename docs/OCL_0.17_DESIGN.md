# OCL 0.17: Namespaces and Visibility

Status: **design approved for implementation and local testing; working
implementation uncommitted; independent review and results approval pending**.
Prepared: 2026-11-11. Compatibility baseline: v0.16.0,
`17fec92be48aae5784e4213104823c6418a1a6b7`.

This document proposes prototype behavior, not permanent canonical OCL syntax.
The owner initially approved the summary's core direction, then explicitly
approved the clarified per-file activation, mixed-mode rejection, and detailed
lookup/privacy rules for implementation and local testing. The existing
[language specification](OCL_LANGUAGE_SPEC.md) and [module rules](MODULES.md)
continue to describe the legacy compiler contract. Implementation/local testing
are authorized by the owner's explicit approval; no release, GitHub issue,
or publication is authorized by this draft itself. Current local
behavior is recorded in [NAMESPACES.md](NAMESPACES.md).

## 1. Purpose and boundary

Let separately authored modules use the same declaration names, deliberately
expose an interface, and reject accidental access to implementation details.
The user-facing result is predictable lookup and useful compile-time errors.
Success requires both these new capabilities and unchanged default behavior
for valid 0.16 programs. No runtime namespace objects or dynamic lookup.

In scope: module-qualified names, declaration visibility, name resolution,
diagnostics, compatibility, and tests. Out of scope: packages, subdirectories,
reflection, dynamic loading, separate object compilation, incremental caches,
wildcard imports, re-exports, import aliases, nested namespace declarations,
per-field privacy, methods, runtime globals, or Orbit/2 integration.

Nested namespaces were considered: they would require new declaration and
lookup rules beyond the existing one-directory module graph. Defer them.
This does not reject the requirements document's future dotted library names;
0.17 would remain a smaller prototype, not a redesign of that roadmap.

## 2. Verified current design

Evidence is a source/document inspection, not a fresh execution of the suite.

| Component | Current behavior | Design consequence |
| --- | --- | --- |
| `compiler/modules.py:74` | Iterative root-relative graph loading | Keep loader protections and traversal bounds |
| `compiler/modules.py:123` | Collects enum names across the whole graph | Cannot continue using one flat enum-name set in namespace mode |
| `compiler/modules.py:136` | Concatenates declarations into one `Program` | Preserve module ownership before flattening |
| `compiler/semantic.py:145` | Tables use unqualified declaration names | Namespace mode needs owner-qualified identities |
| `compiler/parser.py:408` | Distinguishes enum variants using known enum names | Qualification must not confuse enums with field access |
| `compiler/parser.py:467` | Classifies named types while parsing | Cross-module type names need resolution-aware handling |
| `compiler/driver.py:9` | `compile_source` rejects imports without filesystem access | Preserve this API boundary |
| `compiler/diagnostics.py:61` | Structured diagnostics use schema version 1 | Avoid an incidental diagnostic-schema redesign |

0.16 exposes all reachable declarations, including transitive dependencies.
It permits forward references and rejects existing declaration collisions
across the whole graph. Types and values have distinct collision rules:
structures/enums share a type space; constants/functions share a value space.
Locals and parameters may shadow constants, but local names cannot be reused
within a function, including sibling blocks. Existing call lookup is separate
from local-value lookup. These details must not be silently simplified.

## 3. Approved source-controlled activation and compatibility

Unconditional private-by-default is incompatible with unchanged 0.16 sources:
an imported unannotated function that is callable today would become private.

### 3.1 Exact proposed header

Each source file opts in by placing this declaration before its imports and
other declarations:

```text
module namespaces;
```

This exact spelling is approved. Whitespace and comments may
precede it or separate its tokens; normal file BOM handling remains unchanged.
The header occurs exactly once and only at the start of a file's token stream.
Duplicate, late, malformed, or unsupported headers are compilation errors;
they must not trigger fallback or change the meaning of already parsed tokens.

Without the header, the file is legacy 0.16. `module` and `namespaces` are
contextual header spellings, not globally reserved words. Existing declarations
named `module`, `namespaces`, or `pub` remain legal wherever 0.16 allowed them.
The two-identifier header cannot be a valid 0.16 top-level declaration, so it
does not reinterpret a previously valid source. Namespace files recognize `pub`
as their mode-specific export modifier; migrating a legacy identifier named
`pub` may require renaming it, and must never happen automatically.

There is no CLI switch, environment variable, filename convention, or API
override to activate namespaces. The proposed `--module-mode` flag and
`module_mode` API parameter from the first draft are withdrawn. Check, emit,
build, and `compile_file` derive mode from the same source header.
`compile_source` recognizes its own header but stays filesystem-free and rejects
imports as before. Existing API call signatures need not change.

### 3.2 Legacy and namespace modules: recommended boundary

For 0.17, require a homogeneous reachable import graph. Every file declares
its own mode; no entry file or dependency switches another file implicitly.

| Importer | Dependency | Result |
| --- | --- | --- |
| Legacy | Legacy | Unchanged 0.16 flat lookup, including transitive declarations |
| Namespace | Namespace | Direct-import lookup and explicit public exports |
| Legacy | Namespace | Reject at the import site: mixed module modes |
| Namespace | Legacy | Reject at the import site: mixed module modes |

Perform mode-consistency checks during graph loading, before declaration
combination, visibility analysis, or lowering. For a mismatch, identify both
files and their modes and explain that the graph must be migrated consistently.
Do not load an alternate file, flatten the dependency, or infer public exports.
Multiple errors follow deterministic source-order graph traversal, as today.

Legacy declarations have no explicit export annotations: their existing flat
availability is preserved in an all-legacy graph. They are NOT automatically
promoted to `pub` declarations or given namespace interfaces. After a file opts
in, unannotated declarations become private; the owner must add `pub` to its
intended interface and make dependencies opt in before that graph compiles.
Removing the header is not an automatic rollback: namespace syntax and exports
must also be restored to valid legacy source.

This restriction intentionally defers mixed-mode interoperability, not support
for old programs. Every unchanged valid 0.16 graph still takes the legacy path.
It does mean migration is graph-wide and may be inconvenient for shared legacy
libraries. Supporting adapters or mixed-mode export rules would be a separate
design decision, not an implementation improvisation.

Alternative considered: allow namespace modules to import legacy modules with
all legacy declarations public. Rejected as the recommendation because legacy
transitive lookup and cross-file references would require a legacy compilation
group with its own export/type-identity rules; exposing only one file would not
preserve legacy behavior. An entry-only header is also not recommended because
it would change dependency semantics without an explicit change in those files.

Legacy mode must retain the 0.16 grammar, reserved-word behavior, resolution,
source behavior, and documented diagnostic contracts. In particular, `pub`
must remain a usable identifier there. New syntax must not auto-switch modes.
No promise of byte-identical executable files or a stable ABI is introduced.

## 4. Proposed namespace-mode semantics

### 4.1 Module ownership and imports

Each resolved source file owns one module namespace. `import math;` continues
to load `math.ocl` from the entry directory and introduces the qualifier `math`
only in the importing module. Qualification does not load files implicitly.
An import is not an export and does not expose a dependency's dependencies.

All current loader rules remain: 256-file cap including entry, no cycles,
duplicate-direct-import detection, diamond loading once, entry-only `main`,
root containment, and exact resolved-path identities. Two genuine files that
differ only by case remain distinct. Aliases and hard links retain the 0.16
identity policy; no case-folded namespace identity is allowed.

The qualifier is the spelling at the import site; the underlying declaration
identity is its resolved owner plus its name and symbol category. Different
valid spellings for the same loaded file must not create distinct enum types.
The entry need not have an identifier-shaped filename; it needs no self qualifier.

### 4.2 Visibility

Top-level functions, constants, structures, and enums are private to their
declaring module unless explicitly public. Private declarations remain fully
usable within that module, including forward references. Visibility is checked
during semantic analysis, before evaluating an inaccessible constant use,
lowering, or native tool invocation, not inferred from LLVM linkage.

The semantic checker uses the referencing module and the declaration's actual
owner. Access is permitted exactly when they are the same module, or when the
owner is directly imported and the declaration is public. A qualified name,
alias spelling of a file, transitive dependency, constant initializer, or an
unselected branch cannot bypass this rule. All bodies and expressions are
checked, including unreachable-at-runtime expression branches. Internal calls
from a public function to its own private helpers remain legal: callers do not
gain direct access to those helpers. No check is deferred to runtime.

A public function may use private helpers in its body. Its parameter/result
types, however, must not expose a private enum. A public enum-typed constant
must likewise have a public enum type. If that type belongs to another module,
it must be public there and accessible through a direct import by the declaring
module. A consumer needing to name it imports its owner directly; this is not
an implicit re-export. Rejection belongs at the exposing declaration/type use.

Public structs expose all their existing fields; public enums expose all their
variants. Private fields/variants and opaque public types are deferred. Existing
local-only struct restrictions and supported field types remain unchanged.
Entry `main` remains the executable entry independent of its public annotation;
dependency files still cannot define it. Public does not mean a stable native ABI.

### 4.3 Unqualified lookup

Resolution uses the syntactic category of the reference, not overload guessing:

| Reference | First applicable source | Fallback |
| --- | --- | --- |
| Value identifier | In-scope parameter/earlier local | Own value declaration, then public values of direct imports; selected declaration must be a constant |
| Function call | Own value declaration | Public values of direct imports; selected declaration must be a function |
| Named type | Own type | Public types of direct imports |
| Enum variant / match arm | Resolve the enum type first | Check its variant and nominal identity |

Resolve a name in its category before checking whether the selected declaration
supports that operation. A wrong-kind own value declaration must not cause a
call to silently fall through to a same-named imported function. Scalar built-in
types retain their existing treatment. No new implicit conversions or overloads.

An own-module declaration takes precedence over imported candidates. At the
imported tier, zero accessible candidates means unresolved or inaccessible;
one means resolved; more than one means ambiguity, never first-import-wins.
Only public candidates participate in ambiguity. If none is public but a
matching private declaration exists in a direct dependency, report inaccessible.
Never search unrelated or merely transitive modules to make a reference succeed.

Candidate collection uses the shared value space for constants/functions and
the shared type space for structures/enums. Thus an imported public constant
and imported public function with the same name are ambiguous even at a call
site; filtering by kind must not silently choose the function. A unique
wrong-kind candidate produces a wrong-kind diagnostic. Own declarations win
even when private or of the wrong kind; failure does not retry imported names.
Within the imported tier, deduplicate by declaration identity, then sort
diagnostic candidates by exact qualifier and declaration spelling (ASCII,
case-sensitive), never by filesystem enumeration or import order.

The explicit distinction between local value lookup and function-call lookup
remains. Local declaration order, self-reference rejection, constant shadowing,
and function-wide no-local-shadowing remain as in 0.16.

Concrete namespace-mode resolution cases (fragments, not full programs):

| Situation | Required result |
| --- | --- |
| Local `count`, own constant `count`, imported constant `count` | Bare `count` selects the in-scope local |
| Own `initialize`, imports `a` and `b` both export `initialize` | Bare call selects own function |
| No own `initialize`, `a` and `b` both export it | Bare call is ambiguous regardless of import order |
| Same imports, explicit `a::initialize()` | Select `a` only; still check public visibility |
| Local `a`, direct import `a` | Bare `a` is the local; `a::initialize()` uses the import |
| Local `initialize`, imported function `initialize` | `initialize()` resolves in the top-level value space; locals are not callable values |
| Own constant `initialize`, imported function `initialize` | Call is a wrong-kind error, not fallback |
| One public imported `count`, another private imported `count` | Public constant wins; private one is not an ambiguity candidate |
| `a` imports `b`, caller imports only `a` | Caller cannot name `b` or its declarations without importing `b` |

These rules apply only after explicit namespace opt-in. The legacy path retains
its existing flat lookup and diagnostics; no new precedence rule is backported.

### 4.4 Qualified lookup and collisions

A qualified reference selects a direct-import namespace, then a declaration in
the required category. Reject an unknown qualifier, unknown member, private
member, or wrong declaration kind separately. Qualification cannot bypass
privacy and never refers to a local object.

Qualified references cover function calls, constants, named local/parameter/
result types, struct literals, enum values, and match arms. Qualified constants
remain immutable; assigning to them must produce a diagnostic, never a crash.
There is no module-qualified mutable global storage in this milestone.

Within one module, keep the existing type/value collision rules and reject
duplicates. Across modules, equal names are legal and have distinct identities.
Two public `initialize` functions are legal until an unqualified call is
ambiguous; explicit qualification chooses one. Own names can hide imports;
qualification remains available. No local-shadowing policy is broadened.

With the approved-in-principle `::`, qualifier names occupy a separate syntactic
space: a local `math` does not prevent `math::initialize()`.

## 5. Syntax record and proposed complete form

| Decision | Alternatives | Recommendation and tradeoff |
| --- | --- | --- |
| Qualification | `math::initialize()`; `math.initialize()` | `::` approved in principle; no replacement of existing variant/field dot |
| Export annotation | `pub fn`; `public fn`; separate export list | `pub` approved in principle; new mode-specific keyword |
| Explicit private marker | `private` modifier; absence of `pub` | Absence of `pub`: no redundant keyword in this milestone |
| Namespace declaration | Explicit namespace blocks; file-defined namespace | File-defined only: matches loader ownership without extra hierarchy |
| Activation | Per-file header; entry-only header; CLI/API mode | Per-file `module namespaces;` recommended; entry-only mode propagation and compiler switches rejected as recommendations |

Proposed complete two-file example:

```text
// main.ocl
module namespaces;
import math;
fn main() -> i32 { return math::initialize(); }

// math.ocl (a separate file)
module namespaces;
pub fn initialize() -> i32 { return helper(); }
fn helper() -> i32 { return 42; }
```

`math::helper()` from main must be a private-access diagnostic, even though
`initialize` can call it internally. Unqualified `initialize()` is also legal
here because there is exactly one public imported candidate.

Additional reference forms are `math::LIMIT`, `math::Point { x: 20, y: 22 }`,
and `math::Status.Ready`. The `pub` modifier is permitted once immediately before
`fn`, `const`, `struct`, or `enum`, and nowhere else. It cannot modify an import,
header, local, field, or variant. In namespace mode it is reserved; in legacy
mode it retains its 0.16 identifier behavior.

These are proposed 0.17 forms, not valid 0.16 examples. `module::Enum.Variant`
keeps the existing variant dot; it is not a nested namespace. A qualified type
must work in every currently supported type position. Repeated namespace paths,
member chains outside existing grammar, and modifiers on locals are rejected.

## 6. Diagnostic contract

New diagnostic codes will be allocated only after approval and an inventory of
existing codes. Existing errors must not be renumbered for this milestone.

| Condition | Primary location | Actionable message content |
| --- | --- | --- |
| Unknown qualifier | Qualifier token | Name the missing import; suggest adding a direct import |
| Unknown member | Member token | Name the selected module and absent declaration |
| Private access | Member/use token | Identify private declaration and owner; suggest a public API |
| Ambiguous unqualified use | Referenced name | List accessible qualified candidates deterministically |
| Duplicate own declaration | Second declaration | Identify first declaration and symbol category |
| Private type exposed publicly | Type use in public declaration | Name the type that must be public or the declaration that must remain private |
| Wrong-kind reference | Referenced member | Explain expected and actual declaration categories |
| Unsupported syntax/mode | Offending token or argument | Explain supported form/mode without silently changing behavior |
| Mixed import modes | Import's module-name token | Name importer/dependency modes; require deliberate graph migration |
| Duplicate/late/invalid header | Offending header token | Require a single leading `module namespaces;` or no header |

Retain source filename, line, column, source excerpt/caret, CLI exit behavior,
and JSON schema version 1. Declaration locations may be included in the message
without adding a new required JSON field. Candidate order is stable, never set
iteration order. No path-dependent suggestion should change name resolution.
Malformed and inaccessible references must produce diagnostics, not host
`KeyError`, `AttributeError`, recursion failures, or native compiler failures.

## 7. Engineering, security, and performance considerations

Preserve module ownership through parsing and semantic analysis. Bind names to
stable declaration identities before constant evaluation and code generation;
do not patch source strings or concatenate source files to fake namespaces.
Enum and structure identities must include their owner, including in constants,
signatures, comparisons, and match exhaustiveness. Qualification must not add
unaccounted recursive parsing depth.

Likely affected files: `modules.py` (graph/ownership), `nodes.py` and `types.py`
(identities), `lexer.py`/`parser.py` (approved syntax), `semantic.py` (resolution,
visibility, and constant evaluation), `codegen.py` (distinct symbols),
`driver.py`/`cli.py` (consistent source-derived behavior), and diagnostic tests. Implementation
decomposition is advisory; do not refactor unrelated compiler paths.

LLVM symbols/types must distinguish equal names in different modules without
collisions with source or generated names. Keep the entry symbol `main`.
Use deterministic, injective internal naming, not randomized hashes, raw host
absolute paths, or import aliases. Check relocation of the same source graph
does not change internal identities. This is not an ABI stabilization.

Build per-module tables and direct-import candidate indexes rather than walking
the whole graph for every name. Keep existing global declaration/storage limits;
do not multiply the constant cap by the number of modules. No runtime registry,
namespace allocation, or lookup should appear in generated code. Compile-time
cost is unknown until measured on a fixed baseline and near-limit graph.

Privacy is a source-language access rule, not a security sandbox or secrecy
guarantee. Source files, binaries, and symbols may reveal private declarations.
Retain filesystem containment protections and test real filesystems where
available. Namespace work does not solve filesystem races or make untrusted
source execution safe. Native test programs must remain time-bounded.

## 8. Verification plan and release gates

All tests below are planned, not run. Counts and platform skips must be reported
from actual runs. Existing 0.16 tests run unchanged in default legacy mode.

| Test group | Protects | Fails when | Does not prove |
| --- | --- | --- | --- |
| Legacy regression, CLI/API, old keyword identifiers | Existing 0.16 behavior | Defaults change, `pub` becomes reserved, imports leak into string API | Every possible old source is covered |
| Header positions, comments, BOM, old identifier spellings, CLI/API parity | Explicit per-file activation | Mode inferred from invocation/dependency, header accepted late, legacy identifiers rejected | Mixed-mode interoperability |
| Both mixed-edge directions, transitive mixed edge, diamond | No implicit conversion or legacy exports | Mixed graph accepted or error points to wrong import | An adapter design, which is deferred |
| Own/direct/transitive/diamond lookup | Documented lookup tiers | Import order chooses a winner or transitive access succeeds | Real filesystem alias behavior |
| Positive and negative visibility by declaration kind | Compile-time access and public signatures | Private read/call/type/literal/pattern succeeds, or public use fails | Binary confidentiality |
| Duplicate and ambiguous names | Collision policy and actionable errors | Duplicate accepted, ambiguity hidden, candidates unstable | Native symbol uniqueness |
| Native same-name functions/constants/types | Correct binding and lowering | Wrong module chosen, symbols collide, enum types merge | Stable ABI |
| Every qualified syntax position | Parser/semantic agreement | Read works but assignment/pattern/type path crashes | Unimplemented syntax options |
| Text/JSON located errors in dependency files | Diagnostic ownership and schema | Wrong file/span, missing correction, traceback | Editor integration |
| Alias/case/containment/limits, actual filesystem where possible | Loader regressions and bounded compilation | Privacy bypass, case collision, escape, lost limits | Skipped host capabilities |
| Repeat/relocation and optimization-mode runs | Determinism and native behavior | Repeated IR differs or O0/O2 behavior diverges | Cross-host byte-identical binaries |
| Depth and hostile malformed cases with timeouts | Crash/hang resistance | New syntax defeats stack/depth guards | Arbitrary host resource exhaustion |

Mutation checks must disable each access guard, make import selection first-wins,
erase module ownership from types/symbols, expose transitives, and bypass public
signature checks. Every non-equivalent mutation must fail a bounded test.
Also remove header/mixed-mode checks, force a dependency to inherit entry mode,
and expose legacy declarations automatically; each must be caught by a test.
Record equivalent survivors with witnesses/reasoning, not a blanket percentage.

Before release:

1. Owner approves compatibility policy, semantics, and exact syntax/API surface.
2. Implement approved behavior and its positive/negative tests together.
3. Run complete regression suite, all existing acceptance programs and new
   namespace examples in both optimization modes; expected outputs are explicit.
4. Give Claude the approved spec, exact base and uncommitted patch, content hashes,
   commands/results, mutations, compatibility changes, and known limitations for
   independent local review. Review does not require committing the work.
5. Resolve/disposition findings and obtain owner approval for the implementation
   and local verification results BEFORE any commit or push. Any necessary
   publication for remote review/CI must be explicitly approved, not inferred.
6. After that approval, commit/push the reviewed content and run the repository's
   full hosted platform/Python matrix. Report skips and simulated coverage
   separately from real-host coverage. Obtain delta review if content changes;
   prepare the acceptance report tied to the final reviewed SHA. Hosted CI is
   pending, not passed, while publishing remains prohibited.
7. Only with explicit release approval, date changelog, merge through the agreed
   gate, tag the merged tree, and verify a clean checkout of the tag.

## 9. Work sequence and rollback

Priority 1: approve mode and language surface; these determine all later work.
Priority 2: ownership and resolution, then parser/semantic enforcement; codegen
must consume resolved identities rather than independently guess meaning.
Priority 3: CLI/API parity, native/negative/regression tests, docs, independent
review. Tests accompany each step; this is not permission to defer testing.

Effort is not yet estimated in hours: ownership/type changes are the largest
unknown, parser surface and mode plumbing next, with mutation and cross-platform
validation a separate required work package. Estimate after the approval choices
are fixed rather than inventing a completion date now.

Before release, revert the feature branch if needed. After release, retain the
legacy path and make a reviewed corrective release; never silently reinterpret
namespace-mode source as legacy or move a published tag. There is no persistent
data migration or website deployment in this milestone.

## 10. Documentation and approval record

Keep `OCL_LANGUAGE_SPEC.md` as the implemented prototype reference and
`MODULES.md` as its module companion. This proposal stays separate until approved.
If later split into `docs/language/{modules,namespaces,visibility,diagnostics}.md`,
move content with links rather than create competing normative copies. Label
each document as implemented prototype, approved future design, or proposal;
prototype behavior must not be presented as a permanent language guarantee.

Approved in principle from the summary: unchanged legacy default, explicit
opt-in, `::`, private-by-default, `pub`, ambiguity errors, and deferring nested
namespaces/re-exports. This is not line-by-line approval of the first draft.

Clarifications subsequently approved for implementation/local testing:

- D1: approve the exact per-file `module namespaces;` header, its placement and
  contextual recognition rules, with no compiler-mode override (section 3.1).
- D2: approve rejection of mixed legacy/namespace graphs in both directions,
  with no automatic public exports from legacy files (section 3.2).
- D3: approve the detailed lookup/shadowing and semantic visibility rules,
  including wrong-kind resolution, public type exposure, and examples
  (sections 4 and 5).

The owner explicitly authorized proceeding under D1-D3. No commits, pushes,
tags, or releases are authorized now. Implementation and local verification
may proceed, not publication; the separate implementation/results approval
gate in section 8 still applies. No independent approval of the code is implied.
