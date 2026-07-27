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
