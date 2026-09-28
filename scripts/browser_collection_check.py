"""Source failures stay visible while remaining sources complete the evaluation."""

import json
import asyncio
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn
from playwright.sync_api import sync_playwright, expect
from app.config import load_settings
from app.main import create_app
from app.demo import create_demo_app
from starlette.responses import JSONResponse


def serve(app):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(
        target=server.run, kwargs={"sockets": [sock]}, daemon=True
    )
    thread.start()
    for _ in range(100):
        if server.started:
            return server, thread, f"http://127.0.0.1:{port}"
        time.sleep(0.05)
    raise RuntimeError("Test server did not start")


os.environ["DASHBOARD_PASSWORD"] = "collection-browser-test!"
with tempfile.TemporaryDirectory() as tmp:
    mock_app = create_demo_app()
    mock_app.state.hold_llm = True

    @mock_app.middleware("http")
    async def unavailable_sources(request, call_next):
        path = request.url.path
        if path == "/github/repos/demo/empty/commits":
            return JSONResponse(
                {"message": "Git Repository is empty."}, status_code=409
            )
        if path == "/github/repos/demo/private/commits":
            return JSONResponse(
                {"message": "TOKEN=do-not-display-this"}, status_code=403
            )
        if path == "/confluence/content/1003":
            return JSONResponse(
                {"message": "TOKEN=do-not-display-this"}, status_code=404
            )
        while mock_app.state.hold_llm and path == "/llm/chat/completions":
            await asyncio.sleep(0.05)
        return await call_next(request)

    mock_server, mock_thread, mock_url = serve(mock_app)
    cfg = load_settings("config/demo.toml")
    cfg["storage"]["path"] = str(Path(tmp) / "review.sqlite3")
    cfg["limits"]["llm_timeout"] = 60
    for kind in ("github", "confluence", "llm"):
        cfg[kind]["api_url"] = mock_url + "/" + kind
    app = create_app(cfg)
    server, thread, url = serve(app)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 1050})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(url)
            page.get_by_label("대시보드 비밀번호").fill("collection-browser-test!")
            page.get_by_role("button", name="워크스페이스 접속").click()
            page.get_by_role("button", name="2026년 데모 불러오기").click()
            expect(page.get_by_role("button", name="평가 시작")).to_be_visible()
            ev = page.evaluate("state.evaluation")
            project = ev["projects"][0]
            project["github_urls"][:0] = [
                "https://github.demo.test/demo/empty",
                "https://github.demo.test/demo/private",
            ]
            page.evaluate(
                "async body => await api('/evaluations/'+state.evaluation.id,'PUT',body)",
                {
                    "title": ev["title"],
                    "year": ev["year"],
                    "projects": ev["projects"],
                    "revision": ev["revision"],
                },
            )
            page.evaluate("async () => await openEvaluation(state.evaluation.id)")
            page.get_by_role("button", name="평가 시작").click()
            issue_block = page.locator("#collection-issues")
            expect(issue_block).to_contain_text("수집 제외 3건", timeout=20000)
            expect(issue_block).to_contain_text("평가를 계속하고 있습니다")
            expect(issue_block).to_contain_text("HTTP 409")
            expect(issue_block).to_contain_text("GitHub가 빈 저장소라고 응답했습니다")
            expect(issue_block).to_contain_text("HTTP 403")
            expect(issue_block).to_contain_text("읽기 권한")
            expect(issue_block).to_contain_text("HTTP 404")
            for path in (
                "/github/repos/demo/empty/commits",
                "/github/repos/demo/private/commits",
                "/confluence/content/1003",
            ):
                expect(issue_block).to_contain_text(path)
            assert "do-not-display-this" not in page.locator("body").inner_text()
            assert page.evaluate("state.evaluation.status") == "running"
            page.screenshot(
                path="artifacts/collection-issues-running.png", full_page=True
            )
            mock_app.state.hold_llm = False
            expect(page.get_by_role("button", name="최종 확정")).to_be_visible(
                timeout=60000
            )
            expect(issue_block).to_contain_text("수집 가능한 근거로 평가했습니다")
            expect(
                page.get_by_text("잠정 · 일부 근거 미수집", exact=True)
            ).to_be_visible()
            assert page.evaluate(
                "state.evaluation.aggregate.find(r => r.member_id === 'T001').provisional"
            )
            assert page.evaluate("state.evaluation.results.length") == 30
            assert (
                page.evaluate(
                    "state.evaluation.scopes['project-2'].collection_incomplete"
                )
                is False
            )
            assert (
                page.evaluate(
                    "state.evaluation.aggregate.find(r => r.member_id === 'T006').collection_incomplete"
                )
                is False
            )
            page.screenshot(
                path="artifacts/collection-issues-completed.png", full_page=True
            )
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate(
                "document.documentElement.scrollWidth <= innerWidth + 1"
            )
            page.screenshot(
                path="artifacts/collection-issues-mobile.png", full_page=True
            )
            page.reload()
            page.locator('[data-action="open"]').first.click()
            expect(issue_block).to_contain_text("HTTP 409")
            expect(
                page.get_by_text("잠정 · 일부 근거 미수집", exact=True)
            ).to_be_visible()
            assert not errors, errors
            Path("artifacts/collection-verification.json").write_text(
                json.dumps(
                    {
                        "mode": "isolated synthetic HTTP services",
                        "browser": browser.version,
                        "members": 10,
                        "projects": 6,
                        "results": 30,
                        "page_errors": errors,
                        "checks": [
                            "409 empty repository skipped",
                            "403 repository and 404 document skipped",
                            "diagnostic path/status/reason/action visible during execution",
                            "provider secrets not echoed",
                            "other sources and projects complete",
                            "affected scores provisional without penalizing unaffected projects",
                            "diagnostics survive reload",
                            "mobile layout",
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            browser.close()
            print(
                "Partial source failures, visible diagnostics and completed evaluation passed."
            )
    finally:
        mock_app.state.hold_llm = False
        server.should_exit = True
        thread.join(timeout=10)
        mock_server.should_exit = True
        mock_thread.join(timeout=10)
