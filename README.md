# Trace · 팀원 기여도 평가

Confluence 문서와 GitHub 코드 산출물을 근거로 팀원의 기여를 평가하는 비밀번호 보호 웹 대시보드입니다. 이 문서는 실제 운영 PC의 설치·설정·실행·백업을 안내합니다.

## 준비 사항

- Docker Engine 또는 Docker Desktop과 Docker Compose v2가 설치된 PC
- Bash 실행 환경. Windows는 WSL2 터미널에서 실행합니다.
- Confluence 및 GitHub 읽기 권한과 OpenAI 호환 Chat Completions API를 제공하는 LLM 서버
- 서버가 각 서비스 API에 접근할 수 있는 네트워크

프로젝트 디렉터리에서 다음 명령으로 Docker를 확인합니다. Python·Node 설치는 운영 실행에 필요하지 않습니다.

```bash
docker version
docker compose version
```

## 최초 설정과 실행

최초 설치 때만 설정 예제를 복사합니다. 기존 설정 파일을 덮어쓰지 마세요.

```bash
cp -n .env.example .env
cp -n config/settings.example.toml config/settings.toml
chmod 600 .env
chmod 644 config/settings.toml
```

`.env`에서 `DASHBOARD_PASSWORD`를 12자 이상의 비밀번호로 바꾸고 GitHub·Confluence·LLM 인증 정보를 입력합니다. `config/settings.toml`에서 `app.demo = false`, 서비스 주소·인증 방식·LLM 모델을 확인합니다. TOML의 비밀 값은 `env:변수명` 참조로 유지하세요.

```bash
./start.sh production
```

이미지 빌드와 컨테이너 기동, 상태 확인이 끝나면 `http://localhost:8080`에서 `.env`에 설정한 비밀번호로 로그인합니다. 포트는 `.env`의 `PORT`, 수신 주소는 `BIND_HOST`로 정하며 기본값은 `8080`과 `127.0.0.1`입니다. 다른 PC에서 접속할 때는 운영 PC의 주소와 네트워크 설정을 사용합니다.

설정 파일이 없으면 실행 스크립트가 예제를 만들고 중단합니다. 생성된 파일을 편집한 뒤 같은 명령으로 다시 실행하세요. `.env`가 없으면 임의 비밀번호를 생성해 파일에 보관하며 콘솔에는 출력하지 않습니다.

## 환경별 설정

| 설정 | 내용 |
| --- | --- |
| `auth.password` | 직접 지정 또는 `env:DASHBOARD_PASSWORD` |
| `auth.secure_cookie` | HTTPS 운영에서 `true` |
| `auth.public_url` | 역방향 프록시 사용 시 외부 Origin, 예: `https://review.company.example` |
| `github.web_url`, `github.api_url` | GitHub 또는 GitHub Enterprise의 웹/API base. Enterprise 예: `https://git.company.example`, `https://git.company.example/api/v3` |
| `github.token` | 저장소 읽기 권한 토큰 |
| `confluence.web_url`, `confluence.api_url` | Cloud 예: `https://company.atlassian.net/wiki`, `https://company.atlassian.net/wiki/rest/api`. Data Center는 설치 context path 포함 |
| `confluence.auth` | Cloud `basic`(이메일+API 토큰), Data Center `bearer`(PAT) |
| `confluence.username`, `confluence.token` | 사용자 및 읽기 권한 토큰 |
| `llm.api_url`, `llm.key`, `llm.model` | OpenAI 호환 `/chat/completions` API를 제공하는 서버 또는 게이트웨이. base에 `/chat/completions`를 중복 입력하지 않음 |
| `llm.json_mode` | JSON response_format 지원 여부 |
| `limits.*` | 요청 제한 시간, 수집 페이지·버전·커밋·저장소 수, 본문/LLM 배치 문자 한도 |
| `analysis.*` | GitHub 근거 통합 전략·연속 커밋/파일 한도·최소 절감률·LLM 판정 캐시 |

환경 변수 참조 형식은 `env:변수명`입니다. 운영 설정 파일과 `.env`는 Git 및 Docker 이미지에서 제외됩니다. 운영 URL과 읽기 권한을 가진 자격 증명이 필요합니다.

컨테이너는 UID 10001로 실행되므로 마운트한 TOML에 읽기 권한이 필요합니다. 기본 TOML에는 URL과 환경 변수 참조만 두고, 비밀번호와 토큰은 권한 0600인 `.env`에서 관리하세요. 토큰을 TOML에 직접 넣는 경우에는 컨테이너 사용자만 읽을 수 있도록 파일 소유권/ACL을 별도로 설정해야 합니다.

TLS는 역방향 프록시에서 종료할 수 있습니다. 이때 `auth.public_url`과 `secure_cookie=true`를 지정하고, `.env`의 `BIND_HOST`는 배포 네트워크에 맞게 설정합니다. 외부 노출 주소와 API base는 운영자가 신뢰하는 서버로 지정하세요. 수집된 산출물은 설정한 LLM 서버로 전달됩니다.

## 대시보드 설정과 우선순위

로그인 후 **서비스 연결**에서 세 서비스의 주소와 인증 정보를 입력하고 각각 **연결 테스트**를 실행합니다. GitHub/Confluence는 사용자 인증, LLM은 모델 응답을 확인합니다. 개별 저장소·문서 권한은 실제 수집 때 확인하며 LLM 연결 테스트에는 소량의 API 사용량이 발생할 수 있습니다.

대시보드에서 저장한 값은 TOML/환경 변수보다 우선하며 `/data/review-settings.json`에 보관됩니다. 비어 있는 토큰 입력란으로 저장하면 기존 값을 유지하고, **저장된 인증키 삭제**로 제거할 수 있습니다. 화면에 저장된 설정을 바꾸려면 해당 화면에서 변경하세요. `analysis.*` 등 TOML에서 관리하는 설정과 `.env` 변경은 `./start.sh production` 재실행으로 반영합니다.

팀원 계정·연차를 등록한 뒤 프로젝트 기간, 참여자, 근거 루트, 가중치를 지정해 평가합니다. 한 번에 한 평가만 실행됩니다. 상세 조작, 사후 편집, CL 보정과 분석 전략은 [대시보드 사용 안내](docs/user-guide.md)를 참고하세요.

## 상태 확인·업데이트·종료

```bash
docker compose -p trace-production -f compose.yaml ps
docker compose -p trace-production -f compose.yaml logs --tail 100 dashboard
curl --fail http://localhost:8080/health

# 소스·환경 설정 변경 반영. 실행 중인 평가는 먼저 완료하거나 취소합니다.
./start.sh production

# 중지: 저장 데이터는 유지합니다.
docker compose -p trace-production -f compose.yaml down
```

데이터는 `trace-production_review-data` 볼륨의 `/data`에 남습니다. 단일 컨테이너·단일 프로세스로 운영하며 `--workers 1`을 유지하세요. 서버 중단 당시 실행 중이던 평가는 재시작 후 실패 상태로 표시되어 다시 시도할 수 있습니다. 배포 후 브라우저를 새로고침하면 최신 화면을 받습니다.

## 백업

평가 실행과 설정 편집을 마친 뒤 아래 명령을 실행합니다. 예시의 백업 이름은 실행할 때마다 새 이름으로 바꾸세요. DB에는 근거·결과·감사 이력·판정 캐시가 포함됩니다. 화면에서 저장한 서비스/기준 설정 파일과 호스트의 `.env`, `config/settings.toml`도 함께 보관해야 합니다.

```bash
umask 077
mkdir -p backups/backup-2026-09-27

docker compose -p trace-production -f compose.yaml exec dashboard \
  python scripts/backup.py /data/backup-2026-09-27.sqlite3
docker compose -p trace-production -f compose.yaml cp \
  dashboard:/data/backup-2026-09-27.sqlite3 backups/backup-2026-09-27/review.sqlite3

# 화면 설정을 저장한 적이 있는 경우에만 파일이 존재합니다.
docker compose -p trace-production -f compose.yaml exec dashboard \
  test -f /data/review-settings.json
docker compose -p trace-production -f compose.yaml cp \
  dashboard:/data/review-settings.json backups/backup-2026-09-27/review-settings.json

cp .env backups/backup-2026-09-27/.env
cp config/settings.toml backups/backup-2026-09-27/settings.toml
chmod 600 backups/backup-2026-09-27/* backups/backup-2026-09-27/.env
```

`test -f`가 실패하면 화면 설정 파일이 없는 상태이므로 해당 `cp`만 생략합니다. DB 백업은 SQLite backup API를 사용하므로 WAL에 남은 변경도 포함합니다. 실행 중인 DB 파일 하나만 직접 복사하지 마세요. 백업 폴더에는 인증 정보가 포함되므로 접근 권한을 제한합니다.

## 복원

서버를 중지하고 현재 볼륨을 별도 보관한 뒤 복원합니다. 호스트 설정도 같은 백업 시점의 파일로 복원해야 합니다. 아래의 백업 경로를 실제 경로로 바꾸세요. 새 PC라면 먼저 위 설치 준비를 완료합니다.

```bash
docker compose -p trace-production -f compose.yaml down
cp backups/backup-2026-09-27/.env .env
cp backups/backup-2026-09-27/settings.toml config/settings.toml
chmod 600 .env
chmod 644 config/settings.toml

# 설정만 읽고 기동 전 이미지를 준비합니다.
docker compose -p trace-production -f compose.yaml build dashboard
docker volume create trace-production_review-data

# 현재 DB/WAL/설정을 함께 분리한 뒤 백업을 설치합니다.
docker run --rm --user 0 --entrypoint sh \
  -v trace-production_review-data:/data \
  -v "$PWD/backups/backup-2026-09-27:/restore:ro" \
  trace-team-review:local -c '
    set -eu
    archive_dir="/data/before-restore-$(date +%s)"
    mkdir "$archive_dir"
    for name in review.sqlite3 review.sqlite3-wal review.sqlite3-shm review-settings.json; do
      if [ -f "/data/$name" ]; then mv "/data/$name" "$archive_dir/"; fi
    done
    cp /restore/review.sqlite3 /data/review.sqlite3
    if [ -f /restore/review-settings.json ]; then cp /restore/review-settings.json /data/; fi
    chown -R 10001:10001 /data
    chmod 600 /data/review.sqlite3
    if [ -f /data/review-settings.json ]; then chmod 600 /data/review-settings.json; fi
  '
./start.sh production
```

복원 후 로그인, 평가 목록, 서비스 연결 설정을 확인합니다. `down -v`는 볼륨을 삭제하므로 운영 종료에 사용하지 않습니다.

## 문제 해결

| 증상 | 확인할 내용 |
| --- | --- |
| 접속할 수 없음 | `ps`의 healthy 상태, 포트 충돌, `PORT`/`BIND_HOST`, PC 방화벽과 프록시 경로 |
| TOML 권한 오류 | 컨테이너 UID 10001이 `config/settings.toml`을 읽을 수 있는지 확인 |
| 로그인/인증 실패 | `.env`의 비밀번호 변경 후 재기동 여부, HTTPS와 `secure_cookie` 설정 |
| 설정 파일을 바꿔도 연결이 그대로임 | 서비스 연결 화면에 저장된 값이 파일보다 우선하는지 확인 |
| API 연결 실패 | URL의 API base, 인증 방식, 읽기 권한, 인증서와 네트워크 확인 |
| 수집 제외·잠정 결과 | 결과 상단에서 실패 경로·코드·원인 확인. 접근 설정을 고친 뒤 전체 다시 분석. 기존 실행 실패는 다시 시도 |
| 팀원 탭 등 화면 오류 | 브라우저 강력 새로고침 후 로그 확인 |
| 평가 시작 불가 | 초안의 보완 항목, 연차, 가중치 합 100, 이미 실행 중인 평가 확인 |

[문서 목록](docs/index.md) · [API 안내](docs/api.md)
