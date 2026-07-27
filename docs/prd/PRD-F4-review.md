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
