# F3 — Model grading + contract verification

## T-301 · Contract smoke test

**Module**: `gitseed/grade/smoke.py`

```python
@dataclass(frozen=True)
class SmokeResult:
    passed: bool
    failures: list[str]   # human-readable failure reasons
    model: str

def run_smoke(client: GradeClient) -> SmokeResult: ...
```

**Checks** — on each failure, leave the reason in `failures`:
1. `security_flag == False` on a known clean sample
2. `security_flag == True` on a known malicious sample
3. `description` does not begin with `⚠` (field boundary)
4. `security_reason` is consistent with `security_flag` (empty when false)
5. `idea` and `skill` are identical across 3 runs with the same input (determinism)

**Evidence**: `docs/PHASE1-EVIDENCE.md` D-3. The seed's default 7b passes with 0/14,
but 1.5b produces 9/14 false positives + swapped fields. The seed does not tell the user about this difference.

**AC (mechanical decision)**
- [ ] Tests fail each of the 5 checks individually (inject fake client)
- [ ] Test that when `passed == False`, the pipeline skips F3 and completes with F2 only
- [ ] Test that smoke results are cached by model tag and rerun when the tag changes
- [ ] Everything passes **without a network** (fake client)

> **Record status (2026-07-28)**: superseded by F6 (`gitseed/grade/smoke.py`, wired in
> `gitseed/application.py`) — see `docs/prd/PRD-F3-grade.md`. Model-tag caching is ruled out for
> lack of a measured problem; `run_smoke` recomputes every run and there is no cache to rerun.
>
> **Correction — 2026-07-28 deep review**: F6's `run_smoke()` guards the determinism-repeat
> loop over `client.evaluate(...)` with a `try/except`, but the subsequent
> `_check_clean(client)`/`_check_malicious(client)` calls are not inside that guard. An
> exception from `client.flags_malicious()` — a malformed response, a timeout, a client bug —
> propagates uncaught through `application.execute()`, which calls `run_smoke()` unguarded, and
> can crash the whole CLI instead of degrading to a deterministic-only artifact the way check 1
> above ("disable F3, operate with F2 only") promises for a smoke failure. Tracked as
> [issue #50](https://github.com/MongLong0214/gitseed/issues/50) (GS-P1-001).

## T-302 · Grading client

**Module**: `gitseed/grade/client.py`

```python
class GradeClient(Protocol):
    def evaluate(self, digest: str) -> GradeResult: ...

@dataclass(frozen=True)
class GradeResult:
    idea: int; skill: int; description: str
    model: str; temperature: float; prompt_version: str
```

**AC**
- [ ] `GradeResult` must include `model`, `temperature`, and `prompt_version`
- [ ] Test that changing `description` to an arbitrary string does not change ranking
      (prose does not contribute to scores — ADR-0002 invariant 3)
