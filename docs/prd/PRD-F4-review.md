# PRD F4 — Review queue (human-executed)

## Goal
Present an evidence-backed ranked list and turn **only items individually approved by a person**
into external actions.

## Evidence
`docs/adr/ADR-0001-identity.md`. GitHub AUP prohibits "automated starring or following"
and has no quantity threshold. If a person decides each item, the tool is a UI, not automation.

## Requirements
1. Show each item's **score, security signals, and citations** together in the ranked list
2. Per-item approval `[s]tar [f]ollow [b]oth [n]ext [q]uit`
3. `--approve-all`: show the full list, then approve once (still a human decision)
4. Record approvals, rejections, and their evidence in commits as **CommitLore trailers**
5. Provide reversal commands (`unstar`/`unfollow`) for executed external actions — the seed has only queries

## AC (mechanical decision)
- [ ] Test that no external API write call occurs without approval (default non-dry-run path)
- [ ] Test that reversal commands actually call unstar/unfollow
- [ ] Approval results remain as commit trailers and `commitlore validate` returns 0
- [ ] `--approve-all` also requires 1 human input (reject in non-interactive mode)

## Correction — 2026-07-28 deep review

This PRD's Goal promises "an evidence-backed ranked list," and requirement 4 promises
approvals and rejections are recorded "in commits as CommitLore trailers," together with
"their evidence." A deep review of `dev` at `d0e1ecd` found both promises only partially kept
by what has shipped so far — the AC above is satisfied at face value (a trailer block that
passes `commitlore validate` is produced), but "their evidence" is narrower in the shipped
commit than in this PRD's own requirement. Full findings and tracked issues are recorded in
[`docs/tickets/F4-review.md#correction-2026-07-28-deep-review`](../tickets/F4-review.md); in
summary:

- The ranked list a person reviews (radar) and the ranking that actually drives the approval
  prompt are, today, two different scores computed two different ways — see
  `docs/adr/ADR-0009-single-ranking-source.md`, which records the decision to unify them on
  the deterministic score, and [issue #36](https://github.com/MongLong0214/gitseed/issues/36).
- "Their evidence" — the prompt a person saw, their literal answer, when they answered, and
  the score/risk/coverage state at that moment — is not what reaches the commit; only the
  target and decision (plus an optional reason) do. See
  [issue #40](https://github.com/MongLong0214/gitseed/issues/40) and, for bulk approval
  specifically, [issue #37](https://github.com/MongLong0214/gitseed/issues/37).
- Requirement 5's reversal-command promise is undercut by the same gap: with no durable action
  outcome recorded, `Undo: easy` (written into every commit with a live action) is currently
  an unproven claim rather than a tested property. See
  [issue #38](https://github.com/MongLong0214/gitseed/issues/38).
- Nothing in this PRD anticipated action atomicity or ordering relative to the decision commit;
  both are now tracked as their own gaps, not requirement violations, at
  [issue #41](https://github.com/MongLong0214/gitseed/issues/41) and
  [issue #42](https://github.com/MongLong0214/gitseed/issues/42).

This is recorded as a correction, not a rewrite of the requirements above: the requirements
are still the target. What shipped meets them at the type-safety layer (AC-1/AC-2 equivalents
in `docs/tickets/F4-review.md`) and not yet at the permanent-record layer.
