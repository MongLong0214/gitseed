# Handoff — gitseed

Written 2026-07-29 by the outgoing session. Read this before touching anything.

> Verify any number here before citing it. Nothing checks that this document
> stays current.

## What this project is

gitseed surfaces undervalued GitHub repositories and, with explicit human
approval, performs external actions (star, follow) — recording every decision as
a CommitLore trailer block in a local git repository. It is a discovery tool with
a safety contract, and the safety contract is the harder half.

**The approval gate is the product's central invariant.** It stands between a run
and a GitHub write the user never sanctioned. Two changes this week broke it
accidentally and both were sent back. See "The approval gate" below before
touching `cli.py`, `application.py` or anything on the run path.

## State

`dev` is the default branch; PRs required. Baseline as of this writing:

```
310 tests passing
1 open issue, deferred to backlog
```

Run `python3 -m pytest -q` and `gh issue list --state open` rather than trusting
those numbers.

The single remaining issue is #66, a share card, deferred to backlog while work
concentrates on CommitLore. Everything else is closed. The correctness and safety
work is done and each piece was verified by breaking it: the approval gate,
append-only storage, ref atomicity, honest reversibility grades, raw metadata
retention, category wiring, evidence-producer validation, and search ordering
recorded as a policy rather than a claim.

Two ADRs carry decisions a later maintainer will want to overturn without knowing
why they were made. ADR-0012 says this project must not emit an undervaluation,
momentum, growth or breakout score, because it has observed attention for
individual repositories and no baseline for what attention to expect. ADR-0013
says search ordering is a collection preference, not evidence about what it
surfaces. Both state their own falsification conditions; overturn them with
evidence.

## The approval gate

PR #87 established the property and #88, #92, #93 and #94 each defended it again:
**a store that is broken, missing or unwritable must never suppress the approval
prompt, change what it asks, or fail a run the user approved and that succeeded.**

Every PR touching the run path since has been checked this way, and it caught a
real coupling once. Do not skip it because the tests pass — the tests passed in
the case it caught.

The mechanism is ordering — the store write happens *after* approval and
execution. Verify it the way I did rather than trusting a test count:

```python
# in gitseed/storage.py, inside the observation write
raise RuntimeError("broken")
```

then `python3 -m pytest -q tests/test_review_cycle.py`. All 11 must still pass.
When #88 was first submitted this gave 5 failed, which is how the coupling was
caught. Do the same for `metadata()` in `adapters.py` — that one is verified too.

Related invariants that came with it:

- **Append-only means append-only.** A correction is a new row pointing at what it
  corrects, never a mutation. No CLI path updates or deletes a stored row.
- **`first_seen` is enforced in the store, not by the caller.** A caller that
  forgets is exactly what an invariant is for.
- **Observations are stored raw, not as derived growth.** "grew 4,751 stars" is a
  subtraction anyone can do later and goes stale the moment another observation
  lands.
- **No history is invented.** A repository surfaced before observation recording
  existed has its first observation dated to the first run that recorded one, not
  to the run that surfaced it.

## What the product may and may not claim

**ADR-0012 (PR #90) decided that gitseed must not emit an undervaluation,
momentum, growth or breakout score from the data available today.**

Undervaluation is `expected_attention - observed_attention`. Both terms are
needed and only one exists:

| | |
|---|---|
| computable today | the activity score; current observed stars; raw commit and contributor counts |
| after observations accumulate | star change and velocity for repositories observed twice — recording began in #88, no repository has two points yet |
| not from this data | what attention was *expected*, which needs a category population |

A single `(time, stars)` point cannot produce a velocity, and GitHub does not
expose the history retroactively — this is a wait, not a fetch. The current score
is an activity signal and is described as one. Shipping a growth score built from
activity data would make every recommendation wrong in the same direction.

The ADR states its own falsification condition. Overturn it with evidence, not
preference.

## Open issues

**#66 — share card, deferred to backlog.** The issue asks for a surface to share a
"gitseed found this early" claim, and ADR-0012 established this project cannot
make an early-discovery claim from today's data. The resolution recorded on the
issue: what a run can honestly share is what it observed and when — this
repository had this many stars at this timestamp, gitseed surfaced it, here is the
query. That becomes evidence of early discovery only later, when someone compares
it against what the repository became, so the card's job is to make that
comparison possible rather than to pre-empt it. Build it for the verifier: the
query (#67 records it), the timestamp, raw counts (#64 keeps them), engine
versions, run id. A card that cannot be checked is marketing.

If it feels weak without a discovery claim, that weakness is the honest state of
the product, and #65's accumulating observations are what will fix it — no
repository has two data points yet.

## Conventions

Branches `feat-issue-<n>` / `bug-issue-<n>` off `dev`, PR required, `--no-ff`.

Every commit carries a CommitLore record with a `Record-Id`. Run
`git log -3 --format=%B` to see the vocabulary before writing one; do not invent
trailer names. The `Ruled-out` line matters most — a future reader needs to know
what was rejected and why, not only what shipped.

All records in this repository are English: issues, milestones, PRs, commit
messages. This is an owner instruction.

Before any PR:

```
python3 -m pytest -q          # establish the baseline yourself, do not assume
```

## Hard constraints

**Never perform a real GitHub star, follow or other write** while developing or
testing, and do not add code that could. Tests use fixtures and fakes. This is not
negotiable and it is not a style preference — the tool's entire premise is that
external actions require explicit approval.

**Dry-run is the default and stays the default.**

**Do not weaken or delete an approval-cycle test.** They are the safety contract's
regression suite. If a change makes one fail, the change is wrong.

## Delegation

The owner's standing instruction is `gpt-worker.sh` with `terra` for general work
and `sol` for architecture, security and final gates. Claude subagents are not to
be used. Worktrees under `~/projects/wt/` need `danger-full-access` because their
git metadata lives in the parent repository, outside a `workspace-write` sandbox.

Write closed packets: goal, task class, exact files, acceptance criteria,
verification, forbidden scope. Require every new test to be seen failing before it
passes — a test never observed to fail is not a test. Two submissions this week
looked complete and were not: one shipped a comment describing a constant that was
never written, and one broke the approval gate while passing its own tests.

Verify submitted work by breaking the production code yourself rather than reading
the test count. That is how both were caught.
