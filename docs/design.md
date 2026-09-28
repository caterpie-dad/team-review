# 설계

## 구성
브라우저(한국어 대시보드) → FastAPI 단일 프로세스 → SQLite(WAL) + 단일 백그라운드 실행기 → Confluence REST v1 / GitHub REST / OpenAI 호환 Chat Completions API. 프런트는 순수 JS/CSS이며 외부 CDN 의존성이 없다. 별도 데모 서버가 동일한 HTTP 계약으로 고정된 합성 자료와 LLM 응답을 제공한다.

## 데이터
SQLite는 members, evaluations, sessions, audit를 저장한다. 평가는 프로젝트 설정 JSON, 실행 스냅샷, 수집 evidence, 결과, warnings, scope, 상태 및 revision을 보존한다. 평가 설정 수정과 결과 조정은 revision을 비교하는 낙관적 잠금을 사용한다. 감사 이력은 별도 테이블에 추가만 한다. running 상태에 대한 SQLite partial unique index로 이중 실행을 원자적으로 차단한다. 모든 쓰기는 트랜잭션을 사용한다. 작업별 네트워크 클라이언트와 DB 연결을 생성한다.

초안용 `DraftEvaluationInput`/`DraftProject`와 실행용 `EvaluationInput`/`Project`를 분리한다. 초안은 미입력 이름·날짜·참여자·근거와 미완성 가중치 합을 보존한다. 실행 시작과 완료 평가 편집에는 엄격한 모델과 근거 범위 검사를 적용한다. 상세 API의 `draft_issues`로 실행 전 보완 항목을 안내하며, 검증 실패 시 worker를 실행하지 않는다. `validation_details`는 오류 위치를 한국어 항목명으로 변환하고 입력값·자격 증명을 응답에서 제외한다.

## 상태 전이
`draft → running → completed → finalized`. running은 failed/cancelled로도 종료된다. failed/cancelled는 다시 시작할 수 있고 이전 실행 결과를 감사 기록에 보존한다. completed/finalized는 사유를 남겨 편집하거나 전체 재분석할 수 있다. finalized → completed는 확정 해제 이유를 요구한다. 실행 중인 평가의 설정은 불변이다. 실행 전후 편집은 이전 버전을 보존한다. 프로세스 재시작 시 남은 running은 failed(interrupted)로 복구한다.

## 수집 경계
사용자 URL의 scheme/origin은 설정된 web URL과 정확히 일치해야 한다. URL에서 repo, org, page ID만 추출하며 네트워크 요청은 설정된 API base에 고정된 상대 경로로 직접 생성한다. 외부 응답에 들어있는 URL이나 페이지네이션 URL을 요청하지 않고 page/start 값을 자체 증가시킨다. 리다이렉트를 따르지 않는다. 조직 목록은 해당 조직 소유 repo만 허용하고 forks/archived를 제외한다. Confluence는 루트에서 child/page BFS만 수행한다. body 내 하이퍼링크나 attachment는 조회하지 않는다. 버전별 수정자와 이전 버전 대비 diff를 근거로 사용한다. 네트워크·일시 HTTP 오류는 최대 3회 요청한다. 수집 요청의 접근 실패는 `ResourceError`로 구분하고 org/repo 목록, 커밋, 문서 버전, 하위 목록 단위로 격리한다. 앞서 확보한 페이지와 다른 경로는 유지한다. `scopes[project_id].collection_issues`에 비밀 값이 제거된 요청 경로·코드·안전하게 분류한 원인·조치를 기록하고 worker가 진행 중에도 저장한다. 임의 응답 본문과 인증 헤더는 반환하지 않는다. 무결성·LLM 판정 오류는 계속 실행 실패로 처리한다. 한도 도달과 제외된 데이터는 warnings로 표시한다.

## LLM 계약
프로젝트/개인별 근거를 제한된 크기의 배치로 분석한다. 모델에는 차원 기준, 근거 ID/본문, 출력 JSON 스키마만 제공하고 비밀/개인 이름은 전달하지 않는다. 근거 인용은 제출된 내용의 substring 검증, ID allowlist 검증을 통과해야 한다. 모든 기준은 정확히 한 번 반환되어야 하며 유한 0~100 점수 또는 null을 허용한다. 점수가 있으면 적어도 하나의 유효 인용을 요구한다. 모델 출력은 실행 가능한 HTML이 아닌 텍스트로 표시한다. 배치 결과는 동일 가중으로 통합하고 각 배치의 개별 근거를 보존한다. 이는 변경 수 가중이 아니며, 배치 경계의 영향은 한계로 표시한다.

## 보안과 운영
설정의 비밀번호는 시작 때 검증한다. 세션 토큰은 난수이며 DB에는 SHA-256만 저장한다. 쿠키 HttpOnly/SameSite=Strict, 운영 HTTPS 환경은 Secure=true. 쓰기 요청은 Origin 또는 별도 CSRF 헤더 검사. 로그인 실패 지연/제한, CSP, frame 차단, CSV 수식 주입 방지. 기본 호스트 노출은 127.0.0.1이다. 연계 API 키는 환경 변수 참조를 지원하고 read-only 설정 파일로 마운트한다. 키를 오류에 출력하지 않는다. UI에 모델/실행 모드와 수집 제한을 공개한다. 네트워크 서비스 URL은 운영자가 신뢰하는 엔드포인트만 설정한다.

## 적응형 근거 통합과 판정 캐시

저장소의 다수 커밋 작성자를 전체 파일 소유자로 추정하지 않는다. 기본 브랜치의 기간 내 커밋을 수집한 후 부모 SHA가 연결되고 단일 팀원에 귀속된 연속 구간만 통합한다. 원본 evidence와 별도로 `github_consolidated` evidence를 만들고 `source_hashes`에 원본 ID/본문 해시, `base_commit`/`head_commit`에 경계를 보존한다. 원본이 모두 일치하고 같은 작성자에게 속할 때에만 LLM 입력에서 원본을 대체한다. 중복 패킷은 동시에 선택하지 않는다.

파일은 커밋 응답의 blob SHA로 해당 repo의 Git blob API에서 읽는다. 응답 SHA·크기·Base64·UTF-8·Git blob SHA-1을 검증하고 패치를 역적용하면서 각 중간 blob 해시 및 hunk 내용·줄 수를 대조한다. API가 제공한 raw/download URL은 추적하지 않는다. 파일 크기·구간당 파일 수·커밋 수를 제한한다. 검증 실패, 이름 변경, 공동 저자, 병합, SHA 단절, 충분한 입력 절감이 없는 경우에는 원래 diff로 돌아간다. 통합에는 순변경·최종 문맥·원본 이력 및 순변경에 남지 않은 중간 추가/삭제 줄을 포함한다. 중간 줄의 반복/세부 순서는 압축됨을 한계에 명시한다.

GitHub의 커밋 조회는 기본 브랜치와 since/until 범위를 지원하며, blob 조회는 특정 파일 객체의 Base64 내용을 반환한다. [공식 커밋 API](https://docs.github.com/en/rest/commits/commits), [공식 blob API](https://docs.github.com/en/rest/git/blobs).

캐시 키는 정규화된 전체 모델 요청(프롬프트·rubric·evidence), 엔드포인트, 인증키 해시, 프로젝트 기간/허용 URL, 팀원 ID/계정 매핑의 SHA-256이다. 원본 비밀키는 저장하지 않는다. SQLite `judgment_cache`는 최대 항목 수를 제한하고 적재된 판정도 현재 근거에 대해 재검증한다. `force` 재분석은 읽기만 우회하며 새 검증 결과로 갱신한다. 캐시와 별도로 평가별 `analysis_policy`, 결과별 `analysis` 사용량을 저장한다. 수동 점수·의견은 기존 사후 보정 경로에서만 병합한다.

## 검증 순서
1. 도메인 및 API 테스트: 가중치·일자·중복 계정·인증·동시 실행·근거 인용·집계·조정/충돌·확정·재시작.
2. 수집기 테스트: 범위 이탈·기간·버전 귀속·페이지네이션·중복·실패 응답.
3. 모의 HTTP 서버를 실제 어댑터로 호출하는 2026년 10명/6개 프로젝트 평가 및 조정/내보내기 검증.
4. 브라우저로 로그인→팀원→초안→평가 실행→결과→조정 흐름 확인, 스크린샷 저장.
5. Docker 이미지/Compose 기동 검증. 실행 환경의 제약은 검증 보고서에 구분 기록.

## API 참조
- GitHub commits: https://docs.github.com/en/rest/commits/commits
- Confluence REST v1: https://developer.atlassian.com/cloud/confluence/rest/v1/intro/
- Confluence version expansion: https://developer.atlassian.com/server/confluence/expansions-in-the-rest-api/

## 연차/ID 변경 설계 (2026-09-27)
`members.career_years`와 `career_reference_year`로 평가 연도의 연차를 계산한다. 평가 시작 시 `level_policy`와 팀원 스냅샷을 고정한다. `aggregate()`는 보정 전 가중평균과 레벨 계수를 반환하고 최종점수를 계산한다. 과거 `level_policy` 없는 평가는 계수 1로 유지한다. 평가 시작 전 미등록/유효 범위 밖 연차를 거부한다. LLM 입력에는 CL이나 연차를 넣지 않는다. DB 스키마 버전 2 마이그레이션은 팀원/과거 스냅샷/감사 JSON의 폐기된 `git_emails` 필드만 제거한다.

## 사후 보정과 설정 저장
`revisions.revise_evaluation`은 프로젝트의 기간/출처, 개인 계정 매핑, 기준의 설명/구간 변경을 비교한다. 보존 가능한 결과는 원점수/보정/인용을 유지하고 나머지를 `analysis_pending`으로 교체한다. `needs_analysis`가 참이면 확정을 차단한다. 단일 worker는 현재 없는 개인-프로젝트 결과만 분석하고 성공한 기존 결과를 유지한다. `force` 실행은 기존 판정을 전부 갱신하며 호환 가능한 수동 보정 필드는 이관한다. 이전 버전 전체는 `configuration_edited.before` 및 `previous_run` 감사 항목에 저장한다.

공통 설정은 기본 TOML 위에 DB 옆 JSON을 덮어 적용한다. 설정 파일은 0600 임시 파일→fsync→원자적 rename으로 교체하며 revision으로 동시 수정을 감지한다. 설정 저장과 실행 시작은 SQLite 쓰기 트랜잭션을 통해 조정되어 실행 중 설정 변경을 방지한다. 토큰은 GET/검증 오류/감사 응답에서 제외한다. 연결 테스트는 redirect 미추적, 15초 timeout, 외부 오류 본문 비노출을 적용한다.

연결 확인 API 참고: [Confluence current user](https://developer.atlassian.com/cloud/confluence/rest/v1/api-group-users/), [GitHub authenticated user](https://docs.github.com/en/rest/users/users#get-the-authenticated-user). 연결 검사는 평가 근거 수집과 별개이며 사용자 확인 응답을 평가에 사용하지 않는다.

일부 수집 실패가 있는 결과에는 `collection_incomplete`를 저장한다. 프로젝트와 종합 집계는 `provisional` 표시를 제공하고 CSV에도 누락 상태를 추가한다. 누락 비율은 알 수 없으므로 임의로 점수를 깎거나 기존 coverage 수치에 섞지 않는다. 빈 저장소로 확인된 응답은 근거가 없는 정상 제외로 구분한다. Confluence 중간 버전 접근 실패 후 첫 정상 버전은 기준 복구에만 사용하여 다른 사람의 누적 변경을 잘못 귀속하지 않는다.

GitHub 상태 해석 참고: [409와 빈/준비 중 저장소](https://docs.github.com/en/rest/guides/using-the-rest-api-to-interact-with-your-git-database), [권한·호출 한도 오류](https://docs.github.com/en/rest/using-the-rest-api/troubleshooting-the-rest-api).
