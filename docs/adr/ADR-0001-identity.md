# ADR-0001: identity — the name `gradelore`, and what to discard from the seed

- Status: Accepted (2026-07-26)
- Seed: `yumiaura/followme` (Python CLI, 993 lines, 60 stars)

## Context

Phase 3-A requires fixing the name before writing code. Deferring it gets exponentially more expensive
(demonstrated by CommitLore's comprehensive `Annals`→`CommitLore` rename).

## Decision — name

**`gradelore`.** Measured as available on both PyPI and GitHub (both 404).

Reasons for selection:
- Directly states what it does (`grade`) — search discoverability
- Uses the same naming pattern as the owner's existing project `CommitLore` (domain term + `lore`),
  forming a product family. Both consistently center on "verifiable records"
- Easy to pronounce with no bad connotation

## Ruled-out — name

- **`repoassay`** | `github.com/repoassay` is an **Organization created on 2026-07-24** with the
  bio "Open-source tools for evidence-first repository analysis and test
  strategy" — an exact overlap with our domain. The same type of active conflict
  that led ADR-0008 (CommitLore) to reject `menhir`
- **`tailings`** | means **mining waste**. Implies the selected items are garbage
- **`repocull`** | `cull` ambiguously means both selection and slaughter, and we choose what to
  keep, not what to discard
- **`siftwork`** | the metaphor fits, but without `repo` or `grade`, it is hard to find through search
- **`repograder`** | accurate but too generic to support a brand
- **`winnow` / `sluice` / `gradebot` / `codeassay`** | taken on PyPI (each measured 200)
- **`repolens` / `repolore` / `orelight`** | GitHub org taken (empty accounts). Not an active conflict,
  but not clean

## Decision — what to discard from the seed

**Remove unattended automatic follows and stars from the default.**

GitHub Acceptable Use Policies explicitly prohibit this — *"rank abuse, such as
automated starring or following"*. The provision has **no quantity threshold.** 10 per day is still
automated starring. And because StarScout from ICSE 2026 (CMU) detects **account behavior
patterns**, not total volume, low-volume automation makes the pattern clearer.

Replacement: **an evidence-backed review queue.** Discovery, grading, and security screening are fully
automatic; a person approves each final action. The person decides and the tool is the UI, so it is not automated starring.

## Ruled-out — behavior

- **Keep unattended automatic stars and follows** | violates GitHub AUP "rank abuse." Risks suspension
  of the owner's account, and a tool that violates ToS cannot be called enterprise-grade
- **Evade detection with a limit of N per day** | the provision has no quantity threshold, so the violation
  remains, and the limit is ineffective against pattern-based detection
- **Keep the seed's 4-stage chain (`fetch→evaluate→subscribe→star`)** | the last two stages disappear
  for the reasons above, changing the shape of the pipeline itself

## Consequences

- The project becomes **read-only analysis + human-executed curation**. 0 policy risk
- Do not share code with the seed. Inherit only the idea (grading repositories with a local LLM)
- Provide `--approve-all` batch approval (a person reviews the list and approves once). But do not
  recommend unattended cron operation in documentation or make it the default
