# OCL 0.17 local implementation and verification

Historical initial implementation report. For the 2026-11-15 test-only review
follow-up, current test/checker fingerprints, and latest verification, see
[OCL_0.17_REVIEW_DELTA.md](OCL_0.17_REVIEW_DELTA.md).

Date: 2026-11-11. Status: local work only; no release approval implied.
Base/unchanged main: `17fec92be48aae5784e4213104823c6418a1a6b7` (v0.16.0).
Working branch: `codex/ocl-0.17-design`. Changes are uncommitted and unstaged.

## Changes and test-policy disclosure

- Added 49 namespace tests in `tests/test_namespaces.py`; no existing test file
  was changed. These include native O0/O2 execution and negative diagnostics.
- Corrected a new test's expected enum-comparison code to the existing `E0211`;
  did not alter the compiler's established comparison diagnostic.
- Added a bounded, in-memory mutation checker; it does not rewrite source files.
- Did not weaken tests, alter existing timeouts, change CI configuration, bump
  version labels, or edit the release changelog.
- Branding assets are unrelated existing work and were left untouched.

## Implemented behavior

The approved `module namespaces;` header activates each source file explicitly.
Unmarked files remain legacy. Mixed graphs fail at their import edge. `pub`
exports declarations; qualified/unqualified resolution obeys the approved
precedence, ambiguity, and privacy rules. Public signatures reject private types.

The semantic binder retains module ownership until names/types are resolved,
then supplies collision-free AST identities to the existing type checker and
backend. It indexes direct imports once, uses iterative expression/block walks,
and preserves source locations. No source rewriting or runtime namespace lookup.

New diagnostics are E0310-E0318, documented in [NAMESPACES.md](NAMESPACES.md).
The new multi-file acceptance program is `examples/namespaces/main.ocl`.

## Observed local results

Host: Windows, Python 3.14.5, LLVM/Clang 22.1.8.

| Command/check | Observed result |
| --- | --- |
| `python tools/check_clang_version.py` | Exit 0; LLVM 18 minimum satisfied |
| Initial `python -m unittest discover -s tests` | 356 tests run, success, 3 skips |
| Intermediate full regression | 404 tests run, success, 3 skips, before the final duplicate-location regression |
| Final full regression with `OCL_REQUIRE_CLANG=1` | Exit 0; 405 tests run in 97.913 seconds: 402 passed, 3 skipped |
| `python tools/check_namespace_mutations.py` | Exit 0; 49 baseline namespace tests succeed; all 12 selected mutations caught |
| `python -m compileall -q compiler tools/check_namespace_mutations.py` | Exit 0 |
| `git diff --check` and checks on the three new Python files | No whitespace errors; only Git's normal LF-to-CRLF notices |

The full regression includes the existing 14 single-file acceptance programs
and the legacy module example in both optimization modes. The namespace suite
adds its example in both modes. Each expects exit 42; native runs are bounded.

The namespace tests also exercise 20,000 additions, maximum accepted block and
expression nesting from deep Python callers, recursion-limit restoration,
source relocation, private access in an unselected branch, and text/JSON
diagnostic ownership across CLI commands. Real case-sensitive directories were
available here: `math.ocl` and `Math.ocl` remained distinct and ran natively.
The new namespace containment test simulates resolution; it does not claim
that this host supports symlink creation.

## Mutation results

All twelve selected defects fail tests: qualified privacy removed, unqualified
privacy removed, ambiguity guard removed, own-name precedence removed, public
type exposure allowed, declaration-owner identity erased, own duplicates allowed,
wrong-kind check removed, mixed-mode guard removed, late/duplicate header guard
removed, global module cap removed, and constant-owner binding removed.

Ten mutations fail assertions; two fail a positive compilation witness with a
diagnostic (duplicate symbols or an unbound constant). They are not equivalent
mutants. The harness first requires its unmodified selected suite to pass and
treats malformed replacement patterns as harness errors. Mutation success is
limited to these selected defects, not proof against arbitrary defects.

## Review inputs and limits

Review the uncommitted diff against the base above AND the untracked namespace
source, tests, example, docs, and mutation checker. A plain `git diff` omits those
new files. Do not include the unrelated `assets/` directory.

Key SHA-256 fingerprints of the tested working files:

| File | SHA-256 |
| --- | --- |
| `compiler/namespaces.py` | `459106D9F2C109784BA4A384E2495A58FD56B30926F3D3FC0D62722E4D009688` |
| `tests/test_namespaces.py` | `7F54FDEDD73F025DAD3B1C6B96FB57C17F5195A737CFE15CED61FD86BFC1FAA9` |
| `tools/check_namespace_mutations.py` | `4DC03A81E6B24D96574EEF686AD06FB788DCBB43B89EE53F89342EAC03B91ECF` |

Priority independent-review areas: namespace ownership through constants and
types; parser field/enum disambiguation; own/imported name precedence;
private-access checks in every consumer path; mixed-mode loader checks;
filesystem aliases/case; and legacy compatibility. Claude has not independently
reviewed this implementation yet.

No fresh Linux/macOS or Python 3.11/3.12 runs were performed. Hosted CI has not
run on this uncommitted work. No cross-platform pass is claimed. Performance
has not been benchmarked beyond executing the bounded test cases.

The verification skill's check detector reported no recognized checks for this
repository; the declared Python unittest workflow was run directly instead.
Its automated readiness receipt is therefore not evidence for this work.

Owner approval of the implementation/results is required before any commit or
push. Hosted CI and final review gates remain before separate release approval.
No commit, push, tag, PR, release, or website change was performed.
