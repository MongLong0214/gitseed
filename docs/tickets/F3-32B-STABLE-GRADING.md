# F3 — Stable bounded 32B grading implementation ticket

- Status: Verified (2026-08-04)
- Exact base SHA: `fe87559f434ee3499254113d65197850e0d1b45d`
- ADR: `docs/adr/ADR-0014-bound-local-model-grading-work.md`
  (`0668fc472cb7b7342dccb947c748651508cb2844e0bd5ae96be00bb037a37ad6`)
- PRD: `docs/prd/PRD-F3-32B-STABLE-GRADING.md`
  (`c97a65f4055f7109ad3ed217b1d4ec2e0c85a924035eb5d47f23d51d5352ada3`)
- Dependency: ADR and PRD status `Accepted`; no other implementation dependency

## Scope and ownership

The implementer owns only:

- `gitseed/pipeline/run.py`: `_digest` and new private digest-budget helpers/constants;
- `gitseed/cli.py`: `DEFAULT_GRADE_TIMEOUT`, `OllamaGrader.evaluate`,
  `OllamaGrader.flags_malicious`, `OllamaGrader._ask`, and new private prompt-limit
  constants/errors;
- `tests/test_pipeline.py`: bounded-digest and screening-separation tests;
- `tests/test_model_choice.py`: Ollama request/prompt contract tests;
- `tests/test_cli.py`: timeout default/override and CLI incomplete-state tests; and
- `README.md`: live grading limits and timeout guidance.

Do not modify `HANDOFF.md`, artifact/storage schemas, collection policy, security signals,
score/recommendation code, approval/actions, GitHub writers, or any file outside this list.

## Atomic work items

### T1 — Bound the model digest without reducing screening

**RED tests first**

Add tests named:

- `test_large_model_digest_is_bounded_and_declares_omissions`
- `test_bounded_digest_samples_evenly_and_is_utf8_safe`
- `test_small_model_digest_preserves_all_evidence`
- `test_screening_receives_all_files_before_grading_receives_a_bounded_digest`

Run only those tests and capture their expected failures against the exact base. The first
three must fail because `_digest` currently concatenates all files; the separation test must
fail because no bounded grading representation exists.

**Minimum GREEN**

In `gitseed/pipeline/run.py`, add `MODEL_DIGEST_BYTE_CAP=23_000`,
`MODEL_DIGEST_FILE_CAP=16`, `ModelInputTooLarge`, and private helpers for even spacing and a
UTF-8-safe prefix. Keep scanning over the original `files`. Change only the digest constructed
immediately before `grader.evaluate`:

- cap the digest at 23,000 UTF-8 bytes;
- if the complete digest fits, retain every repository/path/content byte and append a
  zero-omission accounting record that is itself included in the cap;
- otherwise select 16 indices using `floor(i * (n - 1) / 15)`, `i=0..15`;
- divide remaining content bytes deterministically, assigning any remainder to lower selected
  indices;
- truncate only at valid UTF-8 boundaries;
- declare selected/sampled file counts and selected/included/omitted content-byte counts; and
- mark each shortened file with original and included content-byte counts.

If mandatory repository/path/accounting structure alone exceeds the cap, raise a named
input-size exception. Do not send partial structural metadata.

### T2 — Bound and version every Ollama generation

**RED tests first**

Add tests named:

- `test_ollama_generation_bounds_output_and_records_bounded_prompt_version`
- `test_ollama_rejects_an_oversized_complete_prompt_without_http`
- `test_output_cut_at_the_generation_limit_is_not_a_grade`

Observe the first two fail because `_ask` has neither limit; the third must prove invalid JSON
is rejected and propagated to the existing incomplete-candidate path.

**Minimum GREEN**

- In `gitseed/cli.py`, add `MODEL_PROMPT_BYTE_CAP=24_000` and
  `MODEL_OUTPUT_TOKEN_CAP=128`.
- Set `num_predict=MODEL_OUTPUT_TOKEN_CAP` on both `evaluate` and `flags_malicious`
  generation requests.
- Before HTTP, reject a complete user prompt over 24,000 UTF-8 bytes with the named
  input-size exception.
- Preserve JSON format, `stream=False`, and `temperature=0`.
- Record successful candidate grades as `prompt_version="cli-v2-bounded"`.
- Do not retry, switch models, synthesize scores, or catch the failure outside the existing
  per-candidate incomplete-state boundary.

### T3 — Set the measured deadline and operator contract

**RED tests first**

Update `test_grade_timeout_defaults_to_a_32b_compatible_budget_and_accepts_an_override` to
expect 240 and confirm it fails against 120. Add a help/README assertion for bounded evidence
and the per-response meaning of the deadline.

**Minimum GREEN**

Set `DEFAULT_GRADE_TIMEOUT=240`. Preserve a positive explicit override and existing rejection
of zero/negative values. Update README/help wording; do not recommend timeout increases as a
remedy for oversized prompts.

## Acceptance-to-test traceability

| PRD acceptance | Owned tests/evidence |
|---|---|
| AC1 | T1 large-digest RED/GREEN + T2 oversized-prompt test |
| AC2 | T1 even-spacing, UTF-8, small-input tests |
| AC3 | T1 screening/grading separation test + existing screening suite |
| AC4 | T2 request inspection test |
| AC5 | T2 zero-HTTP oversized-prompt test + CLI incomplete-state test |
| AC6 | T3 parser default/override/non-positive tests |
| AC7 | T2 generation-limit invalid-JSON test + existing pipeline failure test |
| AC8 | focused and full commands below on exact candidate head |
| AC9 | two live dry-run receipts below on the same exact candidate head |

## Verification commands and expected results

### Focused

```bash
python3 -m pytest -q tests/test_pipeline.py tests/test_model_choice.py tests/test_cli.py
```

Expected: exit 0; all named new tests pass; existing tests in those files remain green.

### Full

```bash
python3 -m pytest -q
```

Expected: exit 0 with no skip introduced for this ticket.

### CLI help

```bash
python3 -m gitseed radar --help
```

Expected: exit 0 and a 240-second per-response default plus bounded grading evidence is
described consistently with README.

### Live 32B dry-run

Use a newly created `/private/tmp/gitseed-32b-acceptance.*` directory. Run each exact query
once with authenticated GitHub reads, `OLLAMA_MODEL=qwen2.5-coder:32b`, default grade timeout,
`--limit 1`, `--dry-run`, and isolated `--artifact`, `--store`, and `--run-id` values:

```text
repo:eevajonnapanula/neule.art
repo:raydeStar/sir-thaddeus
```

Expected for each: exit 0, model smoke PASS, one non-null grade with model
`qwen2.5-coder:32b` and prompt version `cli-v2-bounded`, no model timeout/failure, and no
star/follow or other GitHub write. Record elapsed wall time, artifact path, artifact SHA-256,
model result, failure count, and run-store row.

## Review and evidence invalidation

- After implementation, record the exact candidate head before review or QA.
- Independent review checks diff scope, byte accounting, UTF-8 boundaries, `n=0/1/2/16/17`,
  extremely long paths, malformed/cut-off JSON, wrong model, timeout, partial source state,
  and absence of fallback/retry/external writes.
- Mutate or remove each new production guard and show its named test fails; reading test count
  alone is insufficient.
- Any candidate-head change invalidates focused, full, review, and both live receipts. Rerun
  all four lanes on the new exact head.

## Stop conditions

Stop and return a blocker receipt if the implementation needs an artifact schema change, a
new dependency, a file outside ownership, weaker screening, a retry/fallback, or a GitHub
write. Do not commit, push, merge, or edit the pre-existing `HANDOFF.md` change.

## Completion receipt

Return at most 2KB containing: exact base/head, changed files, RED failures observed, focused
and full totals, mutation checks, live elapsed times and artifact hashes, no-write evidence,
remaining risks, and `git status --short` proving `HANDOFF.md` was preserved.

## Verified receipt — 2026-08-04

- Candidate content: `gitseed/pipeline/run.py`
  `c8e9df3727751b4481e5c2996cc44e743797cb12420cc70bd2686fd63dfca793`;
  `gitseed/cli.py` `8a5b41dacb6f914c6b8eab3670823dbb8b98dd9615f4812e6512c80b051cd715`.
- RED evidence: unbounded digests reached 80,449 and 204,399 bytes; the old request used
  `cli-v1`, allowed an oversized HTTP call, and defaulted to 120 seconds. A multibyte
  accounting test also caught a 1,355-versus-1,353-byte error before final GREEN.
- Focused: 99 passed. Full: 321 passed. Independent review: PASS with no scope defect.
- Live `qwen2.5-coder:32b`, default 240 seconds, authenticated GitHub reads, dry-run only:
  `eevajonnapanula/neule.art` completed in 128 seconds and `raydeStar/sir-thaddeus` in
  127 seconds. Both artifacts are complete, smoke PASS, have one `cli-v2-bounded` grade,
  and contain zero failures.
- Artifact SHA-256: `e4cf80eaf20a405d40c97f6f0942f3511721dacea1eda4b8d676b66f95b95658`
  and `413816ff50bca09142b78ddc67ebb87638809105325c4fdd8e40cecf4bf7cf97`.
- GitHub star/follow status for both targets was HTTP 404 before and after; no external write,
  commit, push, or merge occurred. Evidence is retained at
  `/private/tmp/gitseed-32b-acceptance.xqKWGv`.
