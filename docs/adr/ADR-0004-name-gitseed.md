# ADR-0004: name — `gitseed` (owner decision)

- Status: Accepted (2026-07-27)
- Supersedes: ADR-0001's **name** clause (`gradelore`). ADR-0001's behavior decision (remove automated
  social actions) and rejection list **remain valid.**

## Context

ADR-0001 chose `gradelore`. Two of its three reasons were wrong.

**"It forms a product family by following the same pattern as the owner's `CommitLore`"** — an argument
from branding convenience, not product truth. The referent of `lore` in `CommitLore` is clear
(accumulated decision knowledge attached to commits = the product itself). In `gradelore`, `lore` refers to a score,
but **8/10 is a number, not knowledge passed down.** It borrowed the form without transferring the metaphor.

**"It directly states what it does (grade)"** — only half true after the pivot. The pipeline is
collection → deterministic security screen → **contract-verified** LLM grading → human-operated review queue.
Grading is one middle stage and even the stage **trusted least and therefore placed behind a smoke test**.

I proposed `repotriage` as an alternative (triage = screening before acceptance + prioritization + subsequent
human treatment), but **the owner decided on `gitseed`.** The name is the owner's decision.

## Decision

**`gitseed`.**

## Measured availability (following the ADR-0009/CommitLore procedure)

| Namespace | Result | Decision |
|---|---|---|
| **PyPI `gitseed`** | **404 — available** | The registry we will distribute through. No problem |
| PyPI `git-seed` | 404 — available | Reserve |
| GitHub org `gitseed` | **Taken** — Organization, created 2015, 3 repos | Below |
| npm `gitseed` | **Taken** — v0.0.0, abandoned stub since 2022 | Not our registry |

GitHub org contents: `gitseed` (Rust CI system, ★1, pushed 2025-09), `gitseed_old` (GCP
bastion bootstrap shell script, 2018), `seedpq` (★2, 2026-01).

**This differs from `CommitLore`'s rejection of `gitlore`.** That was an **active tool in the same domain**
in the registry we would distribute through (npm). Here the distribution registry (PyPI) is empty,
the conflicting item is a CI system in a different domain, and ★1~2 is not a brand likely to be confused.
The repository will live at `MongLong0214/gitseed`, so the org namespace is unnecessary.

## Known risk — record it

**`seed` easily reads as bootstrapping or scaffolding.** Two repositories in the org above actually
do that work (CI system, bastion bootstrap). This tool triages, so the connotation may mislead.
The README's first sentence must remove that misunderstanding immediately.

## Ruled-out

- **Keep `gradelore`** | Context above. At 1 commit, this is the cheapest point to replace it
- **`repotriage`** | my proposal, and the metaphor is more accurate, but the name is the owner's decision
- **`repotrust`** | promises exactly what we explicitly refuse to assert (trust)
- **`repograder`** | names the pipeline after its least-trusted stage
- **`touchstone`** | the most accurate metaphor, but taken on PyPI (200)

## Consequences

- Replace directory and document references with `gitseed`
- **Exclude old references in ADR-0001 and this document from mechanical replacement.** They are decision history;
  replacement would erase what changed and why (the same rule as CommitLore ADR-0008/0009)
- Do not edit the message of genesis commit `f2e8535`; it is history
