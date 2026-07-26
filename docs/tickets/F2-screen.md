# F2 — 결정적 보안 스크린 (키스톤)

LLM 없이 동작하는 유일한 계층이라 **키스톤**이다. F3(모델 채점)가 스모크 테스트에
실패해도 파이프라인은 F2만으로 완주해야 한다(ADR-0002 불변식 1).

## T-201 · 신호 추출기

**모듈**: `gitseed/screen/signals.py`

```python
@dataclass(frozen=True)
class Signal:
    kind: str          # "install-script" | "obfuscation" | "network" | "postinstall" | "dep"
    severity: str      # "low" | "high"
    path: str          # 인용: 파일 경로
    line: int          # 인용: 1-기반 줄 번호
    excerpt: str       # 인용: 그 줄 (최대 120자)

def scan_text(path: str, text: str) -> list[Signal]: ...
```

**규칙** (각각 테스트 1개 이상):
| kind | 탐지 | severity |
|---|---|---|
| `install-script` | `curl`/`wget` 출력이 `sh`/`bash`로 파이프 | high |
| `obfuscation` | base64 blob 200자 이상, 또는 hex 리터럴 200자 이상 | high |
| `postinstall` | `package.json` 의 `postinstall`/`preinstall` | high |
| `network` | 하드코딩된 IP, 또는 `.onion`/단축 URL 도메인 | low |
| `dep` | 의존성 이름이 알려진 타이포스쿼팅 목록과 일치 | high |

**AC (기계 판정)**
- [ ] 모든 `Signal` 이 `path`·`line`·`excerpt` 를 갖는다. 인용 없는 신호를 만들면
      생성자가 `ValueError`. 이를 검증하는 테스트
- [ ] 깨끗한 샘플 10개(`tests/fixtures/clean/`)에서 신호 0건
- [ ] 악성 패턴 샘플 5개(`tests/fixtures/malicious/`)에서 각 kind 최소 1건
- [ ] `scan_text` 는 네트워크·모델·서브프로세스를 부르지 않는다 (import 검사 테스트)
- [ ] 같은 입력 2회 호출이 같은 리스트를 반환 (결정성)

## T-202 · 심각도 집계

**모듈**: `gitseed/screen/verdict.py`

```python
def severity_of(signals: Sequence[Signal]) -> str:  # "none" | "low" | "high"
```

`high` 신호 1개 이상 → `high`. `low` 만 있으면 `low`. 없으면 `none`.
**불리언으로 뭉개지 않는다** — 씨앗의 `security_flag` 가 그 실패였다.

**AC**
- [ ] 세 경로 각각 테스트
- [ ] 빈 리스트가 `none` (예외 아님)
