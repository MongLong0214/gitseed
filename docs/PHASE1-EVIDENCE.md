> The historic Phase 0 unattended-action assumption discussed below is superseded by [ADR-0006](adr/ADR-0006-no-unattended-external-writes.md).
# Phase 1 — Evidence collection (reproduction experiments first)

Invariant 1: no claims without evidence. Reproduce defects and take numbers only from run logs.
Everything below is the result of execution on this machine.

Environment: Apple M4 Pro / 48GB unified memory / 16 GPU cores / ollama 0.17.4

---

## D-1. Static facts about the seed (confirmed directly after clone)

| Item | Measured | Notes |
|---|---|---|
| Size | 12 files / 993 lines of Python | |
| Tests | **0** | `find` result 0 |
| CI | **0** | No `.github/workflows` |
| License | **None** | `gh repo view` licenseInfo null |
| Stars | 60 | As of 2026-07-26 |
| Rate-limit handling | **0 cases** | `rate·429·403·X-RateLimit·retry·backoff` all absent |
| Migrations | Idempotent column additions **exist** | No `user_version` tracking, ordering, type changes, or backfill |
| dry-run | **Exists** | `DRY_RUN` env + `--dry-run` argument |
| Reversal | Queries only | `unfollowed_above`/`unstarred_above`. **No commands** for unfollow/unstar |

> I wrote "no migrations" and "no dry-run" in this table as **assumptions before cloning,
> and both were wrong**. 2 violations of invariant 1. Do not delete them; they are decision history.

## D-2. Grading determinism — measured

Repeatedly call the seed's `libs/ollama.evaluate` with the **same digest**. `temperature: 0.1`,
`format: json`, no `seed` parameter.

**Seed default qwen2.5-coder:7b, n=10:**

```
idea   distinct=[5.0]  range=0.0  sd=0.000
skill  distinct=[9.0]  range=0.0  sd=0.000
description   3 of 10 runs differ
```

**1.5b, n=22 (reference):** likewise, `idea·skill` sd=0.000; only description varies.

**Conclusion: scores were completely stable in both models.** My initial claim that "the same
repository receives different scores" **did not reproduce.** `temperature: 0.1` + `format: json`
effectively fixes the numeric fields. Only the free-text `description` field varies.

This changes the rebuild direction: **determinism was not a seed problem.** Narrow originality item 2
from "make grading deterministic" to **"separate the varying prose field from the grading path"**
— the numbers are already stable; the problem is a structure that mixes prose into grading (see D-3).

## D-3. Security classification — **withdrawn.** It was a model-size problem, not a seed defect

**Seed default `qwen2.5-coder:7b`, same digest, n=14:**

```
security_flag=true                0/14
description starts with ⚠           0/14
mismatch                            0/14
```

**The classification field (`security_flag`) is perfect with 7b.** The 64% from 1.5b below cannot
be used to judge the seed, and it will not be. Publishing it unchanged would have attributed a
nonexistent defect to the seed's author.

**But prose-field leakage rarely reproduces even with 7b.** In the D-2 determinism measurement
(7b, n=10), `description` began with `⚠ SECURITY: The code does not contain any malicious...`
1 time — it added a security prefix while saying the code was clean. Because `security_flag` itself
was not contaminated, this **does not affect grading decisions**, but the user-visible explanation
misleads. This aligns exactly with D-2's new conclusion ("separate prose fields from the grading path"):
the numbers are stable; the problem is free text leaking into the display layer.

### After the withdrawal, something more important remains

| Model | Size | False positives on clean code |
|---|---|---|
| qwen2.5-coder:1.5b | 1.0 GB | **9/14 (64%)** + swapped fields |
| qwen2.5-coder:7b (seed default) | 4.7 GB | **0/14** |
| qwen2.5-coder:32b | 19.9 GB | Measurement planned |

The seed checks **only whether the model is installed** (`libs/ollama.py:47` — error if absent).
**It does not check whether the model can follow the output contract.**

Using `OLLAMA_MODEL=qwen2.5-coder:1.5b` on a small machine is a reasonable choice.
It gives the user a tool that, **of 3 equal parts of clean repositories, classifies 2 as malicious,**
without warning. Worse, the classification arrives with `security_reason` saying "the code is simple
and well structured."

**This is the rebuild's actual differentiator** — the rationale for originality item 3
("do not leave malware classification to an LLM alone") changes from "LLMs cannot be trusted" to
**"the model is trusted without verifying whether it can follow the contract."**
That is a far more concrete and fixable problem.

Response direction (finalized in the ADR): a **contract-compliance smoke test** at startup — inspect
the model with a known clean sample and a known malicious sample; if it fails, refuse grading or lower
its grade. Add deterministic signals (dependencies, network calls, obfuscation) on top.

---

### Reference: original 1.5b measurement (evidence data for the withdrawn judgment)

With an **intentionally clean digest** (6-line JSON logger, no network, dependencies, or obfuscation), n=14:

```
security_flag=true                9/14  (64%)
description starts with ⚠           5/14
mismatch (flag=false yet warning-prefixed)  5/14
```

`flag` and `description` are **perfectly inversely correlated** — in the 9 flag=true runs, desc does
not warn; in the 5 flag=false runs, desc warns. And the `security_reason` field contains
`"⚠ SECURITY: The code is a simple, well-structured..."`. In other words, **the reason field
contains an explanation, the reason says "fine," but the flag says "malicious."**

The prompt (`libs/ollama.py:33`) instructs description to begin with `⚠ SECURITY: ` when the flag is
true. The model applies that instruction across field boundaries.

⚠️ **Because this number comes from 1.5b, it cannot be used to judge the seed.** Cite it only after
remeasuring with 7b (default) and 32b (the maximum on this machine).

## D-4. Model-selection rationale (maximum on this machine)

| Model | Size | Decision |
|---|---|---|
| qwen2.5-coder:1.5b | 1.0 GB | Preliminary measurement |
| qwen2.5-coder:7b | 4.7 GB | **Seed default** — fair comparison baseline |
| qwen2.5-coder:32b | 19.9 GB | **Maximum on this machine** — comfortably fits the default GPU allocation of ~36GB from 48GB |
| 70b Q4 | ~40 GB | Exceeds default GPU allocation → thrashing. Excluded |

Reason for using one family: changing only size isolates "is the model too small, or is the design bad?"

## Defect in the tool itself (incidental discovery)

`ollama pull` **failed on a registry 503 but returned exit 0**.

```
Error: pull model manifest: 503: upstream connect error ...
$ echo $?  → 0
```

Trusting the exit code and proceeding led to a `model not found` 404. Verify every subsequent pull
with `ollama list`. **An exit code is not evidence of success** — the third instance of this lesson
in this session.

---

## C. Falsification (performed directly, no delegation) — **a discovery that changes project direction**

### C-1. Direct competitors

Search found no exact peer tool that "discovers and grades other people's GitHub repositories with
a local LLM and performs automatic social actions." Results were in adjacent areas (LLM benchmarks,
model recommendations, eval-training repositories) and pointed elsewhere. → The discovery+grading niche is real.

### C-2. ⚠️ Automatic follows and stars violate GitHub policy

**GitHub Acceptable Use Policies explicitly prohibit this:**
> "rank abuse, such as automated starring or following"
> "inauthentic interactions ... automated inauthentic activity"

Evidence:
- GitHub Docs, Acceptable Use Policies (site-policy)
- ICSE 2026 (CMU): 18,617 repositories, 600 myriad fake stars, GitHub reactively detecting and deleting them

**The seed's core workflow directly violates this provision.** The `subscribe` (automatic follow)
and `star` (automatic star) chained by `main.py` are exactly "automated starring or following."
They run without human confirmation when a score exceeds the threshold.

### C-3. What this falsification does to the project

**The rebuild must not copy the seed's automatic social actions unchanged.** Making a
ToS-violating tool enterprise-grade is a contradiction.

Revised direction (finalized in the ADR):
- **Keep**: discovery + local-LLM grading + deterministic security screening — this part is valuable
  and has no policy problem. It is read-only.
- **Change**: replace automatic follows and stars with a **curation artifact** (ranked list with evidence).
  The user reviews that list and decides *by hand*. Remove automatic social actions from the default.
- **If retained optionally**: require a machine account + explicit human approval gate, cite
  GitHub AUP in the README, and always default to dry-run. But this is a gray area, so
  excluding it from the default distribution is safer.

### C-4. Reverse my Phase 0 judgment — with evidence

In Phase 0, I judged that because "human gate for automatic actions" was absent from the owner's
multiple choice, automatic actions should remain. **C-2 destroys the premise of that judgment.**
The owner did not omit the option to mean "keep it even if it violates ToS"; the owner did not know
that fact (and neither did I at clone time).

This follows the CommitLore principle exactly — **new evidence is a legitimate reason to reverse
a decision.** Change direction to remove automatic social actions from the default, and record the
decision in the ADR and commit trailer as
`Ruled-out: automatic follow and star by default | violates GitHub AUP "rank abuse"`.

The owner directed "10 days autonomous" + "human gate for automatic actions not selected," but that
directive predates knowledge of this policy fact. Autonomous delegation does not include **authority
to make a decision that harms the owner on their behalf** — distributing a ToS-violating tool under
the owner's name risks the owner's account, so use the safe default (remove automatic actions) and
retain the rationale for this judgment.

---

## D-5. The root cause is not model size, but **a prompt that instructs the model to cross field boundaries**

D-3 concluded that "the seed does not check the model's contract-compliance ability." That was only
half right. The confounders were separated — clean digest, n=12:

| Model | Prompt | security_flag false positive | description starts with ⚠ |
|---|---|---|---|
| 1.5b | Seed original | **9/12** | **11/12** |
| 1.5b | Strict | **0/12** | **0/12** |
| 7b | Seed original | 0/12 | 0/12 |
| 7b | Strict | 0/12 | 0/12 |

**Failure requires both a small model and the seed prompt.** With the strict prompt,
even 1.5b produces no false positives.

### Narrowed down to the exact language causing it

The relevant instruction in the seed original (`libs/ollama.py`):

> `When flagged, set idea and skill to 1.0 and begin description with '⚠ SECURITY: '`

If the same instruction is **removed from quotation marks and incorporated into prose**, 1.5b false
positives change from 4~6/10 → **0/10**.

```
prompt with a quoted literal   x  any digest   4~6/10
prompt paraphrased, no quotes  x  any digest   0/10
```

**A quoted literal reads as "output this" to a small model.** The trigger is not the instruction
itself, but the literal notation.

### Design rules this gives gitseed

1. **Do not put an output marker in the prompt as a literal.** The moment a format instruction crosses
   into a data field, a small model copies it as content
2. Keep fields orthogonal — `security_flag` owns the security decision, while
   `description` only explains
3. Even so, **the smoke gate remains necessary.** We do not control which prompt/model combination
   a user runs, so inspect the combination actually in use

### Measured smoke gate (using the seed prompt verbatim)

| Model | Prompt | Gate | Reason |
|---|---|---|---|
| 1.5b | Seed original | **FAIL** | 5/5 false positives + description field leakage |
| 1.5b | Strict | **FAIL** | skill [7, 9] across 5 runs with the same input — score variance |
| 7b | Seed original | PASS | |
| 7b | Strict | PASS | |

1.5b is rejected **for different reasons under the two prompts**. I expected "1.5b will pass with
the strict prompt," which was **an assumption without measurement.** The gate corrected my expectation.

> ⚠️ The first attempt at this verification **paraphrased** the seed prompt and passed all four combinations.
> The paraphrase removed the quoted literal. It does not reproduce without the original wording.
