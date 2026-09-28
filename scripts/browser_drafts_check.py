"""Isolated browser regression for saving and completing unfinished drafts."""

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
from app.demo import create_demo_app, fixture_evaluation, fixture_members


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


os.environ["DASHBOARD_PASSWORD"] = "draft-browser-test!"
with tempfile.TemporaryDirectory() as tmp:
    mock_app = create_demo_app()
    mock_app.state.hold = False

    @mock_app.middleware("http")
    async def hold_collection(request, call_next):
        while mock_app.state.hold and request.url.path.startswith("/github/"):
            await asyncio.sleep(0.05)
        return await call_next(request)

    mock_server, mock_thread, mock_url = serve(mock_app)
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
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(url)
            page.get_by_label("대시보드 비밀번호").fill("draft-browser-test!")
            page.get_by_role("button", name="워크스페이스 접속").click()
            # Start without any team members and leave three project names blank.
            page.get_by_role("button", name="새 평가 만들기").click()
            page.locator("#eval-title").fill("")
            page.locator("#eval-year").fill("2026")
            page.get_by_role("button", name="프로젝트 추가").click()
            page.get_by_role("button", name="프로젝트 추가").click()
            page.locator('[data-field="start"]').first.fill("")
            page.locator('[data-field="end"]').first.fill("")
            page.get_by_role("button", name="초안 저장").click()
            expect(
                page.get_by_text("초안이 저장되었습니다.", exact=False)
            ).to_be_visible()
            expect(page.get_by_role("heading", name="이름 없는 평가")).to_be_visible()
            expect(
                page.get_by_text(
                    "프로젝트 · 3번 · 이름: 1자 이상 입력하세요.", exact=True
                )
            ).to_be_visible()
            eid = page.evaluate("state.evaluation.id")
            with page.expect_response(lambda r: r.url.endswith("/start")) as rejected:
                page.get_by_role("button", name="평가 시작").click()
            assert rejected.value.status == 422
            expect(page.locator("#toast")).to_contain_text(
                "평가 이름: 1자 이상 입력하세요."
            )
            assert "String should" not in page.locator("body").inner_text()
            assert page.evaluate("state.evaluation.status") == "draft"
            Path("artifacts").mkdir(exist_ok=True)
            page.screenshot(path="artifacts/incomplete-draft.png", full_page=True)
            page.reload()
            page.locator(f'[data-action="edit-from-list"][data-id="{eid}"]').click()
            expect(page.locator("#eval-title")).to_have_value("")
            expect(page.locator('[data-field="name"]')).to_have_count(3)
            expect(page.locator('[data-field="start"]').first).to_have_value("")
            # Make a partial edit and prove saving does not require finishing the form.
            page.locator("#eval-title").fill("초안 보완 브라우저 검증")
            page.get_by_role("button", name="초안 저장").click()
            expect(
                page.get_by_role("heading", name="초안 보완 브라우저 검증")
            ).to_be_visible()
            sample = fixture_evaluation(cfg)
            status = page.evaluate(
                """async member => (await fetch('/api/members', {
                method:'POST', headers:{'Content-Type':'application/json','X-Review-Request':'1'}, body:JSON.stringify(member)
            })).status""",
                fixture_members()[0],
            )
            assert status == 201
            page.reload()
            page.locator(f'[data-action="edit-from-list"][data-id="{eid}"]').click()
            for _ in range(2):
                page.get_by_role("button", name="프로젝트 삭제").last.click()
            page.locator('[data-field="name"]').fill("완성한 테스트 프로젝트")
            page.locator('[data-field="start"]').fill("2026-01-01")
            page.locator('[data-field="end"]').fill("2026-12-31")
            page.locator('[data-field="weight"]').fill("100")
            page.get_by_role("button", name="전체 선택 / 해제").click()
            page.locator('[data-field="github_urls"]').fill(
                sample["projects"][0]["github_urls"][0]
            )
            page.get_by_role("button", name="초안 저장").click()
            expect(
                page.get_by_role("heading", name="초안 보완 브라우저 검증")
            ).to_be_visible()
            assert page.evaluate("state.evaluation.draft_issues") == []
            other = page.evaluate(
                "async () => await api('/evaluations/'+state.evaluation.id+'/clone','POST',{})"
            )
            mock_app.state.hold = True
            page.get_by_role("button", name="평가 시작").click()
            expect(page.get_by_role("button", name="실행 취소")).to_be_visible()
            page.get_by_role("button", name="평가 대시보드").click()
            page.locator(f'[data-action="open"][data-id="{other["id"]}"]').click()
            expect(page.get_by_role("button", name="평가 시작")).to_be_disabled()
            page.get_by_role("button", name="평가 대시보드").click()
            page.locator(f'[data-action="open"][data-id="{eid}"]').first.click()
            page.get_by_role("button", name="실행 취소").click()
            expect(page.get_by_role("button", name="취소 요청됨")).to_be_visible()
            mock_app.state.hold = False
            expect(page.get_by_role("button", name="다시 시도")).to_be_visible(
                timeout=30000
            )
            assert page.evaluate("state.evaluation.status") == "cancelled"
            page.get_by_role("button", name="다시 시도").click()
            expect(page.get_by_role("button", name="최종 확정")).to_be_visible(
                timeout=60000
            )
            assert page.evaluate("state.evaluation.status") == "completed"
            page.get_by_role("button", name="평가 대시보드").click()
            page.get_by_role("button", name="새 평가 만들기").click()
            page.get_by_role("button", name="초안 저장").click()
            expect(page.get_by_role("button", name="설정 보완하기")).to_be_visible()
            removed_id = page.evaluate("state.evaluation.id")
            page.get_by_role("button", name="평가 편집", exact=True).click()
            page.once("dialog", lambda dialog: dialog.accept())
            page.get_by_role("button", name="초안 삭제").click()
            expect(
                page.get_by_role("heading", name="기여를 이해하는 새로운 기준")
            ).to_be_visible()
            assert (
                page.locator(f'[data-action="open"][data-id="{removed_id}"]').count()
                == 0
            )
            assert not errors, errors
            Path("artifacts/draft-verification.json").write_text(
                json.dumps(
                    {
                        "browser": browser.version,
                        "page_errors": errors,
                        "mode": "isolated synthetic HTTP services",
                        "checks": [
                            "blank evaluation and three project names save",
                            "no members or sources save",
                            "empty dates and incomplete weights save",
                            "reload and partial edit retain inputs",
                            "start rejects incomplete draft with Korean field labels",
                            "complete draft then evaluate",
                            "second evaluation start disabled while running",
                            "cancel and retry through dashboard",
                            "delete unfinished draft from editor",
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            browser.close()
            print(
                "Incomplete draft save, reload, Korean validation and completed evaluation passed."
            )
    finally:
        mock_app.state.hold = False
        server.should_exit = True
        thread.join(timeout=10)
        mock_server.should_exit = True
        mock_thread.join(timeout=10)
