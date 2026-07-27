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
