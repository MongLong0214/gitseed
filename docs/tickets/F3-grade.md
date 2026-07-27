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
