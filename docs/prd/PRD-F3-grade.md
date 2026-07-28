# PRD F3 — Model grading + contract verification

## Goal
Use a local LLM to obtain `idea`, `skill`, and `description`, but trust it **only after first
proving that the model can follow the output contract**.

## Evidence
`docs/PHASE1-EVIDENCE.md` D-3. The seed checks only whether the model is *installed*
(`libs/ollama.py:47`). It does not check the ability to follow the contract.

## Requirements
1. **Startup smoke test**: inspect the model with a known clean sample + known malicious sample.
   If it fails, disable F3 and operate with F2 only (degraded function, preserved integrity)
2. Cache the smoke result. Rerun when the model tag changes
3. Store `temperature`, model tag, and prompt version with results — results must be reproducible
4. **Prose fields do not contribute to scores.** `description` is for display, not ranking input
   (D-2: even 7b leaked the security prefix into `description`)

## AC (mechanical decision)
- [ ] Injecting a smoke failure disables F3 and the pipeline completes with F2 only
- [ ] Across 20 calls with the same input, `idea` and `skill` standard deviation is 0 (measured evidence: 7b sd=0.000)
- [ ] Result rows contain model tag, temperature, and prompt version
- [ ] Test that changing `description` to an arbitrary string does not change ranking order

## Record status (2026-07-28)

**Superseded by F6.** This PRD's "startup smoke test" requirement (1) is what F6
(`gitseed/grade/smoke.py`, `run_smoke`, wired into `gitseed/application.py`'s `execute`) actually
built, done better than described here: a model that is unreachable *or* unusable — wrong output
shape, false positives on a known-clean sample, non-deterministic scores — degrades the run
visibly (F2-only, `model coverage: absent`) instead of failing per-candidate the way this PRD's
"five checks, startup integration" framing implied. Treat F6 as the current record for this
requirement; this PRD is the historical target it grew from.

**Model-tag caching (requirement 2, "cache the smoke result, rerun when the model tag changes"):
ruled out.** No measured problem sits behind it — nobody has shown the smoke test's cost is worth
avoiding on repeat runs. Nothing in this project ships on an unmeasured performance argument; see
ADR-0007 (`docs/adr/ADR-0007-scoring-before-seam.md`) and this same PRD's own falsification
standard applied elsewhere in this project. Revisit if a measurement shows the smoke test's
repeated cost actually matters.

The "larger deterministic sample" implied by requirement 2 is `grade/smoke.py`'s `CLEAN_SAMPLES =
5`, chosen and justified by a specific false-positive-rate calculation in that module's comments,
not by this PRD's unspecified "20 calls" figure (which requirement 2 of the AC above tests as
call-repetition for the *determinism* check, a different measurement than sample count for the
*clean-file false-positive* check). The two requirements were conflated here; F6's module comment
is the current, measured record for sample count.

## Correction — 2026-07-28 deep review

Requirement 1's "if it fails, disable F3 and operate with F2 only (degraded function,
preserved integrity)" does not hold for every failure mode inside F6's implementation of that
requirement — specifically, an exception raised by the malicious-sample check is not caught,
and can fail the whole run instead of degrading it. See
[`docs/tickets/F3-grade.md`](../tickets/F3-grade.md) and
[issue #50](https://github.com/MongLong0214/gitseed/issues/50) (GS-P1-001).
