"""Project source chips and stable progress polling through the real HTTP workflow."""

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
from app.demo import create_demo_app, fixture_evaluation


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


os.environ["DASHBOARD_PASSWORD"] = "source-browser-test!"
with tempfile.TemporaryDirectory() as tmp:
    mock_app = create_demo_app()
    mock_app.state.hold = True

    @mock_app.middleware("http")
    async def hold_collection(request, call_next):
        while mock_app.state.hold and request.url.path.startswith("/github/"):
            await asyncio.sleep(0.05)
        return await call_next(request)

    mock_server, mock_thread, mock_url = serve(mock_app)
    cfg = load_settings("config/demo.toml")
    # Collection is deliberately held across several polling/navigation checks.
    cfg["limits"]["request_timeout"] = 60
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
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(url)
            page.get_by_label("대시보드 비밀번호").fill("source-browser-test!")
            page.get_by_role("button", name="워크스페이스 접속").click()
            page.get_by_role("button", name="2026년 데모 불러오기").click()
            expect(page.get_by_role("button", name="평가 시작")).to_be_visible()
            page.get_by_role("button", name="평가 편집", exact=True).click()
            card = page.locator("[data-project]").first
            for key in ("github_urls", "confluence_urls"):
                expect(
                    card.locator(f'[data-source-list="{key}"] .identity-tag')
                ).to_have_count(1)
            sample = fixture_evaluation(cfg)
            for key in ("github_urls", "confluence_urls"):
                entry = card.locator(f'[data-source-entry="{key}"]')
                entry.fill(sample["projects"][1][key][0])
                if key == "github_urls":
                    card.locator(
                        f'[data-action="add-source"][data-key="{key}"]'
                    ).click()
                else:
                    entry.press("Enter")
                expect(
                    card.locator(f'[data-source-list="{key}"] .identity-tag')
                ).to_have_count(2)
                entry.fill(sample["projects"][1][key][0])
                entry.press("Enter")
                expect(page.locator("#toast")).to_contain_text("이미 추가된 링크")
                expect(
                    card.locator(f'[data-source-list="{key}"] .identity-tag')
                ).to_have_count(2)
                entry.fill("")
            entry = card.locator('[data-source-entry="github_urls"]')
            entry.fill("javascript:alert(1)")
            entry.press("Enter")
            expect(page.locator("#toast")).to_contain_text("http 또는 https")
            entry.fill(sample["projects"][2]["github_urls"][0])
            page.get_by_role("button", name="초안 저장").click()
            expect(page.locator("#toast")).to_contain_text("추가 버튼")
            # Unadded input survives participant-driven editor rendering.
            card.locator('[data-action="toggle-member"]').last.click()
            expect(entry).to_have_value(sample["projects"][2]["github_urls"][0])
            entry.press("Enter")
            expect(
                card.locator('[data-source-list="github_urls"] .identity-tag')
            ).to_have_count(3)
            card.locator(
                '[data-action="remove-source"][data-key="github_urls"]'
            ).last.click()
            expect(
                card.locator('[data-source-list="github_urls"] .identity-tag')
            ).to_have_count(2)
            # Collapse to one project, containing both sources of each service.
            for _ in range(5):
                page.get_by_role("button", name="프로젝트 삭제").last.click()
            page.locator('[data-field="weight"]').fill("100")
            page.get_by_role("button", name="전체 선택 / 해제").click()
            page.get_by_role("button", name="초안 저장").click()
            expect(page.get_by_role("button", name="평가 시작")).to_be_visible()
            eid = page.evaluate("state.evaluation.id")
            page.reload()
            page.locator(f'[data-action="edit-from-list"][data-id="{eid}"]').click()
            for key in ("github_urls", "confluence_urls"):
                expect(
                    page.locator(f'[data-source-list="{key}"] .identity-tag')
                ).to_have_count(2)
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate(
                "document.documentElement.scrollWidth <= innerWidth + 1"
            )
            page.screenshot(path="artifacts/project-sources-mobile.png", full_page=True)
            page.set_viewport_size({"width": 1440, "height": 1050})
            page.get_by_role("button", name="초안 저장").click()
            page.get_by_role("button", name="평가 시작").click()
            expect(page.get_by_role("button", name="실행 취소")).to_be_visible()
            app.state.worker.update(
                eid,
                progress=25,
                message="수집 진행 확인",
                warnings=["범위 내 자료 검토 안내"],
            )
            expect(page.locator("#progress-value")).to_have_text("25%", timeout=10000)
            page.locator("#evaluation-warnings summary").click()
            page.get_by_role("button", name="실행 취소").focus()
            page.evaluate(
                "window.stableNodes = [document.querySelector('.layout'), document.querySelector('.fade'), document.querySelector('[role=progressbar]'), document.querySelector('.spinner'), document.querySelector('#evaluation-warnings details')]; window.stableFocus = document.activeElement; window.stableScroll = scrollY"
            )
            app.state.worker.update(
                eid,
                progress=40,
                message="변경된 진행 상황",
                warnings=["범위 내 자료 검토 안내", "새로 수집한 자료 안내"],
            )
            expect(page.locator("#progress-value")).to_have_text("40%", timeout=10000)
            expect(page.locator("#progress-message")).to_have_text("변경된 진행 상황")
            assert page.evaluate(
                "stableNodes.every(n => n.isConnected) && document.activeElement === stableFocus && scrollY === stableScroll && document.querySelector('#evaluation-warnings details').open"
            )
            page.get_by_role("button", name="평가 대시보드").click()
            expect(page.locator("#overview-progress")).to_contain_text("40%")
            page.evaluate("window.overviewNode = document.querySelector('.layout')")
            app.state.worker.update(eid, progress=45, message="목록 진행 갱신")
            expect(page.locator("#overview-progress")).to_contain_text(
                "45%", timeout=10000
            )
            assert page.evaluate("overviewNode === document.querySelector('.layout')")
            page.locator(f'[data-action="open"][data-id="{eid}"]').first.click()
            expect(page.get_by_role("button", name="실행 취소")).to_be_visible()
            # Hold one already-requested poll response, leave the screen, then release it.
            page.evaluate(
                """id => {
                const original = window.fetch;
                window.fetch = async (...args) => {
                    const response = await original(...args);
                    if (String(args[0]) === '/api/evaluations/' + id && !window.pollHeld) {
                        window.pollHeld = true;
                        await new Promise(resolve => window.releasePoll = resolve);
                    }
                    return response;
                };
            }""",
                eid,
            )
            for _ in range(100):
                if page.evaluate("window.pollHeld === true"):
                    break
                page.wait_for_timeout(100)
            assert page.evaluate("window.pollHeld === true")
            page.get_by_role("button", name="팀원 관리").click()
            expect(
                page.get_by_role("heading", name="팀원 관리", exact=True)
            ).to_be_visible()
            page.evaluate(
                "window.memberPage = document.querySelector('.layout'); window.releasePoll()"
            )
            page.wait_for_timeout(300)
            assert page.evaluate(
                "state.view === 'members' && memberPage === document.querySelector('.layout')"
            )
            mock_app.state.hold = False
            page.get_by_role("button", name="평가 대시보드").click()
            page.locator(f'[data-action="open"][data-id="{eid}"]').first.click()
            expect(page.get_by_role("button", name="최종 확정")).to_be_visible(
                timeout=60000
            )
            scope = page.evaluate(
                "state.evaluation.scopes[state.evaluation.projects[0].id]"
            )
            assert scope["repos"] == ["demo/project-1", "demo/project-2"]
            assert scope["confluence_roots"] == ["1000", "1100"]
            assert (
                page.evaluate("Object.values(state.evaluation.evidence).flat().length")
                == 21
            )
            assert not errors, errors
            Path("artifacts/source-polling-verification.json").write_text(
                json.dumps(
                    {
                        "browser": browser.version,
                        "page_errors": errors,
                        "mode": "isolated synthetic HTTP services",
                        "checks": [
                            "add source by button and Enter",
                            "existing/multiple URLs listed",
                            "remove/duplicate/invalid URL handling",
                            "unadded URL guard and retention",
                            "save/reload source arrays",
                            "mobile layout",
                            "all repositories and Confluence roots collected",
                            "progress changes without replacing layout/bar/spinner or losing focus/scroll/details",
                            "overview updates without replacing layout",
                            "late poll cannot overwrite navigation",
                            "completion opens results",
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            browser.close()
            print("Multiple project sources and stable progress polling passed.")
    finally:
        mock_app.state.hold = False
        server.should_exit = True
        thread.join(timeout=10)
        mock_server.should_exit = True
        mock_thread.join(timeout=10)
