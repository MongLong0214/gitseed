# ADR-0006: no unattended external writes

- Status: Accepted (2026-07-27)
- Supersedes: the unattended follow/star decision recorded in `docs/PHASE0.md`; PHASE0 remains the historical record.

## Context

PHASE0 treated the seed's automatic follow and star actions as retained because the owner had not selected a human gate as a required differentiator.  That was reasonable with the facts then available: the rebuild was intended to retain the seed's repository-curation workflow, and `--dry-run` was already known to exist in the seed.

The later policy finding changes that premise.  GitHub's Acceptable Use Policies prohibit automated starring or following, and the owner's standing instruction is that gitseed performs no live star or follow at all.  Retaining unattended actions is therefore neither a valid default nor an optional operating mode.

## Decision

Gitseed performs no live star or follow.  `--dry-run` is the default.

No code path may make a new external write without an `Approval` argument constructed from human input read on a TTY.  This is a structural boundary, not an `if approved` check: `gitseed/review/actions.py` makes approval a required argument to the star/follow action functions, while `collect_approval` rejects non-TTY input.  The real-PTY review-cycle test in `tests/test_review_cycle.py` exercises that production path and records only the action authorized by the terminal response.

The approval boundary remains required even while the standing instruction prohibits live star/follow: it prevents a future implementation from turning a policy change or operational mistake into unattended automation.

## Revisit condition

Revisit this ADR only when both conditions are demonstrably true:

1. GitHub's then-current published Acceptable Use Policies explicitly permit this tool's automated star/follow behavior.
2. The owner has given a new written instruction that expressly authorizes gitseed to perform live star/follow actions.

Meeting either condition alone does not reopen the decision.  A new ADR must record the cited policy text, the owner instruction, and the resulting safety boundary before any live behavior is enabled.
