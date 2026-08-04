# Changelog

Newest first.

## 0.3.0 — 2026-08-04

### Upgrade reasons

- The live scanner never read `package.json`, or any other manifest, lockfile, or workflow
  file — `SOURCE_EXTENSIONS` had no `.json` entry, so a malicious `postinstall` hook was
  invisible to a real run even though the detection rule itself was implemented and passed
  its own tests against a fixture. Priority filenames (manifests, lockfiles, `Dockerfile`,
  `Makefile`, `.github/workflows/*.yml|yaml`) are now selected before the 20-file scan cap is
  applied, not merely early within it, so a repository can no longer push a manifest out of
  the scan by tree position. Files dropped by the 20-file/500KB caps now change the reported
  severity to `none-found-in-scanned-files` instead of an indistinguishable `none`. (#45, #48,
  #49)
- A candidate with zero readable files, zero score coverage, and an unknown risk verdict
  rendered as "recommended" — the same as a candidate that had been fully scanned and come
  back clean. Recommendation now carries four states (`BLOCKED` / `INSUFFICIENT_EVIDENCE` /
  `REVIEW` / `NOT_PRIORITY`), so "we looked and it does not rank" is distinct from "we could
  not see enough to say." Separately, the radar table and the interactive approval queue
  ranked candidates in two different orders (deterministic score vs. the LLM's idea+skill
  grade); all four surfaces (radar, `--json`, `explain`, approval) now share one ranking. (#36,
  #46)
- A GitHub Search result GitHub itself flagged as incomplete (`incomplete_results`) could
  still be reported as a complete `CollectResult`, because both `incomplete_results` and
  `total_count` were read from the response and then discarded. Both are now carried into the
  artifact and printed as candidate coverage, with the reason named when coverage is partial.
  (#47)
- `run_smoke()`'s exception boundary only wrapped its determinism-repeat loop; an exception
  from `flags_malicious()` propagated uncaught and could crash the whole CLI instead of
  degrading to a deterministic-only result, the way every other smoke failure is documented to
  do. Separately, `classify()` recognized `Retry-After` as evidence of a secondary rate limit
  but `parse()` never read its value, so `collect(wait=True)` could compute a zero-second wait
  and retry immediately into the limit that had just throttled it. Both are fixed; the wait is
  now bounded so a missing header, a fast clock, or a hostile `Retry-After` value cannot park
  the process either. (#50, #51)
- Repository-metadata 403 responses were always classified as forbidden, even when GitHub's
  own response metadata showed the quota was exhausted, so the rate-limit remedy was never
  suggested for a quota-exhausted metadata call. (#52)
- Metadata (commits, contributors, license — three or more API calls per candidate) was
  fetched for every candidate before screening ran, so a default `--limit 10` run could
  exhaust the unauthenticated rate budget before screening a single file; candidates that
  screening then blocked had paid for metadata they never needed. The fetch now happens only
  for candidates that survive screening. Separately, a model that answered with prose instead
  of a grade produced a raw Python exception ("could not produce a grade at invalid literal
  for int() with base 10") that read as a gitseed defect. The response is now validated and
  the failure is attributed to the named model, with no retry, default, or midpoint substitute
  grade — a substituted number would have entered the ranking indistinguishable from one the
  model actually produced. (#53, #70)
- `--replay` recomputed stored responses through whatever engine version happened to be
  installed, silently, so its promise of reproducing a prior run did not hold after an engine
  changed. It now states when stored responses are recomputed with matching engines, stops by
  default when an engine changed, and requires an explicit `--allow-engine-mismatch` to
  recompute with current code anyway. (#54)
- `RunArtifact` embedded up to roughly 500KB of source per candidate by default, duplicating
  whatever secrets, malicious payloads, or mismatched-license source that candidate contained
  into every stored or shared artifact. `digest` is now the default source mode, with
  `metadata-only` and `full-source` available as explicit opt-ins. (#56)
- `OLLAMA_HOST` was ignored; gitseed always talked to `localhost:11434` for both model
  discovery and grading. It is now read for both endpoints, accepting a bare `host:port` or a
  full `http(s)://` URL, and still defaults to `localhost:11434` when unset. (#71)
- Local-model grading reused the deterministic scanner's much larger source budget, sent an
  unbounded prompt to Ollama, and left generated tokens uncapped. Large repositories could
  therefore exhaust a 32B model's context or time out without stating what evidence the model
  actually saw. Grading now uses a deterministic digest capped at 23KB and sampled across at
  most 16 files, rejects complete prompts over 24KB before transport, caps output at 128
  tokens, records prompt version `cli-v2-bounded`, and defaults to 240 seconds per model
  response. The deterministic security scan still receives the original selected files.
- `pip install` failed on setuptools ≥61 because `assets/` was discovered as a second
  top-level package, and there was no console-script entry point, so `python -m gitseed` was
  the only way to invoke the tool. Both are fixed: `[tool.setuptools.packages.find]` now
  scopes discovery to `gitseed*`, and `gitseed --help` works after install via a
  `[project.scripts]` entry. (#39, #68)

### Correctness

- `RunArtifact` recorded no engine versions, so an artifact produced by older code loaded and
  was silently misread rather than rejected. It now fails explicitly (`artifact schema version
  mismatch: recorded N, current M`) when the recorded and current schema versions disagree.
  Its collections (candidate lists, results) also now convert to tuples at the artifact
  boundary, so the frozen dataclass is immutable once an artifact exists, rather than wrapping
  mutable lists that could still be mutated after construction. (#55, #57)
- Category packs, including the built-in `coding-agents` pack, existed as library code with no
  path from the CLI, `RunRequest`, or the run artifact — selecting a category had no effect on
  a run. Category selection now travels from the CLI through execution into the artifact, and
  the artifact embeds the selected pack definitions and their extracted evidence rather than a
  bare `pack_version`, so a stored run's categorization can be re-derived from the artifact
  itself. The `coding-agents` pack now also requires an independent agent-runtime evidence
  signal rather than concluding from `AGENTS.md` presence alone, with `AGENTS.md`'s own text
  excluded so the same signal cannot be counted twice. (#58, #59, #60)
- Category-pack validation checked evidence kinds against a separate, hand-maintained
  allow-list, which could silently accept an evidence kind no collector actually produces. It
  now derives available evidence kinds from the registered evidence-producer methods directly,
  and names both the pack and the evidence kind when a requirement is unsatisfiable. (#61)
- Metadata adapters collapsed raw counts (commits in the preceding 30 days, total contributor
  count) into booleans before storing them, discarding the values any future analysis would
  need. Schema-6 artifacts now retain the raw commit count, contributor count, and license
  payload, each with its evidence basis, alongside the existing score. (#64)
- Repository star observations are now recorded to a local store as raw `(repository,
  observed_at, stars)` rows; none were recorded before this release. The store deliberately
  keeps counts rather than deltas, so later rows stay correct as they arrive. (#65)
- GitHub Search is now requested with `sort=updated&order=desc` instead of GitHub's default
  best-match order, and the exact `q`, `sort`, `order`, `pages`, and `per_page` values used are
  recorded in the artifact next to the collection result. This changes which repositories
  occupy a capped or partial result set relative to prior runs — see ADR-0013 under "Claims
  withdrawn" for what this ordering does and does not mean. (#67)

### Safety

- External GitHub actions (star, follow) used to run before anything durable recorded that
  they were authorized — perform, then render, then commit — so a crash in between left GitHub
  changed with no local record of whose approval it was under. An intent commit is now written
  first, the action runs second, and the outcome is committed third, so a crash at any point
  leaves a record of what was authorized and what may already have run. Multi-target and
  multi-action runs are still not atomic (GitHub calls cannot be made atomic), but a failure
  partway now compensates what already succeeded through the existing undo path rather than
  leaving it unrecorded, and the approval's own prompt, answer, and timestamp now reach the
  commit trailer, as `Approval`'s docstring already claimed they did. (#40, #41, #42)
- Every commit recording a live action asserted `Undo: easy` unconditionally, regardless of
  whether an undo path actually existed for that outcome. Outcome commits now derive `Undo`
  from the specific action and its result: a successful star is easy, a successful follow is
  costly, and an unknown or compensated-failure outcome is recorded as permanent. Separately,
  the empty-tree object ID used for a repository's first decision commit was a hardcoded SHA-1
  constant, invalid in a SHA-256 repository; it is now asked of git directly, so both
  repository formats can record a first decision. (#38, #44)
- Bulk approval (`--approve-all`) recorded only a one-line summary and a target count, not the
  listing a reviewer actually saw. It now retains the table shown — up to 20 rows, with a
  stated count of omitted rows and a SHA-256 of the complete displayed listing for batches
  above that. The decision commit's `git update-ref` already carried an expected-old-value
  guard against a concurrent gitseed process silently overwriting another process's commit;
  its regression coverage now proves that guard for both an unborn repository and an existing
  `HEAD`. (#37, #43)
- A failed observation-history write (the local star/time record) is now deliberately isolated
  from the approval/action result, so it cannot turn a successful, approved action into a
  failed run — it prints a warning instead. The run itself, and any failure writing the run
  artifact, still surface normally. (#65)
- Local run history (SQLite, `./.gitseed/runs.db` by default) is now wired into the CLI, and
  review persistence runs after the approval/action path completes, so an unavailable run
  store cannot bypass the GitHub-write gate. (#62)

### Claims withdrawn

- **ADR-0012 — undervaluation requires an expected-attention baseline.** Gitseed's
  deterministic score remains an activity signal (recent commit cadence, contributor breadth,
  license presence). As of this release, gitseed must not emit an undervaluation, momentum,
  growth, or breakout score, because none of those can be computed from what it collects
  today: there is no category-population baseline against which to measure expected
  attention, and star observations only began with this release, so there is no repeated,
  time-separated measurement to derive a velocity or outcome from yet. (#63)
- **ADR-0013 — search ordering is a collection policy, not evidence.** GitHub Search results
  are now ordered by repository update time, descending, and that ordering is stated as a
  collection preference for recently-updated repositories, not as evidence of quality or a
  claim that a recently-updated, low-attention repository is more likely to gain attention
  later. Gitseed has neither an expected-attention baseline nor time-separated outcome data to
  support that claim, for the same reason ADR-0012 gives. (#67)

## 0.2.0 — 2026-07-28

Initial tagged release.
