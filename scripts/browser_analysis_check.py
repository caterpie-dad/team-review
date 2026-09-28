"""Isolated browser regression for adaptive evidence and judgment caching."""

import json
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


os.environ["DASHBOARD_PASSWORD"] = "efficiency-browser-test!"
with tempfile.TemporaryDirectory() as tmp:
    mock_server, mock_thread, mock_url = serve(create_demo_app())
    cfg = load_settings("config/demo.toml")
    cfg["storage"]["path"] = str(Path(tmp) / "review.sqlite3")
    for kind in ("github", "confluence", "llm"):
        cfg[kind]["api_url"] = mock_url + "/" + kind
    app = create_app(cfg)
    server, thread, url = serve(app)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 1050})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(url)
            page.get_by_label("대시보드 비밀번호").fill("efficiency-browser-test!")
            page.get_by_role("button", name="워크스페이스 접속").click()
            page.get_by_role("button", name="2026년 데모 불러오기").click()
            expect(page.get_by_role("button", name="평가 시작")).to_be_visible()
            ev = page.evaluate("""async () => {
                const p = {...state.evaluation.projects[0], weight:100, member_ids:['T001','T002'],
                           confluence_urls:[], github_urls:['https://github.demo.test/demo/efficiency']};
                return await api('/evaluations','POST',{title:'연속 커밋 효율 검증',year:2026,projects:[p]});
            }""")
            page.get_by_role("button", name="평가 대시보드").click()
            page.locator(f'[data-action="open"][data-id="{ev["id"]}"]').click()
            page.get_by_role("button", name="평가 시작").click()
            expect(page.get_by_role("button", name="최종 확정")).to_be_visible(
                timeout=60000
            )
            expect(page.locator(".analysis-summary")).to_contain_text("LLM 요청 1회")
            expect(page.locator(".analysis-summary")).to_contain_text(
                "원본 근거는 보존합니다"
            )
            page.get_by_role("button", name="근거 원문 확인", exact=False).first.click()
            expect(
                page.get_by_role("heading", name="통합 전 커밋 근거")
            ).to_be_visible()
            expect(page.locator('#modal [data-action="evidence"]')).to_have_count(24)
            page.screenshot(path="artifacts/consolidated-evidence.png", full_page=True)
            page.locator('#modal [data-action="evidence"]').first.click()
            expect(page.locator("#modal pre").first).to_contain_text("@@")
            page.get_by_role("button", name="닫기", exact=True).click()
            page.screenshot(path="artifacts/analysis-efficiency.png", full_page=True)
            page.get_by_role("button", name="복제", exact=True).click()
            page.get_by_role("button", name="초안 저장").click()
            expect(page.get_by_role("button", name="평가 시작")).to_be_visible()
            page.get_by_role("button", name="평가 시작").click()
            expect(page.get_by_role("button", name="최종 확정")).to_be_visible(
                timeout=60000
            )
            expect(page.locator(".analysis-summary")).to_contain_text("LLM 요청 0회")
            expect(page.locator(".analysis-summary")).to_contain_text(
                "기존 판정 재사용 1개 배치"
            )
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(
                path="artifacts/analysis-efficiency-mobile.png", full_page=True
            )
            assert not errors, errors
            Path("artifacts/analysis-browser-verification.json").write_text(
                json.dumps(
                    {
                        "browser": browser.version,
                        "page_errors": errors,
                        "mode": "isolated synthetic HTTP services",
                        "checks": [
                            "48 commits from 2 authors evaluated",
                            "efficiency metrics shown",
                            "consolidated and original evidence accessible",
                            "clone reuses judgments with zero new LLM calls",
                            "mobile layout",
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            browser.close()
            print(
                "Adaptive analysis, raw evidence access, cache reuse and mobile display passed."
            )
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        mock_server.should_exit = True
        mock_thread.join(timeout=10)
