# OCL 0.17: Claude review coverage follow-up

Date: 2026-11-15. Local, uncommitted follow-up only.
Base/HEAD/main remain `17fec92be48aae5784e4213104823c6418a1a6b7`.

## Scope and integrity

Claude's independent review found no implementation defects and identified five
Low-severity coverage gaps. The owner approved closing all five and local
verification, without commits, pushes, tags, or releases.

This follow-up adds five test methods and six mutation cases. No existing test
was weakened or removed; no timeout, compiler source, CI setting, release label,
README, changelog, or branding asset was changed. All 14 Python files under
`compiler/` have identical SHA-256 hashes before and after this follow-up.
The already-uncommitted compiler implementation remains untouched.

## Coverage added

| Review gap | New test | Mutation result |
| --- | --- | --- |
| Index-assignment index not bound | `test_constant_names_in_index_assignment_indices` | Omitting `index` rejects valid local/qualified constant indices; both witnesses fail |
| Child block scope can leak | `test_block_constant_shadow_does_not_escape` | Removing either ordinary-block or loop-body scope copying rejects the valid post-block constant reference |
| Private-candidate diagnostic ordering | `test_private_candidate_message_order_is_stable` | Removing sorted import-index construction changes E0314 under reversed imports; test fails |
| Legacy qualification parse rejection | `test_legacy_qualification_is_a_parse_error` | Enabling `::` in legacy parsing changes E0100 for calls, types, and constants; all three checks fail |
| Struct used as enum diagnostic | `test_struct_type_used_as_enum_reports_source_name` | Removing the enum-kind guard changes E0317 and exposes the internal identity; test fails |

The first two tests compile in the test process, then run native witnesses in
both O0 and O2. This makes in-memory mutations observable before subprocess
builds, while native runs separately confirm exit 42. The other three tests pin
diagnostic codes/messages. The struct test explicitly rejects `ocl.ns.` in its
message. The legacy test covers invalid source only; no new legacy semantics.

## Local results

Host: Windows, Python 3.14.5, LLVM/Clang 22.1.8.

- `python tools/check_clang_version.py`: exit 0; LLVM minimum satisfied.
- `python tools/check_namespace_mutations.py` with `OCL_REQUIRE_CLANG=1`:
  exit 0, all 54 namespace tests passed, all 18 selected mutations caught.
  This includes all six new variants and all twelve original mutations.
- Final `python -m unittest discover -s tests` with `OCL_REQUIRE_CLANG=1`:
  exit 0; 410 tests run in 101.783 seconds, 407 passed and 3 host-related skips.
  This reran all 16 acceptance programs in both build modes, each returning 42.
- `python -m compileall -q tests/test_namespaces.py tools/check_namespace_mutations.py`:
  exit 0. New-file whitespace checks passed (only normal Git LF/CRLF notices).
- Compiler hash comparison: all 14 files byte-identical to the independently
  reviewed implementation. `git diff --check`: no whitespace errors.

Mutation failures for the index/scope cases are expected compiler diagnostics
raised while compiling valid witnesses, not crashes or mutation setup failures.
The original three equivalent mutants identified by Claude were not turned into
artificial failures or counted as newly caught defects.

## Current fingerprints

| File | SHA-256 |
| --- | --- |
| `compiler/namespaces.py` (unchanged) | `459106D9F2C109784BA4A384E2495A58FD56B30926F3D3FC0D62722E4D009688` |
| `tests/test_namespaces.py` | `9A483BB57A28D0DC68C8DC84FE6A1AFF82DCEDE24E94FC341A61E2A0B3A47040` |
| `tools/check_namespace_mutations.py` | `F1446561DA480C0C89DAB2CB4F20CE14FDF2E7EBC192B1C57248DFE0AB9A4844` |

The test/checker hashes supersede those in the initial verification report.
Compiler fingerprint and language semantics do not change.

## Remaining gates

Claude's delta review has not run yet. Hosted CI has not run on this uncommitted
work; no Linux/macOS or Python 3.11/3.12 result is claimed. The in-memory mutation
checker cannot mutate subprocess builds; the in-process compilation and
diagnostic assertions catch these mutations, while ordinary native tests verify
the unmodified compiler. No broader mutation-coverage claim is made.

Review this test/checker delta and report without changing canonical syntax or
expanding into 0.18. Reviewers must include untracked files: a plain `git diff`
does not include the new 0.17 files. Exclude unrelated branding work.
Owner approval of results is still required before any commit or publication.
