# F3 — 모델 채점 + 계약 검증

## T-301 · 계약 스모크 테스트

**모듈**: `gitseed/grade/smoke.py`

```python
@dataclass(frozen=True)
class SmokeResult:
    passed: bool
    failures: list[str]   # 사람이 읽을 실패 사유
    model: str

def run_smoke(client: GradeClient) -> SmokeResult: ...
```

**검사 항목** — 각각 실패 시 `failures` 에 사유를 남긴다:
1. 알려진 깨끗한 샘플에 `security_flag == False`
2. 알려진 악성 샘플에 `security_flag == True`
3. `description` 이 `⚠` 로 시작하지 않는다 (필드 경계)
4. `security_reason` 이 `security_flag` 와 일관 (false 면 비어 있음)
5. 같은 입력 3회에 `idea`·`skill` 이 동일 (결정성)

**근거**: `docs/PHASE1-EVIDENCE.md` D-3. 씨앗 기본값 7b 는 0/14 로 통과하지만
1.5b 는 9/14 오탐 + 필드 뒤바뀜. 씨앗은 이 차이를 사용자에게 알리지 않는다.

**AC (기계 판정)**
- [ ] 5개 검사 각각을 개별로 실패시키는 테스트 (가짜 클라이언트 주입)
- [ ] `passed == False` 이면 파이프라인이 F3 를 건너뛰고 F2 만으로 완주하는 테스트
- [ ] 스모크 결과가 모델 태그별로 캐시되고, 태그가 바뀌면 재실행되는 테스트
- [ ] **네트워크 없이** 전부 통과 (가짜 클라이언트)

## T-302 · 채점 클라이언트

**모듈**: `gitseed/grade/client.py`

```python
class GradeClient(Protocol):
    def evaluate(self, digest: str) -> GradeResult: ...

@dataclass(frozen=True)
class GradeResult:
    idea: int; skill: int; description: str
    model: str; temperature: float; prompt_version: str
```

**AC**
- [ ] `GradeResult` 에 `model`·`temperature`·`prompt_version` 이 반드시 들어간다
- [ ] `description` 을 임의 문자열로 바꿔도 랭킹이 변하지 않는 테스트
      (산문은 점수에 기여하지 않는다 — ADR-0002 불변식 3)
