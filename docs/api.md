# API 사용

모든 `/api/*` 경로는 로그인 세션을 요구하며 `/api/login`만 예외입니다. 쓰기 요청에 `X-Review-Request: 1` 헤더를 포함하고 브라우저의 Origin은 서비스의 외부 Origin과 일치해야 합니다. 서버 간 요청은 Origin 없이 헤더 및 쿠키로 인증할 수 있습니다. 비밀번호/토큰을 URL query에 넣지 않습니다.

| Method | 경로 | 동작 |
| --- | --- | --- |
| POST | `/api/login` | `{password}`로 세션 생성 |
| POST | `/api/logout` | 현재 세션 폐기 |
| GET | `/api/meta` | 비밀이 제외된 환경·평가 기준 |
| GET/POST | `/api/members` | 팀원 목록/추가 |
| PUT | `/api/members/{id}` | 팀원 수정/비활성화 |
| GET/POST | `/api/evaluations` | 평가 목록/초안 추가 |
| GET/PUT/DELETE | `/api/evaluations/{id}` | 상세/초안 또는 완료 평가 수정/초안 삭제 |
| POST | `/api/evaluations/{id}/clone` | 설정으로 새 초안 생성 |
| POST | `/api/evaluations/{id}/start` | `{revision, force?, reason?}` 부분/전체 실행 시작 |
| POST | `/api/evaluations/{id}/cancel` | 취소 요청 |
| PATCH | `/api/evaluations/{id}/adjust` | 개별 차원 점수 및 의견 조정 |
| POST | `/api/evaluations/{id}/finalize` | `{revision, reason}` 확정 |
| POST | `/api/evaluations/{id}/reopen` | `{revision, reason}` 확정 해제 |
| GET | `/api/evaluations/{id}/audit` | 실행/조정/확정 이력 |
| GET | `/api/evaluations/{id}/export?format=json` | 근거·결과·감사 이력 전체 |
| GET | `/api/evaluations/{id}/export?format=csv` | 최종 점수 표 |
| POST | `/api/demo/seed` | 빈 데모 DB에 2026년 10명/6개 프로젝트 생성 |
| GET | `/health` | 익명 상태 확인 |

평가 생성/수정 본문:

```json
{
  "title": "2026년 평가", "year": 2026, "revision": 0,
  "projects": [{
    "id": "project-1", "name": "플랫폼", "weight": 100,
    "start": "2026-01-01", "end": "2026-12-31", "member_ids": ["T001"],
    "github_urls": ["https://github.com/org/repo"],
    "confluence_urls": ["https://company.atlassian.net/wiki/spaces/TEAM/pages/123"]
  }]
}
```

초안 POST/PUT은 빈 이름·빈 참여자/근거 목록, 날짜 `null` 또는 빈 문자열, 미완성 프로젝트 가중치 합계를 허용한다. 연도·항목 타입·길이·프로젝트 ID 중복 등 기본 검증은 유지한다. 상세 GET의 `draft_issues`는 실행 전 보완할 항목 목록이다. start는 완성된 설정과 허용된 근거 범위를 검증하며 미완성이면 실행 상태나 데이터를 변경하지 않고 422를 반환한다. 완료/확정 평가 PUT에는 완성된 설정을 요구한다. 검증 오류의 `msg`에는 한국어 항목명과 프로젝트 순서를 포함하고 입력값은 반환하지 않는다.

조정 본문:

```json
{
  "revision": 30, "project_id": "project-1", "member_id": "T001",
  "dimension": "quality", "score": 82,
  "reason": "근거를 다시 검토하여 품질 점수 보정", "comment": "평가자 의견", "reset": false
}
```

`score: null`은 판정 보류, `reset: true`는 원래 LLM 결과로 복원합니다. 변경 시 `revision`은 최신 상세 응답의 값을 사용합니다. 불일치는 409를 반환하며 최신 내용을 읽어 다시 판단해야 합니다. API 오류 응답은 `{ "detail": "설명" }` 또는 입력 검증 오류 배열입니다. 401 인증 필요, 403 CSRF/모드 위반, 404 대상 없음, 409 상태/동시 실행/수정 충돌, 422 입력 오류, 429 로그인 제한입니다.

## 팀원 연차 및 계정 입력
팀원 생성/수정은 `github_ids`, `confluence_ids` 배열과 필수 정수 `career_years`(1~100), `career_reference_year`(2000~2200, 생략하면 현재 연도)를 받습니다. `git_emails`는 허용하지 않습니다. ID 한 항목 안에 쉼표나 공백을 넣으면 거부합니다. 결과의 `aggregate`에는 `raw_score`, `career_years`, `level`, `level_weight`, `level_applied`가 포함됩니다. `score`는 보정 후 종합점수입니다. 기존 평가는 `level_applied=false`이며 원래 점수를 유지합니다.

## 편집 및 설정 API
`PUT /api/evaluations/{id}`에 기존 구성과 `revision`, 완료 평가의 `reason`을 전달한다. `refresh_members`로 현재 팀원 정보를 가져오고, `rubric`/`level_weights`로 이 평가의 기준을 편집하거나 `use_current_rubric`으로 공통 기준을 적용한다. `needs_analysis`와 결과별 `analysis_pending`을 확인한 후 start로 필요한 항목을 분석한다. `force:true`와 사유는 전체 재분석이며 기존의 유효한 수동 보정은 유지한다. 확정된 평가는 편집 후 completed로 돌아간다.

`PATCH /api/evaluations/{id}/review-text`는 `{revision, project_id, member_id, summary, limitations, reason}`으로 의견을 편집한다.

- `GET /api/settings`: 비밀 값 없는 공통 설정과 revision
- `PUT /api/settings/rubric`: `{revision, rubric:{version, dimensions:[{id,name,description,weight}],anchors}, level_weights}`
- `PUT /api/settings/services/{github|confluence|llm}`: `{revision,api_url,web_url?,auth?,username?,secret?,clear_secret?,model?,json_mode?}`
- `POST /api/settings/services/{kind}/test`: 동일한 본문으로 저장 전 연결 확인. `{ok,message,elapsed_ms?}` 응답. `ok:false`는 연결 실패이며 API HTTP 상태 200일 수 있다.

공란 secret은 현재 값을 유지하고 clear_secret은 삭제한다. 서비스 저장은 인증키를 반환하지 않는다. 실행 중 저장은 409, revision 충돌도 409다. 화면 저장은 TOML보다 우선하는 영속 설정 파일을 사용한다.

## 분석 효율 필드
평가에 `analysis_policy`를 보존하며 결과별 `analysis`에는 `strategy`, `original_items`, `sent_items`, `original_chars`, `sent_chars`, `reduction_percent`, `consolidated_groups`, `requests`, `cache_hits`, `request_chars`, `input_tokens`, `output_tokens`, `evidence_ids`를 포함한다. `sent_chars`는 캐시 조회 전 선택된 근거 본문 크기이며 `request_chars`는 실제 요청 메시지의 문자 수다. API가 토큰 사용량을 반환하지 않으면 토큰 필드는 null이다. `requests`에는 재시도도 포함한다. `force:true`는 기존 판정 캐시를 읽지 않고 모든 판정을 다시 요청한다.

원본 evidence를 보존하고 별도의 `metadata.kind=github_consolidated` 근거를 추가한다. 통합 근거의 `source_hashes`는 원본 evidence ID/본문 해시, `base_commit`/`head_commit`은 연속 구간 경계다. 실제 LLM에 선택된 근거 ID는 결과의 `analysis.evidence_ids`로 확인한다.

## 수집 제외와 잠정 결과

상세/JSON 내보내기의 `scopes[project_id].collection_issues`는 수집에서 건너뛴 요청 목록이다. 각 항목은 `service`, `method`, `url`, `status`(네트워크 오류는 null), `code`, `reason`, `hint`, `attempts`, `stage`, `message`를 포함한다. URL에는 인증 정보나 임의 쿼리를 포함하지 않는다. 오류 원문·인증 헤더는 제공하지 않는다.

접근 실패가 있어도 나머지 수집과 판정이 끝나면 상태는 `completed`이다. 영향받은 scope/result 및 집계의 프로젝트에 `collection_incomplete`가 표시되며, 종합 집계의 `provisional`은 자료 부족 또는 일부 수집 실패가 있음을 뜻한다. 빈 저장소만 제외된 경우에는 `collection_incomplete=false`이다. CSV 마지막 두 열은 `provisional`(개인 종합)과 `collection_incomplete`(프로젝트)다. 없는 근거는 null 점수이며 낮은 기여로 환산하지 않는다. `/start`의 `force:true`로 전체 재수집할 수 있다.
