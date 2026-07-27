# ADR-0003: language, runtime, and distribution

- Status: Accepted (2026-07-26)

## Decision

- **Python 3.11+**, standard library first. Keep the same language as the seed — seed users
  can migrate, and the standard library is enough for the ecosystem (GitHub API and SQLite)
- **SQLite**, **ordered migrations** based on `PRAGMA user_version`. The seed's ad hoc
  column additions cannot change types or backfill data
- **Minimize dependencies**: standard-library `urllib` + `sqlite3`. External dependencies only for test tools
- **Distribution**: `pipx install` or `git clone`. Reevaluate at v0.1 shipment whether to apply
  CommitLore ADR-0011 (registry-free git distribution) unchanged here

## Ruled-out

- **Rewrite in Rust/Go** | breaks the migration path for seed users. Performance is not the bottleneck
  (local LLM inference is)
- **requests/httpx dependency** | standard `urllib` is enough, and 0 dependencies eliminate installation failures
- **ORM** | an ORM is excessive for one table. Write migrations directly
