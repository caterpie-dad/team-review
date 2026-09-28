import copy
import csv
import hashlib
import hmac
import io
import json
import secrets
import sqlite3
import time
import uuid
import fcntl
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from pydantic import Field, ConfigDict, ValidationError
from .config import load_settings
from .db import Database, now
from .domain import (
    Member,
    EvaluationInput,
    DraftEvaluationInput,
    StrictModel,
    DIMS,
    aggregate,
    career_at,
    level_policy,
    parse_source,
)
from .worker import Worker
from .settings import (
    SettingsStore,
    SettingsConflict,
    ServiceInput,
    RubricSettings,
    test_connection,
)
from .revisions import revise_evaluation, CONFIG_FIELDS
from .validation import validation_details
from .analysis import options as analysis_options


class Login(StrictModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    password: str = Field(max_length=500)


class Revision(StrictModel):
    revision: int


class StartInput(Revision):
    force: bool = False
    reason: str = Field(default="", max_length=3000)


class Adjustment(Revision):
    member_id: str
    project_id: str
    dimension: str
    score: float | None = Field(ge=0, le=100, allow_inf_nan=False)
    reason: str = Field(min_length=3, max_length=3000)
    comment: str = Field(default="", max_length=6000)
    reset: bool = False


class ReviewText(Revision):
    member_id: str
    project_id: str
    summary: str = Field(max_length=10000)
    limitations: str = Field(max_length=10000)
    reason: str = Field(min_length=3, max_length=3000)


class Decision(Revision):
    reason: str = Field(default="", max_length=3000)


def create_app(cfg=None, db_path=None):
    cfg = cfg or load_settings()
    path = db_path or cfg.get("storage", {}).get("path", "data/review.sqlite3")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    process_lock = open(str(path) + ".lock", "a")
    try:
        fcntl.flock(process_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        process_lock.close()
        raise RuntimeError(
            "동일 데이터베이스를 사용하는 서버가 이미 실행 중입니다. 단일 프로세스로 실행하세요."
        ) from None
    db = Database(path)
    runtime = SettingsStore(
        cfg, Path(path).parent / (Path(path).stem + "-settings.json")
    )
    worker = Worker(db, runtime.effective)

    @asynccontextmanager
    async def lifespan(app):
        yield
        # Lock remains held until worker threads have stopped; shutdown marks them cancelled.
        for eid in list(worker.threads):
            with db.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                ev = db.get(eid, conn)
                if ev and ev["status"] == "running":
                    ev["cancel_requested"] = True
                    db.save(conn, ev)
        for thread in list(worker.threads.values()):
            limits = cfg.get("limits", {})
            thread.join(
                timeout=max(
                    limits.get("llm_timeout", 120), limits.get("request_timeout", 30)
                )
                + 10
            )
        process_lock.close()

    app = FastAPI(
        title="Trace · 팀원 평가",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.settings = runtime
    app.state.process_lock = process_lock
    app.state.db, app.state.worker, app.state.cfg = db, worker, cfg
    static = Path(__file__).parent / "static"
    asset_version = hashlib.sha256(
        b"".join(
            (static / name).read_bytes()
            for name in ("app.js", "settings.js", "style.css")
        )
    ).hexdigest()[:16]
    index_html = (
        (static / "index.html").read_text().replace("__ASSET_VERSION__", asset_version)
    )

    @app.middleware("http")
    async def security(request, call_next):
        path = request.url.path
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            expected = cfg["auth"].get("public_url", str(request.base_url)).rstrip("/")
            if (origin and origin != expected) or request.headers.get(
                "x-review-request"
            ) != "1":
                return JSONResponse(
                    {"detail": "동일 출처 요청만 허용합니다."}, status_code=403
                )
        if path.startswith("/api/") and path != "/api/login":
            token = hashlib.sha256(
                request.cookies.get("review_session", "").encode()
            ).hexdigest()
            with db.connect() as conn:
                row = conn.execute(
                    "SELECT expires FROM sessions WHERE token=?", (token,)
                ).fetchone()
            if not row or row["expires"] <= time.time():
                return JSONResponse({"detail": "로그인이 필요합니다."}, status_code=401)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        )
        if path == "/" or path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        elif path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        # Never echo credential input in validation errors.
        return JSONResponse(
            status_code=422,
            content={"detail": validation_details(exc.errors())},
        )

    @app.exception_handler(SettingsConflict)
    async def settings_conflict(request, exc):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    def settings_writable(conn):
        if conn.execute("SELECT 1 FROM evaluations WHERE status='running'").fetchone():
            raise HTTPException(
                409,
                "평가 실행 중에는 공통 설정을 저장할 수 없습니다. 완료 후 저장하세요.",
            )

    @app.get("/api/settings")
    def settings():
        return runtime.public()

    @app.put("/api/settings/rubric")
    def save_rubric(body: RubricSettings):
        with db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            settings_writable(conn)
            before = runtime.public()
            rubric = body.rubric.model_dump()
            rubric["version"] = f"custom-{body.revision + 1}"
            result = runtime.save(
                body.revision, {"rubric": rubric, "level_weights": body.level_weights}
            )
            db.audit(
                conn,
                None,
                "rubric_updated",
                {
                    "before": before["rubric"],
                    "after": rubric,
                    "level_weights": body.level_weights,
                },
            )
        return result

    def service_body(kind, body):
        if kind not in ("github", "confluence", "llm"):
            raise HTTPException(404, "지원하지 않는 서비스입니다.")
        try:
            return runtime.service_candidate(kind, body)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.put("/api/settings/services/{kind}")
    def save_service(kind: str, body: ServiceInput):
        with db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            settings_writable(conn)
            candidate = service_body(kind, body)
            result = runtime.save(body.revision, {kind: candidate})
            db.audit(
                conn,
                None,
                "service_updated",
                {"service": kind, "revision": result["revision"]},
            )
        return result

    @app.post("/api/settings/services/{kind}/test")
    def check_service(kind: str, body: ServiceInput):
        return test_connection(kind, service_body(kind, body))

    @app.get("/health")
    def health():
        with db.connect() as conn:
            conn.execute("SELECT 1")
        return {"status": "ok"}

    @app.post("/api/login")
    def login(body: Login, request: Request):
        ip = request.client.host if request.client else "unknown"
        stamp = time.time()
        with db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM login_attempts WHERE ip=?", (ip,)
            ).fetchone()
            if row and row["until"] > stamp and row["count"] >= 10:
                raise HTTPException(
                    429, "로그인 시도 제한입니다. 15분 뒤 다시 시도하세요."
                )
            valid = hmac.compare_digest(
                hashlib.sha256(body.password.encode()).digest(),
                hashlib.sha256(cfg["auth"]["password"].encode()).digest(),
            )
            if not valid:
                count = row["count"] + 1 if row and row["until"] > stamp else 1
                conn.execute(
                    "INSERT OR REPLACE INTO login_attempts VALUES(?,?,?)",
                    (
                        ip,
                        count,
                        row["until"] if row and row["until"] > stamp else stamp + 900,
                    ),
                )
            else:
                conn.execute("DELETE FROM login_attempts WHERE ip=?", (ip,))
                token = secrets.token_urlsafe(32)
                age = int(cfg["auth"].get("session_hours", 8) * 3600)
                conn.execute("DELETE FROM sessions WHERE expires<?", (stamp,))
                conn.execute(
                    "INSERT INTO sessions VALUES(?,?)",
                    (hashlib.sha256(token.encode()).hexdigest(), stamp + age),
                )
        if not valid:
            raise HTTPException(401, "비밀번호가 올바르지 않습니다.")
        response = JSONResponse({"ok": True})
        response.set_cookie(
            "review_session",
            token,
            httponly=True,
            secure=cfg["auth"].get("secure_cookie", False),
            samesite="strict",
            max_age=age,
        )
        return response

    @app.post("/api/logout")
    def logout(request: Request):
        with db.connect() as conn:
            conn.execute(
                "DELETE FROM sessions WHERE token=?",
                (
                    hashlib.sha256(
                        request.cookies.get("review_session", "").encode()
                    ).hexdigest(),
                ),
            )
        response = JSONResponse({"ok": True})
        response.delete_cookie("review_session")
        return response

    @app.get("/api/meta")
    def meta():
        effective = runtime.effective()
        return {
            "rubric": effective["rubric"],
            "level_policy": level_policy(effective),
            "demo": cfg.get("app", {}).get("demo", False),
            "model": effective["llm"]["model"],
            "services": {
                key: {
                    "url": effective[key].get("web_url", effective[key]["api_url"]),
                    "configured": bool(effective[key].get("api_url")),
                }
                for key in ("github", "confluence", "llm")
            },
            "limits": cfg.get("limits", {}),
            "analysis": analysis_options(effective),
            "branch_policy": "GitHub 기본 브랜치에 포함된 커밋 · UTC 날짜 기준",
        }

    @app.get("/api/members")
    def members():
        return db.members()

    def save_member(body, existing=False):
        with db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            all_members = db.members(conn)
            found = next((m for m in all_members if m["id"] == body.id), None)
            if existing and not found:
                raise HTTPException(404, "팀원을 찾을 수 없습니다.")
            if not existing and found:
                raise HTTPException(409, "이미 사용 중인 팀원 ID입니다.")
            for m in all_members:
                if m["id"] == body.id:
                    continue
                for key in ("github_ids", "confluence_ids"):
                    if set(m[key]) & set(getattr(body, key)):
                        raise HTTPException(
                            409, f"{key}: 다른 팀원에게 이미 연결된 계정입니다."
                        )
            conn.execute(
                "INSERT OR REPLACE INTO members VALUES(?,?)",
                (body.id, body.model_dump_json()),
            )
            db.audit(
                conn,
                None,
                "member_updated" if existing else "member_created",
                {"id": body.id},
            )
        return body.model_dump()

    @app.post("/api/members", status_code=201)
    def add_member(body: Member):
        return save_member(body)

    @app.put("/api/members/{mid}")
    def update_member(mid: str, body: Member):
        if mid != body.id:
            raise HTTPException(400, "팀원 ID는 변경할 수 없습니다.")
        return save_member(body, True)

    def require(eid, conn=None):
        ev = db.get(eid, conn)
        if not ev:
            raise HTTPException(404, "평가를 찾을 수 없습니다.")
        return ev

    def revision(ev, value):
        if ev["revision"] != value:
            raise HTTPException(
                409, "다른 작업으로 내용이 변경되었습니다. 새로고침 후 다시 시도하세요."
            )

    def validate_input(body, conn, previous=None):
        current_cfg = runtime.effective()
        known = {m["id"] for m in db.members(conn) if m["active"]}
        old_projects = {p["id"]: p for p in (previous or {}).get("projects", [])}
        known.update(m["id"] for m in (previous or {}).get("members_snapshot", []))
        try:
            for index, p in enumerate(body.projects, 1):
                if not set(p.member_ids) <= known:
                    raise ValueError(
                        f"프로젝트 {index}번 · 참여 팀원: 활성 팀원만 지정할 수 있습니다."
                    )
                for kind in ("github", "confluence"):
                    for url in getattr(p, kind + "_urls"):
                        if url not in old_projects.get(p.id, {}).get(
                            kind + "_urls", []
                        ):
                            try:
                                parse_source(url, current_cfg, kind)
                            except ValueError as exc:
                                raise ValueError(
                                    f"프로젝트 {index}번 · {kind} 근거 링크: {exc}"
                                ) from None
        except ValueError as e:
            raise HTTPException(422, str(e)) from None

    def runnable_input(data):
        try:
            return EvaluationInput.model_validate(data)
        except ValidationError as exc:
            raise RequestValidationError(exc.errors()) from None

    @app.get("/api/evaluations")
    def evaluations():
        with db.connect() as conn:
            evs = [
                json.loads(row["data"])
                for row in conn.execute(
                    "SELECT data FROM evaluations ORDER BY rowid DESC"
                )
            ]
        return [
            {
                k: ev.get(k)
                for k in (
                    "id",
                    "title",
                    "year",
                    "status",
                    "revision",
                    "progress",
                    "message",
                    "created_at",
                    "updated_at",
                    "error",
                    "demo",
                )
            }
            | {
                "project_count": len(ev["projects"]),
                "member_count": len(
                    {m for p in ev["projects"] for m in p["member_ids"]}
                ),
            }
            for ev in evs
        ]

    @app.post("/api/evaluations", status_code=201)
    def create_evaluation(body: DraftEvaluationInput):
        with db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            ev = {
                **{
                    key: body.model_dump(mode="json")[key]
                    for key in (*CONFIG_FIELDS, "revision")
                },
                "rubric": body.rubric.model_dump()
                if body.rubric
                else runtime.effective()["rubric"],
                "level_policy": level_policy(
                    {"evaluation": {"level_weights": body.level_weights}}
                )
                if body.level_weights
                else level_policy(runtime.effective()),
                "id": str(uuid.uuid4()),
                "status": "draft",
                "created_at": now(),
                "progress": 0,
                "message": "설정을 확인하고 평가를 시작하세요.",
                "demo": cfg.get("app", {}).get("demo", False),
            }
            db.save(conn, ev)
            db.audit(conn, ev["id"], "created", {"title": ev["title"]})
        return ev

    @app.get("/api/evaluations/{eid}")
    def evaluation(eid: str):
        ev = require(eid)
        issues = []
        if ev["status"] == "draft":
            try:
                body = runnable_input({k: ev[k] for k in CONFIG_FIELDS})
                with db.connect() as conn:
                    validate_input(body, conn)
            except RequestValidationError as exc:
                issues = [e["msg"] for e in validation_details(exc.errors())]
            except HTTPException as exc:
                issues = [exc.detail]
        return {**ev, "aggregate": aggregate(ev), "draft_issues": issues}

    @app.put("/api/evaluations/{eid}")
    def edit_evaluation(eid: str, body: DraftEvaluationInput):
        with db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            ev = require(eid, conn)
            revision(ev, body.revision)
            if ev["status"] == "running":
                raise HTTPException(
                    409, "실행 중인 평가는 취소 또는 완료 후 편집하세요."
                )
            if ev["status"] != "draft":
                body = runnable_input(body.model_dump())
                validate_input(body, conn, ev)
            effective = runtime.effective()
            rubric = (
                effective["rubric"]
                if body.use_current_rubric
                else body.rubric.model_dump()
                if body.rubric
                else ev.get("rubric", effective["rubric"])
            )
            policy = (
                level_policy(effective)
                if body.use_current_rubric
                else level_policy({"evaluation": {"level_weights": body.level_weights}})
                if body.level_weights
                else ev.get("level_policy")
            )
            before = copy.deepcopy(ev)
            if ev["status"] == "draft":
                ev.update(
                    {key: body.model_dump(mode="json")[key] for key in CONFIG_FIELDS}
                )
                ev.update(rubric=rubric, level_policy=policy or level_policy(effective))
            else:
                if len(body.reason.strip()) < 3:
                    raise HTTPException(422, "평가 수정 사유를 3자 이상 입력하세요.")
                try:
                    revise_evaluation(ev, body, db.members(conn), rubric, policy)
                except ValueError as exc:
                    raise HTTPException(422, str(exc)) from None
            db.save(conn, ev)
            db.audit(
                conn,
                eid,
                "configuration_edited",
                {
                    "reason": body.reason,
                    "before": before,
                    "after": {
                        key: ev.get(key)
                        for key in (
                            *CONFIG_FIELDS,
                            "rubric",
                            "level_policy",
                            "members_snapshot",
                            "needs_analysis",
                        )
                    },
                },
            )
        return ev

    @app.delete("/api/evaluations/{eid}")
    def delete_evaluation(eid: str, body: Revision):
        with db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            ev = require(eid, conn)
            revision(ev, body.revision)
            if ev["status"] != "draft":
                raise HTTPException(409, "초안만 삭제할 수 있습니다.")
            conn.execute("DELETE FROM evaluations WHERE id=?", (eid,))
            db.audit(conn, eid, "draft_deleted", {"title": ev["title"]})
        return {"ok": True}

    @app.post("/api/evaluations/{eid}/clone", status_code=201)
    def clone(eid: str):
        source = require(eid)
        body = DraftEvaluationInput(
            title=(source["title"] + " (복제)")[:120],
            year=source["year"],
            projects=source["projects"],
            rubric=source.get("rubric"),
            level_weights=(source.get("level_policy") or {}).get("weights"),
        )
        return create_evaluation(body)

    @app.post("/api/evaluations/{eid}/start")
    def start(eid: str, body: StartInput):
        try:
            with db.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                ev = require(eid, conn)
                revision(ev, body.revision)
                if body.force and len(body.reason.strip()) < 3:
                    raise HTTPException(422, "전체 재분석 사유를 3자 이상 입력하세요.")
                if (
                    ev["status"] not in ("draft", "failed", "cancelled")
                    and not (ev["status"] == "completed" and ev.get("needs_analysis"))
                    and not (body.force and ev["status"] in ("completed", "finalized"))
                ):
                    raise HTTPException(409, "현재 상태에서는 시작할 수 없습니다.")
                validate_input(
                    runnable_input({k: ev[k] for k in CONFIG_FIELDS}),
                    conn,
                )
                if ev["status"] != "draft":
                    db.audit(
                        conn,
                        eid,
                        "previous_run",
                        {
                            k: ev.get(k)
                            for k in (
                                "results",
                                "evidence",
                                "scopes",
                                "warnings",
                                "members_snapshot",
                                "rubric",
                                "level_policy",
                                "model",
                                "started_at",
                                "finished_at",
                                "error",
                                "analysis_policy",
                            )
                        },
                    )
                effective = runtime.effective()
                mids = {m for p in ev["projects"] for m in p["member_ids"]}
                snapshot = ev.get("members_snapshot") or [
                    m for m in db.members(conn) if m["id"] in mids
                ]
                policy = ev.get("level_policy")
                if not ev.get("started_at"):
                    policy = policy or level_policy(effective)
                try:
                    if policy:
                        for member in snapshot:
                            career_at(member, ev["year"])
                except ValueError as exc:
                    raise HTTPException(422, str(exc)) from None
                # Unaffected judgments and reviewer corrections survive incremental reanalysis.
                retained = [
                    r for r in ev.get("results", []) if not r.get("analysis_pending")
                ]
                for result in retained:
                    result.setdefault(
                        "model", ev.get("model", effective["llm"]["model"])
                    )
                if body.force:
                    ev["reanalysis_overrides"] = copy.deepcopy(retained)
                    retained = []
                    db.audit(
                        conn, eid, "full_reanalysis_requested", {"reason": body.reason}
                    )
                ev.update(
                    status="running",
                    analysis_policy=analysis_options(effective),
                    bypass_analysis_cache=body.force
                    or (
                        ev["status"] in ("failed", "cancelled")
                        and ev.get("bypass_analysis_cache", False)
                    ),
                    finalized_at=None,
                    finalization_reason=None,
                    cancel_requested=False,
                    error=None,
                    results=retained,
                    evidence=ev.get("evidence", {}),
                    scopes=ev.get("scopes", {}),
                    warnings=ev.get("warnings", []),
                    progress=0,
                    message="수집 준비 중",
                    members_snapshot=snapshot,
                    rubric=ev.get("rubric", effective["rubric"]),
                    started_at=now(),
                    finished_at=None,
                    model=effective["llm"]["model"],
                )
                if policy:
                    ev["level_policy"] = policy
                db.save(conn, ev)
                db.audit(
                    conn,
                    eid,
                    "started",
                    {
                        "model": ev["model"],
                        "rubric_version": ev["rubric"]["version"],
                        "demo": ev["demo"],
                    },
                )
        except sqlite3.IntegrityError:
            raise HTTPException(
                409,
                "이미 진행 중인 평가가 있습니다. 한 번에 한 평가만 실행할 수 있습니다.",
            ) from None
        worker.launch(eid)
        return ev

    @app.post("/api/evaluations/{eid}/cancel")
    def cancel(eid: str):
        with db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            ev = require(eid, conn)
            if ev["status"] != "running":
                raise HTTPException(409, "실행 중인 평가가 아닙니다.")
            ev["cancel_requested"] = True
            ev["message"] = "취소 요청됨 · 현재 네트워크 요청이 끝나면 중단합니다."
            db.save(conn, ev)
            db.audit(conn, eid, "cancel_requested", {})
        return ev

    @app.patch("/api/evaluations/{eid}/adjust")
    def adjust(eid: str, body: Adjustment):
        with db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            ev = require(eid, conn)
            revision(ev, body.revision)
            if ev["status"] not in ("completed", "finalized"):
                raise HTTPException(409, "결과가 있는 평가만 조정할 수 있습니다.")
            result = next(
                (
                    r
                    for r in ev["results"]
                    if r["member_id"] == body.member_id
                    and r["project_id"] == body.project_id
                ),
                None,
            )
            if not result or body.dimension not in DIMS:
                raise HTTPException(404, "평가 항목이 없습니다.")
            if result.get("analysis_pending"):
                raise HTTPException(409, "먼저 변경된 항목을 재분석하세요.")
            if ev["status"] == "finalized":
                ev.update(
                    status="completed", finalized_at=None, finalization_reason=None
                )
                db.audit(conn, eid, "reopened", {"reason": body.reason})
            dimension = result["dimensions"][body.dimension]
            before = json.loads(json.dumps(dimension))
            if body.reset:
                for key in ("adjusted_score", "adjustment_reason", "reviewer_comment"):
                    dimension.pop(key, None)
            else:
                dimension.update(
                    adjusted_score=body.score,
                    adjustment_reason=body.reason,
                    reviewer_comment=body.comment,
                )
            db.save(conn, ev)
            db.audit(
                conn,
                eid,
                "adjustment",
                {
                    "member_id": body.member_id,
                    "project_id": body.project_id,
                    "dimension": body.dimension,
                    "before": before,
                    "after": dimension,
                    "reason": body.reason,
                },
            )
        return {**ev, "aggregate": aggregate(ev)}

    @app.patch("/api/evaluations/{eid}/review-text")
    def review_text(eid: str, body: ReviewText):
        with db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            ev = require(eid, conn)
            revision(ev, body.revision)
            if ev["status"] not in ("completed", "finalized"):
                raise HTTPException(409, "결과가 있는 평가만 편집할 수 있습니다.")
            result = next(
                (
                    r
                    for r in ev["results"]
                    if r["member_id"] == body.member_id
                    and r["project_id"] == body.project_id
                ),
                None,
            )
            if not result:
                raise HTTPException(404, "평가 항목이 없습니다.")
            if result.get("analysis_pending"):
                raise HTTPException(409, "먼저 변경된 항목을 재분석하세요.")
            before = copy.deepcopy(result)
            result.update(
                adjusted_summary=body.summary, adjusted_limitations=body.limitations
            )
            ev.update(status="completed", finalized_at=None, finalization_reason=None)
            db.save(conn, ev)
            db.audit(
                conn,
                eid,
                "review_text_edited",
                {"reason": body.reason, "before": before, "after": result},
            )
        return {**ev, "aggregate": aggregate(ev)}

    @app.post("/api/evaluations/{eid}/finalize")
    def finalize(eid: str, body: Decision):
        with db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            ev = require(eid, conn)
            revision(ev, body.revision)
            if ev["status"] != "completed":
                raise HTTPException(409, "완료 평가만 확정할 수 있습니다.")
            if ev.get("needs_analysis"):
                raise HTTPException(409, "재분석이 필요한 항목을 먼저 평가하세요.")
            if not body.reason.strip() and (
                ev.get("warnings") or any(r["coverage"] < 100 for r in aggregate(ev))
            ):
                raise HTTPException(
                    422,
                    "누락 경고나 판정 보류가 있습니다. 검토한 내용을 확정 사유에 남겨주세요.",
                )
            ev.update(
                status="finalized", finalized_at=now(), finalization_reason=body.reason
            )
            db.save(conn, ev)
            db.audit(conn, eid, "finalized", {"reason": body.reason})
        return ev

    @app.post("/api/evaluations/{eid}/reopen")
    def reopen(eid: str, body: Decision):
        if len(body.reason.strip()) < 3:
            raise HTTPException(422, "확정 해제 사유를 입력하세요.")
        with db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            ev = require(eid, conn)
            revision(ev, body.revision)
            if ev["status"] != "finalized":
                raise HTTPException(409, "확정된 평가가 아닙니다.")
            ev["status"] = "completed"
            db.save(conn, ev)
            db.audit(conn, eid, "reopened", {"reason": body.reason})
        return ev

    @app.get("/api/evaluations/{eid}/audit")
    def audit(eid: str):
        require(eid)
        with db.connect() as conn:
            return [
                {**dict(row), "data": json.loads(row["data"])}
                for row in conn.execute(
                    "SELECT * FROM audit WHERE evaluation_id=? ORDER BY id DESC", (eid,)
                )
            ]

    @app.get("/api/evaluations/{eid}/export")
    def export(eid: str, format: str = "json"):
        ev = require(eid)
        if ev["status"] not in ("completed", "finalized"):
            raise HTTPException(409, "완료된 평가만 내보낼 수 있습니다.")
        if format == "json":
            payload = {**ev, "aggregate": aggregate(ev), "audit": audit(eid)}
            return Response(
                json.dumps(payload, ensure_ascii=False, indent=2),
                media_type="application/json",
                headers={
                    "Content-Disposition": f'attachment; filename="evaluation-{eid}.json"'
                },
            )
        if format != "csv":
            raise HTTPException(422, "json 또는 csv 형식을 선택하세요.")

        def safe(value):
            s = str(value) if value is not None else ""
            return (
                "'" + s
                if s.lstrip().startswith(("=", "+", "-", "@", "\t", "\r"))
                else s
            )

        f = io.StringIO()
        writer = csv.writer(f)
        writer.writerow(
            [
                "member_id",
                "name",
                "overall_score",
                "raw_score",
                "career_years",
                "level",
                "level_weight",
                "coverage",
                "project_id",
                "project_score",
                "project_weight",
                *DIMS,
            ]
        )
        for row in aggregate(ev):
            for project in row["projects"]:
                writer.writerow(
                    [
                        safe(v)
                        for v in [
                            row["member_id"],
                            row["name"],
                            row["score"],
                            row["raw_score"],
                            row["career_years"],
                            row["level"],
                            row["level_weight"],
                            row["coverage"],
                            project["project_id"],
                            project["score"],
                            project["weight"],
                            *[project["scores"][d] for d in DIMS],
                        ]
                    ]
                )
        return Response(
            "\ufeff" + f.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="evaluation-{eid}.csv"'
            },
        )

    @app.post("/api/demo/seed")
    def seed():
        if not cfg.get("app", {}).get("demo", False):
            raise HTTPException(403, "데모 모드에서만 사용할 수 있습니다.")
        from .demo import fixture_members, fixture_evaluation

        with db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if (
                conn.execute("SELECT COUNT(*) FROM members").fetchone()[0]
                or conn.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0]
            ):
                raise HTTPException(
                    409, "빈 데모 데이터베이스에서만 샘플을 만들 수 있습니다."
                )
            for m in fixture_members():
                conn.execute(
                    "INSERT INTO members VALUES(?,?)",
                    (m["id"], json.dumps(m, ensure_ascii=False)),
                )
            ev = {
                **fixture_evaluation(cfg),
                "id": str(uuid.uuid4()),
                "status": "draft",
                "created_at": now(),
                "revision": 0,
                "progress": 0,
                "demo": True,
            }
            db.save(conn, ev)
            db.audit(conn, ev["id"], "demo_seeded", {"members": 10, "projects": 6})
        return ev

    @app.get("/")
    def index():
        return HTMLResponse(index_html)

    app.mount("/static", StaticFiles(directory=static), name="static")
    return app
