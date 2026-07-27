# ADR-0002: v0.1.0 scope — what to include and what to cut

- Status: Accepted (2026-07-26)

## Context

The seed has 993 lines, 0 tests, 0 CI, and no license. The rebuild must not be "the same feature,
bigger," but **the minimum product that can produce evidence**. Silent omission is forbidden, so
leave every cut item as a Backlog issue (factory invariant 4).

## Decision — what goes into v0.1.0

| Layer | Work |
|---|---|
| **L0 collect** | Collect candidates with GitHub Search. **Rate-limit handling required** (0 cases in seed) |
| **L1 digest** | Without cloning, retrieve only file list, README, and manifests through GitHub API. Shallow clone is optional |
| **L2 deterministic screen** | Classify dependencies, network calls, obfuscation, and installation scripts **with regular expressions and ASTs**. No LLM |
| **L3 model grade** | Use a local LLM for idea, skill, and description. **Must pass a contract-compliance smoke test at startup** |
| **L4 review queue** | Evidence-backed ranked list. External action occurs only after a person approves each item |
| **L5 record** | Record approvals, rejections, and their evidence as CommitLore trailers |

## Decision — core invariants

1. **Do not trust a model without verification.** At startup, smoke-test it with a known clean
   sample and a known malicious sample. On failure, disable L3 and operate with L2 only (degraded function,
   preserved integrity). Evidence: 64% false positives on clean code with 1.5b, 0% with 7b and 32b (`PHASE1-EVIDENCE.md` D-3)
2. **L2 owns security decisions.** The LLM may offer an opinion but cannot classify something
   as malicious by itself. The costs of false positives and false negatives are asymmetric
3. **Prose does not enter the grading path.** Free-text fields are for display and do not
   contribute to scores. Evidence: even 7b leaked the security prefix into `description`
4. **External action occurs only after human approval.** See ADR-0001

## Ruled-out

- **Clone the entire repository** | the seed creates a digest after cloning. This pays disk, time, and
  malicious-code execution risk even though API metadata provides most signals. Shallow clone is opt-in
  only for deep L2 inspection
- **Reevaluate every time without caching grading results** | the seed's approach. Local LLM calls are the most expensive step
- **Web UI** | v0.1 is CLI-only. A terminal is enough for the review queue
- **Multi-model ensemble** | no evidence justifies the cost. Single model + contract verification comes first

## Backlog (cut items — no silent omission)

B-01 web dashboard · B-02 multi-model consensus · B-03 organization-level curation sharing ·
B-04 embedding-based similar-repository discovery · B-05 GitHub App integration · B-06 grading-history trend analysis
