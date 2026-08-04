# PRD F3 — Stable bounded grading with a local 32B model

- Status: Accepted (2026-08-04)
- Decision: [ADR-0014](../adr/ADR-0014-bound-local-model-grading-work.md)
- Owning stage: F3 local-model grading

## Problem

GitSeed's deterministic scanner is bounded at 500,000 selected source bytes, but the same
bytes are concatenated into one unbounded model prompt. A live dry run using
`qwen2.5-coder:32b` timed out at 180 seconds for both a 42,036-byte candidate and a
386,939-byte candidate. The adapter also leaves generated tokens unbounded. The existing
120-second test verifies only argument plumbing and does not execute the model.

## Goal

Make 32B candidate grading complete predictably on the supported local machine without
weakening deterministic screening, silently changing models, or hiding omitted model
evidence.

## Non-goals

- No change to security signals, source-fetch caps, recommendation rules, or scoring.
- No retry, concurrent grading, smaller-model fallback, summarizer model, or remote service.
- No claim that a bounded model prompt is a full-repository code review.
- No GitHub write, approval-flow change, artifact-schema migration, or main-branch merge.

## Requirements

### R1 — Separate screening and grading budgets

All fetched files continue through deterministic screening under the existing source policy.
Only the digest passed to `Grader.evaluate` is reduced. The reduction must not mutate
`FetchedFiles`, `SourceCoverage`, skipped-file evidence, deterministic signals, or severity.

### R2 — Deterministic representative digest

The model digest must be at most 23,000 UTF-8 bytes, leaving at least 1,000 bytes for the
grading instruction and structural overhead under ADR-0014's 24,000-byte complete-prompt
cap.

For a candidate whose selected files exceed the budget:

1. choose at most 16 files at deterministic, evenly spaced indices over the existing ordered
   file sequence; for `n > 16`, select index `floor(i * (n - 1) / 15)` for each integer
   `i` from 0 through 15, which includes the first and last file;
2. divide the remaining content budget equally across chosen files;
3. keep a UTF-8-safe prefix from each chosen file;
4. retain the repository name and each chosen file path; and
5. include machine-readable counts for selected files, sampled files, selected bytes,
   included bytes, and omitted bytes, plus an explicit marker on every shortened file.

When all files fit, preserve all file contents and report zero omitted bytes. Identical input
must produce byte-identical model digests. If repository/path metadata alone cannot fit, fail
before an Ollama HTTP call with a specific oversized-input error; do not silently remove
structural evidence.

### R3 — Bound and version the Ollama request

Every grade and maliciousness request uses JSON format, non-streaming output,
`temperature=0`, and `num_predict=128`. The adapter rejects any complete user prompt over
24,000 UTF-8 bytes before transport invocation. Candidate grades record prompt version
`cli-v2-bounded`.

### R4 — Use a measured deadline

The default `--grade-timeout` is 240 seconds. A positive explicit override remains
authoritative. Timeout or a response cut off before valid contract JSON remains a visible
candidate failure and makes the run incomplete.

### R5 — Operator-visible documentation

The CLI help and README state the 240-second default, bounded-evidence behavior, and that the
timeout applies per model response. They must not tell an operator that raising the timeout
can make an oversized unbounded prompt safe.

## Acceptance criteria

- **AC1:** a RED test demonstrates that current `_digest` exceeds 23,000 bytes for a large
  candidate; GREEN proves the new digest and final Ollama prompt remain within both caps.
- **AC2:** tests prove deterministic even-spacing, first/last inclusion, UTF-8-safe prefixes,
  explicit omission counts/markers, and no omission when the input fits.
- **AC3:** a spy scanner sees the full original file set while a spy grader sees only the
  bounded digest; coverage, signals, severity, and skipped-file evidence are unchanged.
- **AC4:** request inspection proves `num_predict=128`, `temperature=0`, JSON format, and
  non-streaming output for both model methods.
- **AC5:** an oversized structural prompt makes zero HTTP calls and becomes a named,
  fail-closed incomplete candidate rather than a timeout or silent grade.
- **AC6:** the parser defaults to 240 seconds, preserves an explicit positive override, and
  rejects non-positive values.
- **AC7:** a response stopped at 128 tokens without valid contract JSON is ungraded and makes
  the run incomplete; no numeric fallback is synthesized.
- **AC8:** focused tests, the full test suite, and README/help assertions pass on one exact
  candidate head.
- **AC9:** authenticated, dry-run-only live runs against
  `repo:eevajonnapanula/neule.art` and `repo:raydeStar/sir-thaddeus` using
  `qwen2.5-coder:32b` and the default deadline produce valid grades without timeout. Artifacts
  and the local run store are written only to an isolated temporary directory; no star,
  follow, commit, push, or other GitHub write occurs.

## Failure and stop conditions

- Any reduction in deterministic screening coverage or change in verdict/scoring behavior.
- Any prompt over 24,000 bytes reaches Ollama.
- Any implicit retry or different-model fallback.
- Either exact live candidate times out or returns no valid grade.
- Candidate head changes after review or test evidence is collected; all affected evidence
  becomes stale and must be rerun.
