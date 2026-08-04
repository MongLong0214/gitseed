# PRD F2 — Deterministic security screen (no LLM)

## Goal
Extract risk signals with deterministic rules **before** calling an LLM. This layer owns
malicious classification.

## Evidence
`docs/PHASE1-EVIDENCE.md` D-3: the seed delegates malicious classification to a single LLM call.
When the model is small (1.5b), it classifies 64% of clean code as malicious, and `security_flag`
contradicts `security_reason`. The rate is 0% with 7b and 32b, but **the seed does not enforce
which model is used.**

## Requirements
1. Decide from GitHub API metadata only (no clone required)
2. Signals: installation scripts (`curl|sh` pattern), obfuscation (base64 blob or long hex), network-call
   targets, known malicious package names in dependency manifests, postinstall hooks
3. Every signal cites **which file and line** it came from. Discard signals without citations
4. Output is `{signals: [...], severity: none|low|high}` — do not collapse it into one boolean

## AC (mechanical decision)
- [ ] 0 cases of `severity != none` across 10 known clean samples
- [ ] Tests catch each known malicious-pattern sample (installation script, obfuscation, postinstall)
- [ ] Every signal has a `path#line` citation. A signal without a citation fails the test
- [ ] 0 LLM calls — this module does not call a network or model

## Correction — 2026-07-28 deep review

This PRD's Goal states screening "extract[s] risk signals... before calling an LLM"; the
signal rules themselves (requirement 2) are implemented and correctly tested at the module
level. What is not true yet is that a live run's file selection reaches those rules for the
file types requirement 2 names — `package.json` postinstall/preinstall detection in
particular. See
[`docs/tickets/F2-screen.md#correction-2026-07-28-deep-review`](../tickets/F2-screen.md) for
the finding and [issue #45](https://github.com/MongLong0214/gitseed/issues/45) (GS-P0-001),
[issue #48](https://github.com/MongLong0214/gitseed/issues/48) (GS-P0-008, partial coverage
not reflected in severity), and
[issue #49](https://github.com/MongLong0214/gitseed/issues/49) (GS-P1-018, adversarial
selection order).

## Record status (2026-07-28)

**The `known malicious package names in dependency manifests` signal (`dep`): ruled out, not
implemented.** `gitseed/screen/signals.py` has no `dep` kind and no test names one — this predates
F11's discipline, not an oversight. F11 established that a security claim resting only on model
output is never a finding; the same reasoning applies to a `dep` signal built the way this PRD
describes it (an assertion that a *named dependency* is unsafe). That assertion is either a
narrow, deterministic lockfile fact — "this exact pinned version matches a known-bad entry in a
list this tool ships and can cite" — which is not what this requirement describes, or it is an
inference about a package's trustworthiness, which F11's discipline forbids from becoming a
finding regardless of whether a model or a hand-written heuristic produced it. See commit
trailers for the formal `Ruled-out:` record. Reopen if a deterministic formulation — a shipped,
citable list, not a heuristic — is proposed.
