# ADR-0011: Gate D is gated on a measurement, not on being built

- Status: Accepted (2026-07-28)
- Scope: `docs/tickets/`, `docs/prd/`, milestone `v0.5 · Gate D — discovery, if measured`, issues GS-P1-017 and GS-P2-001 through GS-P2-007

## Context

The 2026-07-28 deep review's final sections (§14, §19, §21, Appendix B) lay out a "10,000
Stars" roadmap: `Category -> History -> Momentum -> Undervalued -> Seeded at -> Share`, and
frame the current deterministic score's limits — no popularity denominator, no star velocity
or acceleration, no category-relative percentile — as gaps to close by building momentum,
undervaluation, and share-loop features. Read on its own, that roadmap treats the absence of
those features as the reason gitseed is not yet a discovery product, and treats building them
as the fix.

That framing repeats a decision this project has already made once, in the opposite
direction. ADR-0007 records why scoring was built *after* M0, not before:

> The v0.2 sequence puts the Core Seam (#8) before scoring (#12). That would make the seam's
> scoring boundary speculative: before M0, PRD §14 proposed roughly forty components and
> several composite scores, but M0 found material signal in only three inputs.

And `docs/M0-VERDICT.md` states the boundary of what that measurement licenses, explicitly and
in its own dedicated section ("What this verdict licenses"):

> The positive-class base rate is 56/118 = 47.5%... the classifier separates small
> repositories from medium repositories, not unknown repositories from breakout repositories.
> The stronger claim that "gitseed finds repositories before they take off" would require a
> different label... The three contributing features license v0.2 to build
> `commit_cadence_30d`, `contributor_count`, and `has_license`... They do not license a
> discovery claim in the README.

M0 already ran the experiment the Gate D roadmap would otherwise assume the answer to. It
found that the one score gitseed currently computes does not predict breakout — it predicts
repository size. Nothing in the current codebase has measured whether *any* available
GitHub-API signal (star velocity, contributor growth, PR merge velocity, release cadence, or
anything else on the review's list) predicts breakout either. Building momentum,
undervaluation-residual, or share-card machinery on top of unmeasured signals — no matter how
well-engineered — would be the exact mistake ADR-0007 and M0 exist to prevent, repeated one
layer up: substituting engineering completeness for a measured claim, and this time on the
component that is closest to the product's actual public promise ("find great repos before
they trend"), where an unlicensed claim causes more damage than an unlicensed *internal*
scoring boundary does.

This is the reviewer's own stated blind spot in this area: the review is right that the
current score cannot support a discovery claim (GS-P2-001 restates its own §8.2/§14 finding),
but its roadmap toward fixing that treats *building* the missing machinery as the next step,
not *measuring first whether the machinery would work*. The project's own prior decision
(ADR-0007) and its own prior measurement (M0) already establish which order this project
uses, and Gate D's roadmap should not be exempt from it just because it is farther from the
security-critical parts of the codebase.

## Decision

**No Gate D item may be implemented as product-facing scoring, ranking, or claimed-value
machinery until a backtest of the same shape as M0 has measured that the specific signal it
depends on predicts breakout** — not repository size, not current popularity, not activity
level, but growth from an unknown or low-attention state to a materially more prominent one.

"Same shape as M0" means, at minimum: a preregistered hypothesis and verdict threshold before
looking at results (M0-VERDICT.md's own discipline); a labeled sample with a breakout
definition that is not simply "stars above a low absolute count" (M0-VERDICT.md names this
exact trap: "a much higher star threshold, or growth since a past date rather than an
absolute star count today"); an honest accounting of collection completeness, the same way
M0 records that its own sample was collected without a token and states the resulting
limitation rather than hiding it; and a stated, checkable primary metric with a numeric pass
threshold, not a qualitative impression.

This applies specifically to:

- **GS-P2-001** (score has no popularity denominator, doesn't measure momentum/undervaluation)
  — the backtest question here is not "can we build a formula that combines activity and
  popularity," it is "does any such formula, measured against real outcomes, separate future
  breakouts from non-breakouts better than chance." Building the formula first and measuring
  after inverts the order this ADR requires.
- **GS-P2-002** (raw counts collapsed to booleans) — an exception, addressed below.
- **GS-P2-003** (no observation history / `first_seen`) — an exception, addressed below.
- **GS-P2-004** (no share card / digest / watchlist) — gated because a share card that
  advertises a "found it early" claim is itself a product-facing claim about the signal that
  selected the repository; it must not ship before that signal is validated, or the card
  becomes the same unlicensed claim in image form.
- **GS-P2-005** (search ordering not tuned for early discovery) — gated because "tuned for
  early discovery" presupposes a definition of what to bias toward, which is the same
  unmeasured signal question.
- **GS-P2-006** (no console-script entry point) and **GS-P2-007** (README hero copy) —
  these do not themselves make a predictive claim, but §2007's own text already says new hero
  copy must not ship "aspirational language ahead of the evidence." GS-P2-006 is largely
  independent of this ADR's substance (it's packaging, not a claim) and may proceed on its own
  timeline; it is listed under Gate D for milestone grouping, not because it carries the same
  measurement dependency.

**Two items are infrastructure, not claims, and are explicitly not blocked by this ADR:**

- **GS-P1-017** (wire the existing SQLite store into the CLI) — persisting observations is a
  prerequisite for ever running a Gate D backtest at all; refusing to build storage until a
  backtest exists would make the backtest impossible to run. This is the same relationship
  ADR-0007 describes between the Core Seam and scoring: infrastructure that a measurement
  needs may be built before the measurement, as long as it does not itself assert the
  measurement's conclusion.
- **GS-P2-003**'s narrow infrastructure half — recording `first_seen` and periodic raw
  observations — is likewise not a predictive claim by itself and may proceed alongside
  GS-P1-017. What remains gated is any feature that *uses* that history to assert momentum,
  undervaluation, or a "seeded before breakout" claim about a specific repository before the
  backtest exists.

## Alternative considered and rejected

**Build the Gate D roadmap as designed, and only gate the final README/marketing claim on
measurement.** This is close to the review's own framing (§13.2: "현재 제품이 실제로 이를
수행하지 않으므로 문구부터 바꾸면 안 된다" — do not change the copy first, because the
product doesn't yet do this). Rejected as insufficient: gating only the *words* while building
the *machinery* unmeasured still produces a shipped momentum/undervaluation score, a shipped
share card, and a shipped "recently surfaced" search bias — each of which embeds an implicit
claim in its existence and default behavior even if the README stays silent. A share card
users generate and post makes the claim on gitseed's behalf regardless of what the README
says. Gating the entire feature, not only its copy, is the standard ADR-0007 already set for
the deterministic score itself, and this ADR applies it consistently rather than making an
exception for the product surface where an unlicensed claim would be most visible.

## Consequences

- Every issue tracking Gate D product/scoring work (GS-P2-001, GS-P2-002 in its
  scoring-consuming half, GS-P2-004, GS-P2-005, and any future momentum/undervaluation work)
  must ship its own backtest evidence, or cite an already-merged one covering the same claim,
  before or alongside the feature — not as a follow-up.
- `docs/M0-VERDICT.md` is the template such a backtest should follow: preregistered threshold,
  stated sample completeness, stated limits, explicit statement of what the result does and
  does not license.
- A future backtest that *does* clear its preregistered threshold licenses exactly what it
  measured — the same discipline ADR-0007 applied to the current score ("The other PRD §14
  components remain rejected until new measurement licenses them"), applied here to any Gate D
  signal.
- Gate C (category) and Gate A/B (correctness and write-safety) are unaffected; this ADR
  scopes only to Gate D.

## Revisit condition

Revisit this ADR only when a specific backtest, run against a real GitHub sample with a stated
breakout label and preregistered threshold, is proposed to supersede it for a *specific* Gate D
signal — at which point that signal's gate lifts on its own evidence, the same way ADR-0007
lifted the gate on `commit_cadence_30d`, `contributor_count`, and `has_license` specifically,
and no others. A general argument that "the roadmap is well-designed" or "competitors ship
this" does not meet this bar; only a measured result does.
