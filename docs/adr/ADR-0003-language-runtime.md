# ADR-0003: 언어·런타임·배포

- Status: Accepted (2026-07-26)

## Decision

- **Python 3.11+**, 표준 라이브러리 우선. 씨앗과 같은 언어를 유지한다 — 씨앗 사용자가
  옮겨올 수 있고, 생태계(GitHub API·SQLite)가 표준 라이브러리로 충분하다
- **SQLite**, `PRAGMA user_version` 기반 **순서 있는 마이그레이션**. 씨앗의 애드혹
  컬럼 추가로는 타입 변경·백필을 할 수 없다
- **의존성 최소화**: `urllib` + `sqlite3` 표준 라이브러리. 외부 의존은 테스트 도구만
- **배포**: `pipx install` 또는 `git clone`. CommitLore의 ADR-0011(레지스트리 없는
  git 배포)을 여기에 그대로 적용할지는 v0.1 출하 시점에 재평가한다

## Ruled-out

- **Rust/Go 재작성** | 씨앗 사용자의 이동 경로가 끊긴다. 성능이 병목이 아니다
  (병목은 로컬 LLM 추론)
- **requests/httpx 의존** | 표준 `urllib`로 충분하고, 의존성 0이 설치 실패를 없앤다
- **ORM** | 테이블 하나에 ORM은 과잉. 마이그레이션은 직접 쓴다
