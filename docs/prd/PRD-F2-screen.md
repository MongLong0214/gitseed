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
