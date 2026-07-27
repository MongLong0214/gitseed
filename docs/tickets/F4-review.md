# T-203 — 리뷰 큐: 승인 없는 외부 쓰기를 구조적으로 불가능하게

PRD: `docs/prd/PRD-F4-review.md` · ADR: `ADR-0001-identity.md`

## 문제

씨앗(`yumiaura/followme`)은 조회만 한다. F4는 이 도구가 처음으로 **외부에 쓰는**
계층이고, GitHub AUP가 금지하는 바로 그 행동(star/follow)을 다룬다. 여기서
"사람이 승인했는지 확인한다"를 `if approved:` 한 줄로 두면, 그 한 줄을 지우거나
분기를 잘못 타는 순간 도구가 AUP 위반 자동화가 된다. 리팩터 한 번, 조건문 하나로
합법과 위반이 갈리는 설계는 이 도메인에서 쓸 수 없다.

## 설계 결정 — 승인은 검사가 아니라 인자다

외부 쓰기 함수는 `Approval` 인스턴스를 **인자로 요구**한다. 그리고 `Approval`은
사람의 입력을 읽은 함수 안에서만 만들어진다.

```
star(target, approval: Approval)   # approval 없이는 호출 자체가 성립하지 않는다
```

이러면 "승인 확인을 빼먹는" 코드 경로가 존재할 수 없다. 승인을 빼먹으려면 인자를
지어내야 하고, `Approval`은 자기가 어떤 입력으로부터 만들어졌는지(`prompt`,
`answer`, `at`)를 들고 있으므로 지어낸 것은 트레일러에 그대로 드러난다.

`--approve-all`도 같은 규칙을 지난다: 목록 전체를 출력한 뒤 사람 입력을 **1회**
받고, 그 1회로부터 항목 수만큼의 `Approval`을 파생시킨다. 비대화형 stdin에서는
파생 자체가 거부된다 — 파이프로 `y`를 밀어넣는 것과 사람이 보는 것은 다르다.

## 범위

- `gitseed/review/approval.py` — `Approval`, `Decision`, 대화형 승인 수집
- `gitseed/review/actions.py` — `star/follow/unstar/unfollow`, 전부 `Approval` 요구
- `gitseed/review/trailers.py` — 승인·거부를 CommitLore 트레일러로 직렬화

범위 밖: 실제 GitHub 호출의 네트워크 계층(F1의 클라이언트를 재사용), TUI.

## AC (기계 판정)

- [ ] AC-1 `star`/`follow`/`unstar`/`unfollow` 어느 것도 `Approval` 없이 호출되지
      않는다 — 인자를 생략한 호출이 `TypeError`로 실패하는 테스트
- [ ] AC-2 `Approval`은 사람 입력을 읽은 경로에서만 생성된다 — 비대화형 stdin에서
      `collect_approval`이 거부하고, 거부 시 어떤 액션도 호출되지 않는다
- [ ] AC-3 되돌림 명령이 실제로 unstar/unfollow를 호출한다 (기록된 호출로 판정)
- [ ] AC-4 거부(`n`)도 트레일러로 남는다 — 승인만 기록하면 "무엇을 안 했는지"가
      사라지고, 안 한 이유가 이 도구의 핵심 산출물이다
- [ ] AC-5 생성된 트레일러 블록이 `commitlore validate`를 통과한다
- [ ] AC-6 `--approve-all`이 사람 입력 1회를 요구하고, 비대화형에서 거부된다

## 뮤테이션 (테스트가 실제로 지키는지의 증거)

각 뮤테이션은 최소 1개 테스트를 깨야 한다.

| # | 변형 | 깨져야 할 것 |
|---|---|---|
| M1 | `actions.star`에서 `approval` 인자 제거 | AC-1 |
| M2 | `collect_approval`의 `isatty` 검사 제거 | AC-2, AC-6 |
| M3 | `Decision.REJECT`를 트레일러에서 누락 | AC-4 |
| M4 | `Approval.at`를 선택 인자로 완화 | 승인 시각 없는 `Approval` 생성이 통과 |
| M5 | `--approve-all`이 입력 없이 전건 승인 | AC-6 |
