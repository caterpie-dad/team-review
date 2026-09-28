# 데모 실행 및 전체 검증

[운영 설치](../README.md) · [문서 목록](index.md)

데모는 합성 Confluence/GitHub 자료와 고정 응답을 반환하는 모의 LLM HTTP 서버를 사용합니다. 실제 외부 계정이나 유료 LLM 키가 필요하지 않습니다. 기능·귀속·인용·집계·입출력을 검증하며 실제 모델의 판단 정확도를 입증하지 않습니다.

## Docker 데모 실행

Docker와 Docker Compose v2, Bash 환경에서 프로젝트 루트를 작업 디렉터리로 사용합니다.

```bash
./start.sh demo
```

`http://localhost:8080`에 접속해 `.env`의 `DASHBOARD_PASSWORD`로 로그인합니다. 파일이 없으면 스크립트가 난수 비밀번호를 생성합니다. 로그인 후 **2026년 데모 불러오기**를 누르면 팀원 10명과 프로젝트 6개가 생성됩니다. **평가 시작**으로 수집·분석·결과 검토를 진행합니다. 샘플 생성은 빈 DB에서만 가능하며 다시 체험할 때는 평가 복제 또는 기존 평가 편집을 사용합니다.

데모 프로젝트는 `trace-demo`, 데이터 볼륨은 `trace-demo_demo-data`입니다. 운영 프로젝트와 볼륨은 분리되어 있습니다. 동시 실행 시 포트를 다르게 지정합니다.

```bash
PORT=8082 ./start.sh demo
docker compose -p trace-demo -f compose.demo.yaml ps
docker compose -p trace-demo -f compose.demo.yaml logs --tail 100 dashboard
docker compose -p trace-demo -f compose.demo.yaml down
```

포트를 바꿨다면 상태·중지 명령에도 같은 `PORT`를 사용하거나 `.env`에 저장하세요. `down`은 데이터를 유지합니다. 기존 워크스페이스에 대한 `down -v`는 사용하지 마세요.

## 포함된 자료

- 2026년, 팀원 10명, 프로젝트 6개, 프로젝트별 개인 평가 30건
- 프로젝트 가중치 25/20/20/15/10/10, 합계 100
- 기본 시나리오 근거 59건: 커밋 변경분 30건과 문서 변경분 29건
- 복수 계정, 미매핑 저자, 기간 밖 자료, 자료 없는 참여자, 범위 밖 링크, 본문 내 악성 지시
- 1~20년차 팀원으로 CL2/CL3/CL4와 종합점수 보정 확인
- 별도 `demo/efficiency` 저장소의 연속 커밋 48개로 통합 분석·판정 캐시 확인

## Python 검증 환경

Python 3.10 이상을 사용합니다. 브라우저 검증에는 Playwright Chromium과 시스템 라이브러리가 필요합니다.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/playwright install --with-deps chromium
```

전체 재검증:

```bash
.venv/bin/python scripts/verify_all.py
```

이 명령은 자동 테스트·정적/구문 검사·브라우저 시나리오·효율 비교·Docker 배포/영속성/백업/복원을 실행합니다. Docker 검증은 별도 임시 프로젝트·포트·볼륨을 사용하고 종료 시 그 자원만 정리합니다. 기존 `.env` 및 실행 중인 대시보드 DB는 수정하지 않습니다. 결과와 실행 로그는 `artifacts/`에 저장합니다.

브라우저 시스템 라이브러리를 별도 경로에 설치한 환경은 해당 경로를 `LD_LIBRARY_PATH`로 지정한 상태에서 실행합니다. Docker는 권한이 있는 사용자로 실행해야 합니다.

## 개별 검증

| 명령 | 범위 |
| --- | --- |
| `.venv/bin/pytest -q` | 인증·도메인·API·수집·LLM 계약·오류·동시 실행·재시도·편집·캐시 |
| `.venv/bin/ruff check app tests scripts` | Python 정적 검사 |
| `.venv/bin/python scripts/browser_members_check.py` | 계정 칩·기존 데이터 호환·연차·모바일 |
| `.venv/bin/python scripts/browser_drafts_check.py` | 미완성 초안 저장·보완·삭제·실행 차단·취소·재시도 |
| `.venv/bin/python scripts/browser_sources_check.py` | 여러 근거 URL 추가·삭제·저장·수집, 진행 화면 유지, 늦은 응답의 화면 덮어쓰기 방지 |
| `.venv/bin/python scripts/browser_editing_check.py` | 사후 편집·기준·연결 설정·재분석 |
| `.venv/bin/python scripts/browser_analysis_check.py` | 통합 근거·원본 열기·캐시·모바일 |
| `.venv/bin/python scripts/benchmark_analysis.py` | 원본 diff/통합 분석의 문자 수와 호출 비교 |
| `.venv/bin/python scripts/verify_docker.py` | 격리 Docker 평가·종합 브라우저·재시작·백업·복원·운영 모드 |
| `.venv/bin/python scripts/verify_docs.py` | 운영/데모 문서 분리·링크·시작 스크립트 설정 생성과 실행 분기 |

위 브라우저 전용 스크립트들은 임시 DB와 별도 모의 서버를 사용합니다. 다음 두 명령은 지정한 **기존 데모 DB를 수정**하므로 체험용 환경에만 사용합니다. 전체 검증 실행기는 이들을 격리 Docker 환경에 대해 실행합니다.

```bash
.venv/bin/python scripts/verify_demo.py --url http://127.0.0.1:8080
REVIEW_URL=http://127.0.0.1:8080 .venv/bin/python scripts/browser_check.py
```

`verify_demo.py`를 먼저 실행해 10명/6개 프로젝트 평가를 준비합니다. 두 스크립트는 `DASHBOARD_PASSWORD` 환경 변수 또는 `.env`를 사용합니다. `browser_check.py`는 로그인·생성·실행·점수 조정·근거·이력·다운로드·확정/해제·로그아웃을 확인합니다.

## Docker 없이 로컬 체험

```bash
.venv/bin/python scripts/dev.py
```

포트 8080과 9001이 비어 있어야 합니다. 데이터는 `data/local-demo.sqlite3`에 저장합니다. 로컬 체험 전용 기본 비밀번호는 `local-demo-2026!`이며 `DASHBOARD_PASSWORD`로 바꿀 수 있습니다. 운영 배포에는 이 명령을 사용하지 않습니다.

## 결과 읽기

[검증 보고서](verification.md)에 실행 결과·기능별 검증 근거·한계를 기록합니다. JSON/CSV, 화면 캡처, JUnit 결과, 실행 로그 및 전체 검증 요약을 `artifacts/`에서 확인할 수 있습니다. 성능 실험의 문자 수는 토큰 수가 아니며 모의 응답 시간은 실제 모델 추론 시간을 나타내지 않습니다.
