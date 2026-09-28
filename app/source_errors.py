"""Safe, actionable diagnostics for individual source requests."""

import re
from urllib.parse import quote, urlencode, urlsplit, urlunsplit


class SourceError(Exception):
    pass


class ResourceError(SourceError):
    def __init__(self, detail):
        self.detail = detail
        status = f"HTTP {detail['status']}" if detail["status"] else detail["code"]
        super().__init__(
            f"{detail['service']} {status} · GET {detail['url']} · "
            f"{detail['reason']} {detail['hint']}"
        )


def request_error(
    cfg,
    service,
    path,
    params=None,
    *,
    response=None,
    network=None,
    attempts=1,
    invalid_json=False,
):
    status = response.status_code if response is not None else None
    message = ""
    if response is not None:
        try:
            payload = response.json()
            if isinstance(payload, dict) and isinstance(payload.get("message"), str):
                message = payload["message"].lower()
        except ValueError:
            pass
    # Classify known provider messages; never publish arbitrary error bodies or headers.
    if invalid_json:
        code, reason, hint = (
            "invalid_json",
            "API가 JSON 대신 다른 형식으로 응답했습니다.",
            "API base와 프록시·로그인 페이지 설정을 확인하세요.",
        )
    elif network:
        code, reason, hint = (
            network,
            (
                "요청 시간이 초과되었습니다."
                if network == "timeout"
                else "서버에 연결하거나 응답을 수신하지 못했습니다."
            ),
            "주소·네트워크·TLS 인증서와 서비스 상태를 확인한 뒤 전체 다시 분석하세요.",
        )
    elif (
        status == 409
        and service == "github"
        and re.fullmatch(r"repos/[^/]+/[^/]+/commits", path)
        and "repository is empty" in message
    ):
        code, reason, hint = (
            "empty_repository",
            "GitHub가 빈 저장소라고 응답했습니다.",
            "분석할 커밋이 없어 이 저장소를 건너뜁니다. 커밋이 추가되면 전체 다시 분석하세요.",
        )
    elif status in (403, 429) and (
        status == 429
        or "rate limit" in message
        or response.headers.get("X-RateLimit-Remaining") == "0"
    ):
        code, reason, hint = (
            "rate_limited",
            "API 호출 한도에 도달했습니다.",
            "한도가 복구된 뒤 전체 다시 분석하세요.",
        )
    elif status == 401:
        code, reason, hint = (
            "unauthorized",
            "인증 정보가 거부되었습니다.",
            "서비스 연결의 토큰·인증 방식을 확인하세요.",
        )
    elif status == 403:
        code, reason, hint = (
            "forbidden",
            "서버가 이 경로의 접근을 거부했습니다.",
            "읽기 권한·조직 SSO 승인·접근 정책을 확인하세요.",
        )
    elif status == 404:
        code, reason, hint = (
            "not_found",
            "경로가 없거나 현재 인증 정보로 접근할 수 없습니다.",
            "저장소·페이지 주소와 읽기 권한을 확인하세요.",
        )
    elif status == 409:
        code, reason, hint = (
            "conflict",
            "서버가 현재 리소스 상태와 요청의 충돌을 알렸습니다.",
            "빈 저장소로 확인된 응답은 아닙니다. 저장소 준비 상태와 API 경로를 확인하세요.",
        )
    elif status is not None and 300 <= status < 400:
        code, reason, hint = (
            "redirect",
            "API가 다른 주소로 이동하도록 응답했습니다.",
            "허용 범위를 벗어나지 않도록 이동을 따르지 않았습니다. 서비스 API base를 확인하세요.",
        )
    elif status is not None and status >= 500:
        code, reason, hint = (
            "server_error",
            "연계 서버 또는 게이트웨이 오류입니다.",
            "서비스 복구 후 전체 다시 분석하세요.",
        )
    else:
        code, reason, hint = (
            "http_error",
            "API가 요청을 처리하지 못했습니다.",
            "표시된 경로와 상태 코드로 서비스 관리자에게 확인하세요.",
        )
    base = urlsplit(cfg[service]["api_url"])
    host = base.netloc.rsplit("@", 1)[-1]
    url = urlunsplit(
        (base.scheme, host, base.path.rstrip("/") + "/" + path.lstrip("/"), "", "")
    )
    # Only collector-generated, non-secret pagination/version/date parameters are recorded.
    allowed = {
        "page",
        "per_page",
        "limit",
        "start",
        "since",
        "until",
        "version",
        "status",
        "expand",
        "type",
    }
    query = urlencode({k: v for k, v in (params or {}).items() if k in allowed})
    if query:
        url += "?" + query
    for section in cfg.values():
        if not isinstance(section, dict):
            continue
        for key in ("password", "token", "key"):
            value = section.get(key)
            if isinstance(value, str) and value:
                for variant in (value, quote(value, safe="")):
                    url = url.replace(variant, "[redacted]")
    url = re.sub(r"[\x00-\x1f\x7f]", "", url)
    return ResourceError(
        {
            "service": service,
            "method": "GET",
            "url": url,
            "status": status,
            "code": code,
            "reason": reason,
            "hint": hint,
            "attempts": attempts,
        }
    )
