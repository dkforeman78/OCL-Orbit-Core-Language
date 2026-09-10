# OCL 0.15 — Structured Diagnostics

Status: implementation authorized by the design authority's instruction to proceed through the next work.
Baseline: v0.14.0. Independent audit and release gates remain in place.

Scope: add `--diagnostic-format=text|json` to every command; cover source, usage,
toolchain, I/O and explicit internal-invariant failures. Keep existing exit codes
and successful output behavior. Fix tab caret placement and LF-only source-line
selection. Preserve source-language rules. No error recovery, multiple-error
accumulation, LSP server, automatic edits, or speculative suggestions.

Implementation: versioned diagnostic serializer, CLI error routing, targeted
source-location and error-channel tests, full regression and hosted verification.
Review: mutation-test selection of JSON, required fields, positions, failure exit
codes, stream isolation, and external-tool failure wrapping. All fourteen native
acceptance programs must still pass at both optimization levels.
