"""Isolated browser test for identity chips and career-weighted results."""

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
    raise RuntimeError("Test server startup failed")


os.environ["DASHBOARD_PASSWORD"] = "browser-test-password!"
with tempfile.TemporaryDirectory() as tmp:
    fixture_server, fixture_thread, mock_url = serve(create_demo_app())
    cfg = load_settings("config/demo.toml")
    cfg["storage"]["path"] = str(Path(tmp) / "review.sqlite3")
    for kind in ("github", "confluence", "llm"):
        cfg[kind]["api_url"] = mock_url + "/" + kind
    app = create_app(cfg)
    server, thread, url = serve(app)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            page.on("pageerror", lambda err: errors.append(str(err)))
            page.goto(url)
            page.get_by_label("대시보드 비밀번호").fill("browser-test-password!")
            page.get_by_role("button", name="워크스페이스 접속").click()
            page.get_by_role("button", name="2026년 데모 불러오기").click()
            expect(page.get_by_role("button", name="평가 시작")).to_be_visible()
            # Simulate optional account fields absent in older stored records.
            legacy = {
                "id": "Z-LEGACY",
                "name": "기존 계정 호환성 검증",
                "active": True,
                "confluence_ids": None,
            }
            with app.state.db.connect() as conn:
                conn.execute(
                    "INSERT INTO members VALUES(?,?)",
                    (legacy["id"], json.dumps(legacy)),
                )

            def legacy_response(route):
                response = route.fetch()
                members = response.json()
                for member in members:
                    if member["id"] == legacy["id"] and not member["github_ids"]:
                        member.pop("github_ids")
                        member["confluence_ids"] = None
                route.fulfill(response=response, json=members)

            page.route("**/api/members", legacy_response)
            page.get_by_role("button", name="팀원 관리").click()
            expect(
                page.get_by_role("heading", name="팀원 관리", exact=True)
            ).to_be_visible()
            page.locator('[data-action="edit-member"][data-id="Z-LEGACY"]').click()
            expect(page.get_by_text("추가된 ID가 없습니다.", exact=True)).to_have_count(
                2
            )
            page.get_by_label("연차", exact=True).fill("4")
            page.get_by_label("GitHub ID", exact=True).fill("legacy-github")
            page.get_by_role("button", name="GitHub ID 추가", exact=True).click()
            page.get_by_label("Confluence ID", exact=True).fill("legacy-confluence")
            page.get_by_role("button", name="Confluence ID 추가", exact=True).click()
            page.get_by_role("button", name="저장", exact=True).click()
            expect(
                page.locator("tbody tr").filter(has_text="Z-LEGACY")
            ).to_contain_text("legacy-github")
            page.locator('[data-action="edit-member"][data-id="Z-LEGACY"]').click()
            expect(
                page.locator('[data-identity-list="github_ids"] .identity-tag')
            ).to_have_count(1)
            expect(
                page.locator('[data-identity-list="confluence_ids"] .identity-tag')
            ).to_have_count(1)
            page.get_by_role("button", name="취소", exact=True).click()
            page.unroute("**/api/members", legacy_response)
            assert all(
                "?v=" in src
                for src in page.locator("script[src]").evaluate_all(
                    "scripts => scripts.map(s => s.src)"
                )
            )
            page.locator('[data-action="edit-member"]').first.click()
            chips = page.locator('[data-identity-list="github_ids"] .identity-tag')
            expect(chips).to_have_count(2)
            assert page.get_by_text("Git 이메일", exact=False).count() == 0
            page.get_by_label("GitHub ID", exact=True).fill("extra-github")
            page.get_by_role("button", name="GitHub ID 추가", exact=True).click()
            expect(chips).to_have_count(3)
            page.get_by_label("GitHub ID", exact=True).fill("extra-github")
            page.get_by_label("GitHub ID", exact=True).press("Enter")
            expect(chips).to_have_count(3)
            page.get_by_label("GitHub ID", exact=True).fill("")
            page.get_by_role("button", name="extra-github 삭제", exact=True).click()
            expect(chips).to_have_count(2)
            page.get_by_label("Confluence ID", exact=True).fill("additional-cf")
            page.get_by_label("Confluence ID", exact=True).press("Enter")
            expect(
                page.locator('[data-identity-list="confluence_ids"] .identity-tag')
            ).to_have_count(2)
            page.get_by_label("연차", exact=True).fill("9")
            expect(page.locator("#career-preview")).to_contain_text("CL3")
            page.screenshot(
                path="artifacts/member-identities-level.png", full_page=True
            )
            page.get_by_role("button", name="저장", exact=True).click()
            expect(page.locator("tbody tr").first).to_contain_text("9년차")
            page.locator('[data-action="edit-member"]').first.click()
            expect(
                page.locator('[data-identity-list="confluence_ids"] .identity-tag')
            ).to_have_count(2)
            expect(page.get_by_label("연차", exact=True)).to_have_value("9")
            page.get_by_role("button", name="취소", exact=True).click()
            page.get_by_role("button", name="평가 대시보드").click()
            page.locator('[data-action="open"]').first.click()
            page.get_by_role("button", name="평가 시작").click()
            expect(page.get_by_role("button", name="최종 확정")).to_be_visible(
                timeout=60000
            )
            expect(
                page.get_by_text("2026년 기준 9년차 · CL3", exact=False)
            ).to_be_visible()
            expect(page.get_by_text("레벨 계수 0.90", exact=False)).to_be_visible()
            rows = page.evaluate(
                "async () => { const list=await (await fetch('/api/evaluations')).json(); return (await (await fetch('/api/evaluations/'+list[0].id)).json()).aggregate; }"
            )
            assert rows[0]["level"] == "CL3"
            assert abs(rows[0]["score"] - rows[0]["raw_score"] * 0.9) < 0.02
            page.screenshot(path="artifacts/level-weighted-result.png", full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate(
                "document.documentElement.scrollWidth <= window.innerWidth"
            )
            page.get_by_role("button", name="팀원 관리").click()
            page.locator('[data-action="edit-member"]').first.click()
            assert page.evaluate(
                "document.documentElement.scrollWidth <= window.innerWidth"
            )
            page.screenshot(
                path="artifacts/member-identities-mobile.png", full_page=True
            )
            page.get_by_role("button", name="취소", exact=True).click()
            page.get_by_role("button", name="팀원 추가").click()
            page.get_by_label("팀원 ID", exact=True).fill("NEW-CL4")
            page.get_by_label("이름", exact=True).fill("신규 등록 검증")
            page.get_by_label("연차", exact=True).fill("17")
            page.get_by_label("GitHub ID", exact=True).fill("new-cl4-dev")
            page.get_by_role("button", name="GitHub ID 추가", exact=True).click()
            page.get_by_role("button", name="저장", exact=True).click()
            expect(page.locator("tbody tr").filter(has_text="NEW-CL4")).to_contain_text(
                "CL4"
            )
            assert not errors, errors
            Path("artifacts/level-verification.json").write_text(
                json.dumps(
                    {
                        "date": "2026-09-27",
                        "browser": browser.version,
                        "page_errors": errors,
                        "mode": "synthetic LLM",
                        "members": rows,
                        "checks": [
                            "missing/null identity lists render and edit",
                            "legacy member add IDs/save/reopen",
                            "versioned dashboard assets",
                            "identity add/Enter/remove/duplicate",
                            "member edit/save/reopen",
                            "new member save",
                            "Git email removed",
                            "career CL preview",
                            "weighted aggregate",
                            "mobile layout",
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            browser.close()
            print(
                "Identity add/Enter/remove/duplicate/save/reopen, email removal, career preview, weighted results and mobile checks passed."
            )
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        fixture_server.should_exit = True
        fixture_thread.join(timeout=10)
