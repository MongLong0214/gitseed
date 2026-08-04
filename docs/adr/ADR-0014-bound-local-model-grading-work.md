# ADR-0014: Bound local-model grading work independently from deterministic screening

- Status: Accepted (2026-08-04)
- Scope: `gitseed/cli.py`, `gitseed/pipeline/run.py`, local Ollama grading

## Context

A live dry run with `qwen2.5-coder:32b`, three GitHub candidates, and a 180-second
per-request deadline completed collection and the model smoke gate but timed out both
candidate grades. The two failed candidates supplied 42,036 and 386,939 source bytes.
The installed model has a 32,768-token context window. The current adapter sends every
selected source byte and places no upper bound on generated tokens:

```json
{"format":"json","stream":false,"options":{"temperature":0}}
```

The existing 120-second default was derived from a roughly 23-second cold one-token
measurement, not an end-to-end grade. Its test only checks that the integer reaches the
HTTP transport. It therefore proves configuration plumbing, not 32B completion.

Source collection currently serves two different purposes. Deterministic screening needs
all source and priority files allowed by its 500,000-byte policy budget. Model grading needs
a bounded, representative prompt. Treating those as one budget makes model work scale with
the scanner's worst case and allows the serving layer to truncate an oversized prompt
without GitSeed stating what the model actually saw.

## Decision

**Keep deterministic screening unchanged, but construct a separate, deterministic and
explicitly bounded evidence digest for model grading. Bound model output as well as input,
and give the supported 32B model a measured request deadline.**

The complete user prompt sent to Ollama is capped at **24,000 UTF-8 bytes**. The grading
digest inside that prompt will:

1. retain the repository name and file paths;
2. distribute a fixed UTF-8 byte budget across selected files rather than allowing one
   large file or lockfile to consume the whole prompt;
3. mark every shortened or omitted file inside the prompt and include the total selected,
   included, and omitted byte counts;
4. remain deterministic for identical candidate files; and
5. leave the full selected file set available to deterministic screening.

The Ollama request will set `num_predict=128`, sufficient for the small JSON contract, and
keep `temperature=0`, JSON format, and non-streaming behavior. The grade will use prompt
version `cli-v2-bounded` so stored results cannot be confused with unbounded `cli-v1`
results. An explicit `--grade-timeout` remains authoritative; the default is **240 seconds**.

Timeout, malformed JSON, and a truncated model answer remain candidate failures and keep
the run incomplete. There is no retry, smaller-model fallback, or silent claim that model
grading covered the full screened source set.

## Alternatives considered and rejected

**Only raise `--grade-timeout`.** Rejected: work remains unbounded, the 386,939-byte prompt
can exceed model context, and serving-layer truncation remains undisclosed.

**Automatically retry with a smaller model.** Rejected: it changes the grading basis within
one run and hides a 32B failure behind a different model.

**Lower the existing source collection caps.** Rejected: those caps protect and bound the
deterministic security scan. Reducing them to help the model would weaken screening coverage.

**Send only the first fixed prefix of the concatenated digest.** Rejected: manifests,
workflows, or a single large early file can crowd all ordinary source out of the model's
evidence.

**Trust Ollama to truncate to its context window.** Rejected: the truncation boundary is not
represented in GitSeed's prompt version or evidence, and it may remove either instructions
or representative source depending on serving behavior.

## Consequences

- 32B grading work has a finite input and output bound per candidate.
- The model grade is explicitly a judgment over bounded evidence, not a full-code audit.
- Deterministic screening coverage and fail-closed behavior do not change.
- Existing artifacts remain renderable; new grades carry a different prompt version.
- The chosen input budget, output-token limit, and default timeout require RED/GREEN tests
  plus a real GitHub dry run using `qwen2.5-coder:32b` before the implementation ticket is
  complete.

## Decision evidence

On the supported local machine, after clearing an abandoned Ollama request queue:

- a 12,288-byte real-source prompt completed in 60.793 seconds, including 3,686 prompt
  tokens, 50.800 seconds of prompt evaluation, and 71 generated tokens in 7.136 seconds;
- a 24,000-byte real-source prompt completed in 97.847 seconds, including 5,960 prompt
  tokens, 86.110 seconds of prompt evaluation, and 101 generated tokens in 10.549 seconds;
- both used `qwen2.5-coder:32b`, JSON format, `temperature=0`, and `num_predict=128`; and
- the 24,000-byte prompt remained well inside the model's 32,768-token context window and
  completed with more than a two-times margin under the selected 240-second deadline.

This evidence chooses the constants. It does not replace the required end-to-end live
acceptance run over the previously failing candidates.

## Falsification

The decision fails if the bounded implementation still times out on either previously
failing live candidate with the default deadline, if identical inputs produce different
digests, if the prompt omits evidence without declaring it, if deterministic screening sees
fewer files, or if the model's JSON contract can exceed the output bound without the run
becoming incomplete.
