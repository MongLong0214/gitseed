# ADR-0001: 정체성 — 이름 `gradelore`, 그리고 씨앗에서 무엇을 버리는가

- Status: Accepted (2026-07-26)
- 씨앗: `yumiaura/followme` (Python CLI, 993줄, 60 스타)

## Context

Phase 3-A는 코드 작성 전에 이름을 굳히라고 요구한다. 미루면 기하급수적으로 비싸진다
(CommitLore가 `Annals`→`CommitLore` 전면 개명으로 실증).

## Decision — 이름

**`gradelore`.** PyPI·GitHub 양쪽 미선점을 실측했다(둘 다 404).

선정 근거:
- 하는 일(`grade`)을 직접 말한다 — 검색 발견성
- 오너의 기존 프로젝트 `CommitLore`와 같은 명명 패턴(도메인어 + `lore`)이라
  제품군을 이룬다. 둘 다 "검증 가능한 기록"이 핵심이라는 점에서 일관된다
- 발음이 쉽고 나쁜 어감이 없다

## Ruled-out — 이름

- **`repoassay`** | `github.com/repoassay`가 **2026-07-24 생성된 Organization**이고
  bio가 "Open-source tools for evidence-first repository analysis and test
  strategy" — 우리 도메인과 정확히 겹친다. ADR-0008(CommitLore)이 `menhir`를 기각한
  것과 같은 유형의 활성 충돌
- **`tailings`** | 광업에서 **폐석**을 뜻한다. 골라낸 것이 쓰레기라는 어감
- **`repocull`** | `cull`은 선별과 도살을 동시에 뜻해 모호하고, 우리는 남길 것을
  고르지 버릴 것을 고르지 않는다
- **`siftwork`** | 은유는 맞으나 `repo`도 `grade`도 없어 검색으로 도달하기 어렵다
- **`repograder`** | 정확하지만 지나치게 일반명사라 브랜딩이 서지 않는다
- **`winnow` / `sluice` / `gradebot` / `codeassay`** | PyPI 선점 (각각 실측 200)
- **`repolens` / `repolore` / `orelight`** | GitHub org 선점(빈 계정). 활성 충돌은
  아니나 깨끗하지 않다

## Decision — 씨앗에서 버리는 것

**무인 자동 팔로우·스타를 기본에서 제거한다.**

GitHub Acceptable Use Policies가 명시적으로 금지한다 — *"rank abuse, such as
automated starring or following"*. 조문에 **수량 임계가 없다.** 하루 10개도
automated starring이다. 그리고 ICSE 2026(CMU)의 StarScout 탐지는 **계정 행동
패턴**으로 잡지 총량으로 잡지 않으므로, 저volume 자동화는 오히려 패턴이 선명하다.

대체: **근거가 붙은 리뷰 큐.** 발견·채점·보안 스크리닝까지 전부 자동, 마지막 실행만
사람이 건건이 승인한다. 사람이 결정하고 도구는 UI이므로 automated starring이 아니다.

## Ruled-out — 동작

- **무인 자동 스타·팔로우 유지** | GitHub AUP "rank abuse" 위반. 오너 계정 정지
  위험이고, ToS 위반 도구를 엔터프라이즈 레벨이라 부를 수 없다
- **하루 N개 제한으로 탐지 회피** | 조문에 수량 기준이 없어 위반은 그대로이고,
  패턴 기반 탐지 앞에서 효과도 없다
- **씨앗의 4단계 체이닝(`fetch→evaluate→subscribe→star`) 유지** | 마지막 두 단계가
  위 이유로 사라지므로 파이프라인 형태 자체가 달라진다

## Consequences

- 프로젝트는 **읽기 전용 분석 + 사람이 실행하는 큐레이션**이 된다. 정책 위험 0
- 씨앗과 코드를 공유하지 않는다. 아이디어(로컬 LLM 레포 채점)만 계승한다
- `--approve-all` 배치 승인은 제공한다(사람이 목록을 보고 한 번 승인). 다만 cron
  무인화는 문서에서 권하지 않고 기본값으로도 두지 않는다
