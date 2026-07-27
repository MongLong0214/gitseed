# Phase 0 — Seed intake (owner answers, 2026-07-26)

> This file is the output of the owner gate. Because the work spans 10 days, keep it on
> disk rather than in session memory. **Changing these answers requires asking the owner again.**

## Seed

`https://github.com/yumiaura/followme` — Python CLI, 993 lines / 12 files, 60 stars.
Collect repositories through GitHub Search → grade with local Ollama (idea, skill, description + malware
verdict) → follow the author and star the repository above a score threshold. Single SQLite file, no server.

## 4 required questions — answers

| Question | Answer |
|---|---|
| **Project type** | **Enterprise rebuild of followme** — properly rebuild the same problem (local-LLM repository curation) |
| **Revenue model** | **Free forever** — MIT, no paid tier, server, or telemetry (same direction as CommitLore) |
| **Visibility** | **Public from the beginning** — public repository from genesis, with the entire history as evidence |
| **Originality** | 3 items below (owner multiple choice) |

## Originality — what must differ from the seed

1. **Fully separate name and branding** — a new name unrelated to followme. Decide after
   measuring availability on PyPI/GitHub (follow the ADR-0009 procedure).
2. **Make grading deterministic and reproducible** — the seed's greatest weakness. The same repository
   must receive the same score. If impossible, *measure* nondeterminism and report it as a confidence interval.
   The same principle as CommitLore's "numbers or silence."
3. **Do not leave malware classification to an LLM alone** — false-positive and false-negative costs
   are asymmetric, and the seed has no evidence. Combine with deterministic signals (dependencies, network calls, obfuscation).

## Unselected item — record

**"Human gate for automatic actions (follow and star)" was not selected.**

Not choosing it in the multiple choice means "it is not a required differentiator," so retain
the seed's automatic actions.

> ⚠️ **Correction**: I wrote here that I would "provide `--dry-run` as a flag," as if it were an improvement.
> **The seed already has it** — the `DRY_RUN` environment variable in `libs/settings.py:74`,
> the `--dry-run` argument in `scripts/subscribe.py`, and the `db.unfollowed_above`/`unstarred_above`
> queries. I invented an improvement before reading the clone, violating invariant 1. This was the second time.
> Do not delete it; it is decision history.

The actual remaining problem differs: there is no **audit log** for external actions. What was
followed/starred, when, and why remains only as DB flags, while the decision evidence disappears.
Reversal queries exist, but there is no reversal *command*.

## Measured weaknesses of the seed (Phase 1 input)

Confirmed directly after clone:

- **0 tests, 0 CI** — confirmed after clone. Final.
- **SQLite migration**: `add_missing_columns` provides **idempotent column additions**. Its comment
  is accurate (`CREATE TABLE IF NOT EXISTS` does not change an existing table). What is absent is
  `user_version` tracking, ordered migrations, type changes, and a data-backfill path.
  > ⚠️ The first version of this item said "no migrations" and was **wrong.** It was an assumption
  > written before cloning. That violated invariant 1; this corrects it. Do not delete it; it is decision history.
- **Nondeterministic Ollama classification**: `temperature: 0.1`, `format: json`, **no `seed`
  parameter** → nondeterministic. But **the magnitude of variance is unmeasured**. "The same repository
  gets different scores" remains a claim, not an observation. Measure it in Phase 1-D.
- Malware classification depends on one LLM — a single call in `libs/ollama.py` produces the malware
  verdict. No deterministic cross-signal.
- 12 files, 993 lines

---

## Owner-gate exemption (2026-07-26, explicit owner directive)

> "Do not wait for owner confirmation. Make a deep judgment on the best approach and proceed autonomously. Ask me nothing for 10 days."

Factory invariant 5 says 3 owner gates (Phase 0 questions, dossier confirmation, public transition)
cannot be skipped. **The owner exempted the rule itself.** Invariant 5 exists to protect the owner's
decision rights, so when the owner decides to delegate, that decision takes precedence.

Application:
- Phase 2 dossier — write it, then proceed to Phase 3 without waiting for confirmation
- Phase 6 public transition — the Phase 0 answer already says "public from the beginning," so execute it
- Every other decision — I decide and leave the rationale in ADRs and records

**Not exempted — retained by my judgment:**

Do not perform actual GitHub **follow or star actions.** They are hard-to-reverse actions against
3rd-party accounts and are unnecessary to build and verify the tool. Verify the path with `--dry-run`,
and leave actual execution as a decision for the owner on their own account. This is not asking for
permission; it is **not taking unnecessary external action**.

Retain the same boundary for all irreversible or externally impactful actions (creating issues or PRs
in another person's repository, changing account settings). Autonomous delegation delegates judgment,
not authority to affect other people.
