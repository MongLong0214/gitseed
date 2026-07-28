# T-203 — Review queue: make external writes without approval structurally impossible

PRD: `docs/prd/PRD-F4-review.md` · ADR: `ADR-0001-identity.md`

## Problem

The seed (`yumiaura/followme`) only reads. F4 is the first layer where this tool **writes externally**,
and it handles the exact actions GitHub AUP prohibits (star/follow). If "check whether a person approved"
is one line, `if approved:`, then deleting that line or taking the wrong branch turns the tool into
AUP-violating automation. A design where one refactor or conditional separates compliance from violation
cannot be used in this domain.

## Design decision — approval is an argument, not a check

External-write functions **require an `Approval` instance as an argument**. And `Approval` is created
only inside a function that reads human input.

```
star(target, approval: Approval)   # the call itself is invalid without approval
```

This makes a code path that "forgets to check approval" impossible. To omit approval, a caller must
fabricate the argument, and because `Approval` carries the input it came from (`prompt`, `answer`, `at`),
the fabrication appears directly in the trailer.

`--approve-all` follows the same rule: print the entire list, receive human input **1 time**,
and derive one `Approval` per item from that 1 input. Derivation itself is rejected on non-interactive
stdin — piping in `y` is different from a person seeing the list.

## Scope

- `gitseed/review/approval.py` — `Approval`, `Decision`, interactive approval collection
- `gitseed/review/actions.py` — `star/follow/unstar/unfollow`, all require `Approval`
- `gitseed/review/trailers.py` — serialize approvals and rejections as CommitLore trailers

Out of scope: network layer for actual GitHub calls (reuse F1's client), TUI.

## AC (mechanical decision)

- [ ] AC-1 None of `star`/`follow`/`unstar`/`unfollow` can be called without `Approval`
      — test that a call omitting the argument fails with `TypeError`
- [ ] AC-2 `Approval` is created only on a path that reads human input — on non-interactive stdin,
      `collect_approval` rejects, and no action is called after rejection
- [ ] AC-3 Reversal commands actually call unstar/unfollow (decided from recorded calls)
- [ ] AC-4 Rejection (`n`) also remains as a trailer — recording only approvals erases "what was not done,"
      and the reason it was not done is this tool's core output
- [ ] AC-5 The generated trailer block passes `commitlore validate`
- [ ] AC-6 `--approve-all` requires 1 human input and rejects non-interactive use

## Mutations (evidence that tests actually protect the behavior)

Each mutation must break at least 1 test.

| # | Mutation | Must break |
|---|---|---|
| M1 | Remove the `approval` argument from `actions.star` | AC-1 |
| M2 | Remove the `isatty` check from `collect_approval` | AC-2, AC-6 |
| M3 | Omit `Decision.REJECT` from the trailer | AC-4 |
| M4 | Make `Approval.at` optional | Creating an `Approval` without an approval time succeeds |
| M5 | Make `--approve-all` approve every item without input | AC-6 |

## Correction — 2026-07-28 deep review

This ticket's Design Decision section states, of `Approval`: "because `Approval` carries the
input it came from (`prompt`, `answer`, `at`), the fabrication appears directly in the
trailer." That is true of the in-memory `Approval` object at approval time. It is not true of
the commit the review-cycle actually produces: `render_block()`
(`gitseed/review/trailers.py`) never serializes `prompt`, `answer`, or `at` — only `target`,
`decision`, and an optional reason reach the trailer. The type-safety guarantee this ticket
documents (AC-1/AC-2, "approval is an argument, not a check") is real and holds at the
call-site level; it does not extend to the permanent record the way this ticket's own prose
implies. A deep review of `dev` at `d0e1ecd` (post-dating the F4 commit feature landed in
`c0fb66f`) found the following, each tracked as its own issue rather than folded into this
historical record:

- **Approval evidence does not survive to the commit.** `prompt`/`answer`/`at`, the Radar
  score, risk verdict, and coverage state at decision time are all absent from the trailer
  block; only `target` and `decision` (plus an optional reason) are recoverable later.
  [Issue #40](https://github.com/MongLong0214/gitseed/issues/40) (GS-P0-005).
- **Bulk approval (`--approve-all`) degrades this further**: `Approval.prompt` for a bulk
  decision holds only a one-line summary ("N items"), never the actual displayed listing.
  [Issue #37](https://github.com/MongLong0214/gitseed/issues/37) (GS-P1-015).
- **External GitHub actions run before the decision commit exists**, with no durable intent or
  outcome record in between — a commit failure after a successful star/follow leaves the
  action unrecorded. [Issue #41](https://github.com/MongLong0214/gitseed/issues/41)
  (GS-P0-003).
- **A `BOTH` approval, or multiple approved targets in one run, is not atomic** and has no
  compensation on partial failure. [Issue #42](https://github.com/MongLong0214/gitseed/issues/42)
  (GS-P0-004).
- **`Undo: easy` is written unconditionally** whenever a session has a non-reject decision,
  with no actual undo path, action-outcome persistence, or compensation behind the claim.
  [Issue #38](https://github.com/MongLong0214/gitseed/issues/38) (GS-P1-016).
- **`git update-ref HEAD <sha>` has no expected-old-value**, so two concurrent gitseed
  processes can race and silently drop one process's decision commit.
  [Issue #43](https://github.com/MongLong0214/gitseed/issues/43) (GS-P1-013).
- **The empty-tree SHA used for a root decision commit is a hardcoded SHA-1 constant**,
  invalid in a SHA-256-object-format repository.
  [Issue #44](https://github.com/MongLong0214/gitseed/issues/44) (GS-P1-014).
- **Separately from this ticket's own scope but load-bearing for what a person is approving**:
  the radar table and the approval prompt currently rank candidates by two different scores.
  See `docs/adr/ADR-0009-single-ranking-source.md` and
  [issue #36](https://github.com/MongLong0214/gitseed/issues/36) (GS-P0-002).

AC-5 ("the generated trailer block passes `commitlore validate`") is unaffected by any of the
above — the block that is produced is valid CommitLore; the finding is about what the block
omits, not its grammar.
