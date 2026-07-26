# PRD F4 — 리뷰 큐 (사람이 실행한다)

## 목표
근거가 붙은 랭킹 목록을 제시하고, **사람이 건건이 승인한 것만** 외부 행동으로
옮긴다.

## 근거
`docs/adr/ADR-0001-identity.md`. GitHub AUP가 "automated starring or following"을
금지하고 수량 임계가 없다. 사람이 각 항목을 결정하면 도구는 UI이지 자동화가 아니다.

## 요구사항
1. 랭킹 목록에 각 항목의 **점수·보안 신호·인용**을 함께 표시
2. 건별 승인 `[s]tar [f]ollow [b]oth [n]ext [q]uit`
3. `--approve-all`: 목록 전체를 보여준 뒤 한 번 승인 (여전히 사람 결정)
4. 승인·거부와 그 근거를 **CommitLore 트레일러**로 커밋에 기록
5. 실행된 외부 행동은 되돌림 명령(`unstar`/`unfollow`)을 제공한다 — 씨앗은 조회만 있다

## AC (기계 판정)
- [ ] 승인 없이 외부 API 쓰기 호출이 발생하지 않는 테스트 (dry-run 아닌 기본 경로)
- [ ] 되돌림 명령이 실제로 unstar/unfollow를 호출하는 테스트
- [ ] 승인 결과가 커밋 트레일러로 남고 `commitlore validate`가 0을 반환한다
- [ ] `--approve-all`도 사람 입력 1회를 요구한다 (비대화형에서는 거부)
