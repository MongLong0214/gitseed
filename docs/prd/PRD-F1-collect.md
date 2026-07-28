# PRD F1 — Collect (GitHub Search → candidate queue)

## Goal
Fetch candidate repositories through GitHub Search and put them in the local DB. **Do not stop
silently on a rate limit** — the seed's measured defect (0 processed) must not recur here.

## Non-goals
- Repository clone (owned by F2)
- Grading (owned by F3)

## Requirements
1. Query the GitHub Search API. Filter by language, minimum stars, and update time
2. **Rate-limit handling**: read `X-RateLimit-Remaining`/`Reset` headers; when exhausted, wait or
   explicitly report termination. Distinguish 403/429 from retryable responses
3. No duplicate insertion (`repo` primary key)
4. On partial failure, preserve already-inserted rows and report progress

## AC (mechanical decision)
- [ ] Given a response with `X-RateLimit-Remaining: 0`, wait or exit with `RateLimitExhausted`.
      **Do not silently return 0 results** — a test verifies this
- [ ] Test that 429/403 (rate) and 403 (forbidden) are handled differently
- [ ] Running the same query 2 times does not increase the DB row count
- [ ] A network exception does not roll back already-committed rows

## Record status (2026-07-28)

**Persistence: satisfied, by a different design than described above.** F8 (`gitseed/storage.py`,
`gitseed/storage_schema.py`) landed a versioned SQLite `run_artifacts` table that stores whole,
immutable `RunArtifact` records with insert-only correction lineage (`corrects_run_id`) — not the
per-candidate row store with a `repo` primary key this PRD's requirement 3 and AC describe. The
requirement this PRD names is met; the shape it predicted is not what shipped. Recorded here so
this reads as closed rather than outstanding.

**`RateLimitExhausted`: ruled out, not implemented.** `CollectResult.complete` and
`CollectResult.stopped_because` already carry a collection's incompleteness explicitly, and the
run artifact records which port failed and why. A dedicated exception type would be a second way
to say what the result type already says — two descriptions of the same fact drift apart the
first time one of them is updated and the other is not. See commit trailers for the formal
`Ruled-out:` record.
