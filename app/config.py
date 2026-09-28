import os
from .domain import level_policy
from .analysis import options
from urllib.parse import urlparse

try:
    import tomllib
except ImportError:
    import tomli as tomllib


def load_settings(path=None):
    path = path or os.getenv("TEAM_REVIEW_CONFIG", "config/settings.toml")
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    for section in cfg.values():
        if isinstance(section, dict):
            for key, value in list(section.items()):
                if isinstance(value, str) and value.startswith("env:"):
                    section[key] = os.getenv(value[4:], "")
    auth = cfg.setdefault("auth", {})
    if len(auth.get("password", "")) < 12:
        raise ValueError(
            "대시보드 비밀번호는 12자 이상이어야 합니다. 설정 파일 또는 환경 변수를 확인하세요."
        )
    if not 1 <= auth.get("session_hours", 8) <= 168:
        raise ValueError("세션 유효 시간은 1~168시간이어야 합니다.")
    limits = cfg.get("limits", {})
    for key, value in limits.items():
        if type(value) not in (int, float) or not 1 <= value <= 1000000:
            raise ValueError(f"limits.{key}: 양수의 유한한 수집 한도를 지정하세요.")
    if limits.get("llm_batch_chars", 32000) < limits.get("max_chars", 16000):
        raise ValueError("llm_batch_chars는 max_chars 이상이어야 합니다.")
    for name in ("github", "confluence", "llm"):
        for key in ("api_url", "web_url"):
            if key in cfg.get(name, {}):
                u = urlparse(cfg[name][key])
                if (
                    u.scheme not in ("http", "https")
                    or not u.hostname
                    or u.username
                    or u.password
                    or u.query
                    or u.fragment
                ):
                    raise ValueError(
                        f"{name}.{key}: 유효한 http(s) base URL이 필요합니다."
                    )
    level_policy(cfg)
    options(cfg)
    return cfg
