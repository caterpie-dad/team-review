# 전체 기능 검증 보고서

검증일: 2026-09-28. 2026년 합성 자료, 팀원 10명, 프로젝트 6개로 구현된 기능을 API·브라우저·Docker에서 검증했다. [전체 실행 기록](../artifacts/full-verification.json)에 각 명령, 종료 코드, 소요 시간과 검증한 소스의 SHA-256을 보관한다. 재현 방법은 [데모 실행 및 전체 검증](demo.md)에 있다.

## 실행 결과

| 검증 | 결과 및 근거 |
| --- | --- |
| 자동 테스트 | 118개 통과. [JUnit](../artifacts/full-suite.xml), [로그](../artifacts/verification-logs/tests.log) |
| 정적·구문 검사 | Ruff, Python compileall, 두 JavaScript 파일, 시작 스크립트 Bash 구문 검사 |
| 팀원 브라우저 | ID 추가·삭제·저장·재열기, 기존 누락/null 데이터, 연차·CL·종합점수, 모바일. [결과](../artifacts/level-verification.json) |
| 초안 브라우저 | 미완성 저장·보완·실행, 두 번째 실행 차단, 취소·재시도, 초안 삭제. [결과](../artifacts/draft-verification.json) |
| 근거 링크·진행 화면 브라우저 | 복수 URL 추가/Enter·삭제·중복 방지·재열기·모바일, 모든 링크 수집, DOM·포커스·스크롤·펼친 경고 유지, 늦은 응답 무시, 완료 전환. [결과](../artifacts/source-polling-verification.json) |
| 사후 편집 브라우저 | 참여자·가중치·의견·기준 편집, 부분/전체 재분석, 보정 유지, 확정 후 편집, 세 서비스 설정과 연결. [결과](../artifacts/editing-verification.json) |
| 분석 효율 브라우저 | 48개 커밋 분석, 통합/원본 근거 열기, 평가 복제와 캐시 표시, 모바일. [결과](../artifacts/analysis-browser-verification.json) |
| Docker 종합 브라우저 | 로그인부터 생성·진행률·조정·근거·감사 이력·CSV·확정/해제·로그아웃, 가중치 슬라이더 및 모바일. [결과](../artifacts/browser-verification.json) |
| Docker 운영 절차 | 빌드·health, 컨테이너 재생성, DB/설정 영속성, 백업 무결성, 새 볼륨 복원, 운영 Compose 기동, 복원 후 세 서비스 연결. [결과](../artifacts/docker-verification.json) |
| 시작 스크립트·문서 | 운영/데모 분기, 설정 자동 생성과 기존 파일 보존, 비밀번호 비노출, 잘못된 모드 거부, 운영 README 분리 및 로컬 링크 검사. [로그](../artifacts/verification-logs/documentation.log) |

모든 브라우저 시나리오에서 JavaScript page error는 0건이다. 자동 테스트에서는 Starlette TestClient가 사용하는 AnyIO 별칭의 DeprecationWarning 1건이 발생했다.

2026-09-28 실행 중인 데모 대시보드에도 변경을 배포했다. 배포 전후 팀원 10명·평가 4건·저장 설정과 평가 상세의 해시가 일치했고, 기존 평가 편집 화면에서 저장된 링크 목록과 추가 버튼을 확인했다. JavaScript 오류는 없었다. [배포 확인 기록](../artifacts/deployed-source-polling-verification.json).

## 기능별 검증 범위

| 기능 | 확인한 동작 | 주요 검증 코드 |
| --- | --- | --- |
| 인증 | 비밀번호, 익명 차단, 쿠키 속성, 세션 만료, 로그아웃, 로그인 제한, CSRF | `tests/test_api.py`, `scripts/browser_check.py` |
| 팀원 관리 | 고유 ID·복수 계정, 중복 귀속 거부, 활성 상태, 과거 스냅샷, Git 이메일 제거, 누락/null 목록 호환 | `tests/test_api.py`, `tests/test_levels.py`, `tests/test_member_compatibility.py`, `scripts/browser_members_check.py` |
| 연차 보정 | 1/8/9/16/17년 경계, CL 계수, 동일 산출물 차등, 연도 전환, null 유지, 기존 점수 보존 | `tests/test_levels.py`, `scripts/browser_members_check.py` |
| 평가 구성 | 프로젝트·참여자·근거·기간·가중치, 클릭 선택·균등 분배·슬라이더, 초안 저장/재열기/복제/삭제, 실행 시 한국어 검증 | `tests/test_drafts.py`, `tests/test_domain.py`, `scripts/browser_drafts_check.py`, `scripts/browser_check.py` |
| 실행 제어 | 한 번에 한 평가, 동시 요청 원자성, 진행률, 다른 시작 버튼 비활성화, 취소·실패 후 재시도, 재시작 복구, 두 번째 프로세스 거부 | `tests/test_api.py`, `scripts/browser_drafts_check.py` |
| 출처·기간 제한 | repo/org 범위 고정, Confluence 루트·버전별 작성자, 기간 밖 자료 제외, URL 위장·외부 페이지네이션·리다이렉트 차단 | `tests/test_domain.py`, `tests/test_connectors.py` |
| 여러 근거 링크 | 두 저장소와 두 문서 루트의 귀속 가능한 근거가 빠짐없이 LLM 요청에 포함됨, 중복 저장소·겹치는 하위 페이지 중복 제거 | `tests/test_sources.py`, `scripts/browser_sources_check.py` |
| 수집 한계 | 미귀속 저자, 병합·바이너리·생성물 처리, 수집 상한, 절단 경고, 버전 불일치와 접근 실패 | `tests/test_connectors.py`, `scripts/verify_demo.py` |
| LLM 계약 | 개인별 근거 제한, 기준 전달, JSON 스키마, 근거 ID·정확한 인용, 점수 범위·NaN·설명·신뢰도 검증, 잘못된 응답 재시도 | `tests/test_domain.py`, `tests/test_connectors.py`, `tests/test_editing_settings.py` |
| 집계·자료 부족 | 기준/프로젝트 가중평균, 참여 분모, 충족률, 자료 없는 차원 보류, CL 보정, 과거 정책 스냅샷 | `tests/test_domain.py`, `tests/test_levels.py`, `scripts/verify_demo.py` |
| 사후 편집 | 프로젝트/참여자 추가·제거, 범위·계정·기준 변경에 따른 선택적 무효화, 비율 변경 재집계, 부분/전체 재분석 시 수동 보정 유지 | `tests/test_editing_settings.py`, `scripts/browser_editing_check.py` |
| 결과 검토 | 점수·의견·한계 편집, 변경 사유, 원점수 복원, revision 충돌, 감사 이력, 최종 확정/해제와 확정 후 편집 | `tests/test_api.py`, `tests/test_editing_settings.py`, 종합/편집 브라우저 |
| 기준·연결 설정 | 평가 내용/비율/CL 계수, 기존 평가 명시적 적용, 서비스 URL·키 저장, 세 연결 테스트, 키 마스킹·유지·삭제, 영속성, 실행 중 변경 차단 | `tests/test_editing_settings.py`, `scripts/browser_editing_check.py` |
| 내보내기 | JSON 원본 근거·판정·감사 이력, CSV 최종 점수, 수식 주입 방어, 서비스 비밀키 미노출 | `tests/test_api.py`, `tests/test_levels.py`, `scripts/verify_demo.py` |
| 분석 효율 | 작성자별 연속 커밋 통합, 최종 파일·순변경·중간 변경, 원본 보존, 안전하지 않은 통합의 diff 복귀, 취소 전파 | `tests/test_analysis.py`, `scripts/benchmark_analysis.py` |
| 판정 캐시 | 동일 근거 재사용, 근거/기준/모델/인증/귀속 변경 무효화, 저장 판정 재검증, 강제 재분석 우회, API 토큰과 문자 수 구분 | `tests/test_analysis.py`, `scripts/browser_analysis_check.py` |
| 배포·복구 | 임시 프로젝트에서 실제 Docker Compose, 볼륨 영속성, SQLite backup API, 캐시 포함 백업, DB·서비스 설정 복원, 운영 모드의 데모 주입 거부 | `scripts/verify_docker.py` |

## 2026년 평가 결과

프로젝트 가중치는 25/20/20/15/10/10, 합계 100이다. 프로젝트별 개인 결과 30건과 근거 59건(커밋 변경분 30건, 문서 변경분 29건)을 생성했다. 미매핑 작성자는 경고로 남기고 개인 점수에서 제외했다. T010의 한 프로젝트는 근거가 없어 판정 보류이며, 종합 충족률 60%를 표시한다.

다음은 HTTP 통합 검증에서 점수를 조정한 직후의 스냅샷이다. 뒤이어 실행하는 브라우저 검증은 별도의 추가 편집을 수행한다. [평가 JSON](../artifacts/demo-evaluation-2026.json), [CSV](../artifacts/demo-evaluation-2026.csv), [통합 결과](../artifacts/demo-verification.json).

| ID | 연차·레벨 | 보정 전 | 종합 | 충족률 |
| --- | --- | ---: | ---: | ---: |
| T001 | 1년·CL2 | 74.89 | 74.89 | 100% |
| T002 | 4년·CL2 | 63.46 | 63.46 | 100% |
| T003 | 8년·CL2 | 75.39 | 75.39 | 100% |
| T004 | 9년·CL3 | 67.64 | 60.87 | 100% |
| T005 | 12년·CL3 | 69.47 | 62.53 | 100% |
| T006 | 16년·CL3 | 61.95 | 55.76 | 100% |
| T007 | 17년·CL4 | 69.98 | 55.99 | 100% |
| T008 | 20년·CL4 | 63.70 | 50.96 | 100% |
| T009 | 6년·CL2 | 67.61 | 67.61 | 100% |
| T010 | 14년·CL3 | 74.20 | 66.78 | 60%·잠정 |

T001의 품질을 76에서 94로 조정했을 때 해당 프로젝트 점수는 74.20→80.50, 종합점수는 72.27→74.89로 반영됐다. LLM 원점수 76과 조정 이유·전후 값은 보존된다.

## 분석 효율 실험

동일 파일을 반복 수정하는 48개 커밋을 원본 diff 방식과 적응형 통합 방식으로 비교했다. 기본 3줄 문맥 diff, 기존 코드, 중간에 추가 후 제거된 테스트를 포함했다. 원본 48건은 보존하고 통합 근거 2건을 추가했다.

| 작성자 수 | 원본→통합 본문 문자 수 | LLM 호출 | 동일 근거 재실행 |
| --- | --- | --- | --- |
| 1명 | 151,351→20,807 | 5→1회 | 0회 |
| 2명 | 151,351→20,807 | 6→2회 | 0회 |

본문 문자 수는 86.3% 감소했다. 이는 토큰 감소율이 아니며, 모의 서버의 실행 시간으로 실제 모델 추론 시간을 추정하지 않는다. 작성자 전환·공동 저자·병합·부모 연결 단절·hash/hunk 오류·rename·파일 한도·snapshot API 실패에서는 원본 diff를 유지함을 자동 테스트로 확인했다. [실험 기록](../artifacts/analysis-benchmark.json).

## 격리와 한계

브라우저/API 검증은 임시 DB, Docker 검증은 난수 프로젝트명·별도 포트·별도 볼륨을 사용한다. 검증 종료 시 해당 Docker 자원만 정리하며 기존 대시보드의 평가와 설정을 수정하지 않는다. 시작 스크립트 검증은 임시 디렉터리와 Docker 명령 대역을 사용하고, 실제 Compose 동작은 별도 Docker 검증으로 확인한다.

Confluence/GitHub/LLM은 합성 HTTP 서비스로 검증했다. **실제 서비스 계정의 권한과 실제 LLM의 의미적 평가 정확도는 이번 검증에 포함되지 않는다.** 모의 LLM 점수는 인사 평가에 사용할 수 없으며, 원본/통합 방식의 실제 판단 동등성이나 프롬프트 주입 저항성을 입증하지 않는다. 대규모 조직 부하와 다중 노드 운영도 검증하지 않았다. 시스템은 SQLite 기반 단일 프로세스·동시 평가 1건으로 설계되어 있다.

기존 단계별 개발 기록은 [이전 검증 기록](verification-history.md)에 보존했다. 현재 기능과 테스트 수는 이 보고서와 전체 실행 기록을 기준으로 확인한다.
