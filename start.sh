#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
mode="${1:-demo}"
if [[ "$mode" != "demo" && "$mode" != "production" ]]; then
  echo 'Usage: ./start.sh [demo|production]' >&2
  exit 1
fi
command -v docker >/dev/null || { echo 'Docker와 Docker Compose를 먼저 설치하세요.' >&2; exit 1; }
docker compose version >/dev/null
if [[ ! -f .env ]]; then
  umask 077
  generated_password="$(od -An -N24 -tx1 /dev/urandom | tr -d ' \n')"
  printf 'DASHBOARD_PASSWORD=%s\nPORT=8080\nBIND_HOST=127.0.0.1\n' "$generated_password" > .env
  echo '.env에 무작위 초기 비밀번호를 저장했습니다. 접속 전에 .env 파일에서 확인하세요.'
fi
chmod 600 .env
if [[ "$mode" == "production" ]]; then
  if [[ ! -f config/settings.toml ]]; then
    cp config/settings.example.toml config/settings.toml
    # This template contains URLs and env references only. Secrets stay in .env (0600).
    chmod 644 config/settings.toml
    echo 'config/settings.toml을 만들었습니다. 서비스 URL/모델을 설정하고 .env에 토큰을 입력한 뒤 다시 실행하세요.'
    exit 1
  fi
  docker compose -p trace-production -f compose.yaml up -d --build --wait
else
  docker compose -p trace-demo -f compose.demo.yaml up -d --build --wait
fi
echo 'Trace 준비 완료. 기본 주소: http://localhost:8080 (.env의 PORT로 변경 가능)'
if [[ "$mode" == "demo" ]]; then
  echo '데모: 로그인 후 “2026년 데모 불러오기”를 누르면 10명 / 6개 프로젝트가 생성됩니다.'
fi
