# ADR-0009: one ranking source — deterministic score orders, the model grade annotates

- Status: Accepted (2026-07-28)
- Scope: `gitseed/cli.py::_radar_records()`, `gitseed/pipeline/run.py::ranked()`, `gitseed/cli.py::main()`

## Context

The 2026-07-28 deep review (GS-P0-002) found that a single run currently produces two
independent orderings from two different scores:

- The radar table, `--json` output, and `explain` all sort by the deterministic metadata
  score (`gitseed/scoring.py`, weighted from `commit_cadence_30d`, `contributor_count`,
  `has_license`, M0-licensed).
- The interactive and `--approve-all` approval queue sorts by `pipeline.run.ranked()`, which
  orders on `Reviewed.score` — `grade.idea + grade.skill`, the local model's own numbers.

Nothing enforces agreement between them. A fixture can be constructed where the deterministic
order is `A > B` and the model order is `B > A`; today's code shows the user `A` first and
then asks them to approve in an order that may start with `B`. The item a person reviewed
first on screen is not necessarily the item their first approval decision was actually about.

This is not a display bug to patch in the renderer. It is two independent facts about a
repository — "is this active and healthy by the measured, M0-licensed signals" versus "what
did the local model say about its idea and skill" — being used interchangeably as if they
were one ranking, when the project has already decided, in ADR-0007 and `docs/M0-VERDICT.md`,
that only the first one is measured and licensed to drive product decisions. The model's
`idea`/`skill` numbers have never been backtested against anything; PRD-F3 requirement 4
explicitly forbids prose fields from contributing to scores, but says nothing about `idea`/
`skill` themselves, and `ranked()` promotes exactly those two numbers to the one place in the
system with the highest stakes: the order external GitHub actions get proposed in.

## Decision

**The deterministic score is the only ranking key**, everywhere a run's candidates are
ordered: the radar table, `--json`, `explain`, the interactive approval prompt, `--approve-all`,
and any future export/share output. `pipeline.run.ranked()` — the `Reviewed.score =
grade.idea + grade.skill` ordering — is removed from every path that decides what a person
sees first or is asked to approve first.

The model's `idea`, `skill`, and `description` fields do not disappear. They remain visible,
per-item, as **annotations**: shown alongside a candidate wherever detail is already shown
(the radar row, `explain`, the approval prompt), but never consulted to decide the order
those candidates appear in. A candidate a reviewer would have ranked highly on `idea+skill`
still gets exactly as much visibility as its deterministic score earns it — it is not hidden,
only not used to jump the queue.

Concretely: `_radar_records()`'s sort key becomes the sole sort key referenced by every
consumer (radar, JSON, explain, approval, bulk approval). This issue tracks introducing one
`ReviewItem`-shaped construction site and one `rank_review_items()` function so the ordering
is computed exactly once per run and passed down unmodified — the review's own proposed
shape — rather than merely calling `_radar_records()`'s sort key from more places, which
would leave the same drift risk the moment someone edits one call site and not the other. Both
achieve "one order," but only the single-construction-site shape prevents the same drift from
recurring by construction; that is the standard the rest of this ADR assumes.

## Alternative considered and rejected

**Keep the LLM `idea+skill` ranking for the approval queue, and reconcile radar to match it.**
Rejected. The deterministic score is what M0 measured (`docs/M0-VERDICT.md`, ROC AUC 0.7432 on
the preregistered verdict) and what ADR-0007 licensed into the product; `idea`/`skill` have
no equivalent backtest and PRD-F3's own smoke-gate discipline treats the model as something to
verify before trusting, not something to trust as an ordering key by default. Making the
approval queue the anchor and radar the follower would promote the unvalidated number to the
higher-stakes position (it decides what gets proposed for external write) and demote the
validated one to a passive display — the opposite of what the evidence supports. It would also
mean *any* future measurement that improves the deterministic score's validity (a new
M0-style backtest) would need to be threaded through two ranking implementations again,
recreating the drift this ADR exists to close.

## Consequences

- No user-facing code path may sort review items by `grade.idea`, `grade.skill`, or any
  derivative of them. A reviewer who finds one has found a regression of this ADR, not a
  style preference.
- `Reviewed.score` and `pipeline.run.ranked()` are removed or, if retained for an unrelated
  internal purpose, are provably unreachable from any ranking decision a user sees.
- The model's `idea`/`skill`/`description` remain first-class, visible fields — this ADR
  changes what orders candidates, not what information is shown about them.
- Any future signal proposed to replace or augment the deterministic score as a ranking key
  (e.g. the Gate D momentum/undervaluation work gated by ADR-0011) must clear the same bar
  the current score cleared: a backtest of the same shape as M0, not intuition about what a
  local model's grade "should" mean.

## Falsification

This decision was wrong if a single `ReviewItem`/`rank_review_items()` construction cannot be
consumed, unmodified, by all of radar, `--json`, `explain`, interactive approval, and
`--approve-all` without a breaking change to any of them, or if some consumer genuinely needs
a different order than the others for a legitimate reason this ADR did not anticipate. A
future reader can check this from the diff that implements GS-P0-002 and from whether any
consumer still imports `pipeline.run.ranked()`.
