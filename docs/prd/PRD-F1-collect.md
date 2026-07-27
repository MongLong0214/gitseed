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
