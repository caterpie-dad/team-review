"""Dashboard overrides persisted outside the image; credential fields are excluded from public responses."""

import copy
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import httpx
from pydantic import Field, SecretStr, model_validator
from .domain import StrictModel, RUBRIC, level_policy, RubricInput


class RubricSettings(StrictModel):
    revision: int
    rubric: RubricInput
    level_weights: dict[str, float]

    @model_validator(mode="after")
    def valid(self):
        level_policy({"evaluation": {"level_weights": self.level_weights}})
        return self


class ServiceInput(StrictModel):
    revision: int
    api_url: str = Field(min_length=1, max_length=2000)
    web_url: str = Field(default="", max_length=2000)
    auth: Literal["bearer", "basic"] = "bearer"
    username: str = Field(default="", max_length=300)
    secret: SecretStr | None = None
    clear_secret: bool = False
    model: str = Field(default="", max_length=200)
    json_mode: bool = True

    @model_validator(mode="after")
    def valid(self):
        for value in (self.api_url, self.web_url):
            if not value:
                continue
            u = urlparse(value)
            if (
                u.scheme not in ("http", "https")
                or not u.hostname
                or u.username
                or u.password
                or u.query
                or u.fragment
            ):
                raise ValueError(
                    "서비스 주소는 사용자정보·query·fragment 없는 http(s) URL이어야 합니다."
                )
        if self.secret and len(self.secret.get_secret_value()) > 8000:
            raise ValueError("토큰이 너무 깁니다.")
        return self


class SettingsConflict(Exception):
    pass


class SettingsStore:
    def __init__(self, cfg, path):
        self.base = copy.deepcopy(cfg)
        self.path = Path(path)
        self.lock = threading.RLock()
        self.data = (
            json.loads(self.path.read_text()) if self.path.exists() else {"revision": 0}
        )

    def effective(self):
        with self.lock:
            cfg = copy.deepcopy(self.base)
            for key in ("github", "confluence", "llm"):
                cfg[key].update(self.data.get(key, {}))
            cfg["rubric"] = copy.deepcopy(self.data.get("rubric", RUBRIC))
            cfg.setdefault("evaluation", {})["level_weights"] = copy.deepcopy(
                self.data.get("level_weights", level_policy(self.base)["weights"])
            )
            return cfg

    def public(self):
        with self.lock:
            cfg = self.effective()
            services = {}
            for kind in ("github", "confluence", "llm"):
                service = cfg[kind]
                services[kind] = {
                    k: service.get(k, "")
                    for k in ("api_url", "web_url", "username", "model")
                }
                services[kind].update(
                    auth=service.get("auth", "bearer"),
                    json_mode=service.get("json_mode", True),
                    secret_set=bool(service.get("key" if kind == "llm" else "token")),
                )
            return {
                "revision": self.data["revision"],
                "rubric": cfg["rubric"],
                "level_policy": level_policy(cfg),
                "services": services,
            }

    def check_revision(self, revision):
        if revision != self.data["revision"]:
            raise SettingsConflict(
                "다른 작업으로 설정이 변경되었습니다. 새로고침 후 다시 시도하세요."
            )

    def save(self, revision, updates):
        with self.lock:
            self.check_revision(revision)
            data = {**self.data, **copy.deepcopy(updates), "revision": revision + 1}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, temp = tempfile.mkstemp(dir=self.path.parent, prefix=".settings-")
            try:
                with os.fdopen(fd, "w") as file:
                    json.dump(data, file, ensure_ascii=False, indent=2)
                    file.flush()
                    os.fsync(file.fileno())
                os.replace(temp, self.path)
                self.data = data
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)
            return self.public()

    def service_candidate(self, kind, body):
        with self.lock:
            self.check_revision(body.revision)
            current = self.effective()[kind]
            result = {**current, "api_url": body.api_url.rstrip("/")}
            if kind == "llm":
                if not body.model:
                    raise ValueError("LLM 모델을 입력하세요.")
                result.update(model=body.model, json_mode=body.json_mode)
            else:
                if not body.web_url:
                    raise ValueError("웹 URL을 입력하세요.")
                result.update(
                    web_url=body.web_url.rstrip("/"),
                    auth=body.auth,
                    username=body.username,
                )
            secret_key = "key" if kind == "llm" else "token"
            if body.clear_secret:
                result[secret_key] = ""
            elif body.secret and body.secret.get_secret_value():
                result[secret_key] = body.secret.get_secret_value()
            return result


def test_connection(kind, config):
    started = time.monotonic()
    headers = {"Accept": "application/json"}
    auth = None
    secret = config.get("key" if kind == "llm" else "token", "")
    if secret:
        if kind != "llm" and config.get("auth") == "basic":
            auth = (config.get("username", ""), secret)
        else:
            headers["Authorization"] = "Bearer " + secret
    try:
        with httpx.Client(timeout=15, follow_redirects=False) as client:
            base = config["api_url"].rstrip("/")
            if kind == "llm":
                response = client.post(
                    base + "/chat/completions",
                    headers=headers,
                    json={
                        "model": config["model"],
                        "messages": [
                            {
                                "role": "user",
                                "content": "Connection test. Reply with OK only.",
                            }
                        ],
                        "max_tokens": 16,
                    },
                )
            else:
                response = client.get(
                    base + ("/user" if kind == "github" else "/user/current"),
                    headers=headers,
                    auth=auth,
                )
            if response.status_code != 200:
                return {
                    "ok": False,
                    "message": f"HTTP {response.status_code}: 주소·인증·권한을 확인하세요. 리다이렉트는 허용하지 않습니다.",
                }
            data = response.json()
            if kind == "github":
                valid = isinstance(data, dict) and bool(data.get("login"))
            elif kind == "confluence":
                valid = (
                    isinstance(data, dict)
                    and data.get("type") != "anonymous"
                    and bool(
                        data.get("accountId")
                        or data.get("username")
                        or data.get("userKey")
                    )
                )
            else:
                valid = (
                    isinstance(data, dict)
                    and bool(data.get("choices"))
                    and bool(data["choices"][0].get("message", {}).get("content"))
                )
            if not valid:
                return {
                    "ok": False,
                    "message": "API 응답 형식 또는 인증된 사용자 확인에 실패했습니다.",
                }
    except (
        httpx.HTTPError,
        ValueError,
        KeyError,
        IndexError,
        TypeError,
        AttributeError,
    ):
        return {
            "ok": False,
            "message": "연결 실패: 네트워크·인증서·API 주소와 응답 형식을 확인하세요.",
        }
    return {
        "ok": True,
        "message": "연결 성공 · 모델 응답 확인"
        if kind == "llm"
        else "연결 성공 · 사용자 인증 확인 (개별 근거 접근 권한은 평가 시 확인)",
        "elapsed_ms": round((time.monotonic() - started) * 1000),
    }
