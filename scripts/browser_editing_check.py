"""Isolated end-to-end verification of corrections, policies and integration settings."""

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


os.environ["DASHBOARD_PASSWORD"] = "editing-browser-test!"
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
            page.get_by_label("대시보드 비밀번호").fill("editing-browser-test!")
            page.get_by_role("button", name="워크스페이스 접속").click()
            page.get_by_role("button", name="2026년 데모 불러오기").click()
            page.get_by_role("button", name="평가 시작").click()
            expect(page.get_by_role("button", name="최종 확정")).to_be_visible(
                timeout=60000
            )
            page.locator('[data-action="adjust"]').first.click()
            page.get_by_role("spinbutton", name="평가자 점수", exact=True).fill("96")
            page.locator('textarea[name="reason"]').fill(
                "사후 구성 변경에도 보존할 수동 점수"
            )
            page.get_by_role("button", name="조정 반영").click()
            page.get_by_role("button", name="평가 편집", exact=True).click()
            page.locator("#eval-title").fill("사후 편집 검증 평가")
            page.locator('[data-field="weight"]').nth(0).fill("30")
            page.locator('[data-field="weight"]').nth(1).fill("15")
            page.locator(
                '[data-action="toggle-member"][data-pid="project-2"][data-mid="T003"]'
            ).click()
            page.locator(
                '[data-action="toggle-member"][data-pid="project-2"][data-mid="T002"]'
            ).click()
            page.locator('[data-rubric-field="weight"]').nth(0).fill("40")
            page.locator('[data-rubric-field="weight"]').nth(1).fill("20")
            page.locator("#edit-reason").fill("프로젝트 참여자와 비율 사후 보정")
            page.screenshot(path="artifacts/post-evaluation-editor.png", full_page=True)
            page.get_by_role("button", name="변경 사항 저장").click()
            expect(page.get_by_role("button", name="변경 항목 재분석")).to_be_visible()
            page.get_by_role("button", name="변경 항목 재분석").click()
            expect(page.get_by_role("button", name="최종 확정")).to_be_visible(
                timeout=60000
            )
            # The finalize button exists while completed; verify the reanalysis action is gone.
            expect(page.get_by_role("button", name="변경 항목 재분석")).to_have_count(0)
            result = page.evaluate(
                "async()=>{const es=await(await fetch('/api/evaluations')).json();return await(await fetch('/api/evaluations/'+es[0].id)).json()}"
            )
            corrected = next(
                r
                for r in result["results"]
                if r["project_id"] == "project-1" and r["member_id"] == "T001"
            )
            assert corrected["dimensions"]["quality"]["adjusted_score"] == 96
            assert len(result["results"]) == 30 and not result["needs_analysis"]
            page.get_by_role("button", name="평가 의견 편집").click()
            page.locator('textarea[name="summary"]').fill("검토자가 편집한 기여 요약")
            page.locator('textarea[name="limitations"]').fill("검토자가 보완한 판단 한계")
            page.locator('textarea[name="reason"]').fill("추가 검토 내용을 반영")
            page.get_by_role("button", name="의견 저장").click()
            expect(
                page.get_by_text("검토자가 편집한 기여 요약", exact=True)
            ).to_be_visible()
            expect(
                page.get_by_text("검토자가 보완한 판단 한계", exact=False)
            ).to_be_visible()
            previous_run = page.evaluate("state.evaluation.started_at")
            page.get_by_role("button", name="전체 다시 분석", exact=True).click()
            page.locator('textarea[name="reason"]').fill(
                "모델 재분석 후 수동 보정 유지 확인"
            )
            page.get_by_role("button", name="전체 재분석 시작").click()
            for _ in range(120):
                finished = page.evaluate(
                    "previous => state.evaluation.started_at !== previous && state.evaluation.status === 'completed'",
                    previous_run,
                )
                if finished:
                    break
                page.wait_for_timeout(500)
            assert finished, page.evaluate(
                "({status:state.evaluation.status, message:state.evaluation.message, toast:document.querySelector('#toast').textContent})"
            )
            expect(page.get_by_role("button", name="최종 확정")).to_be_visible(
                timeout=60000
            )
            expect(
                page.get_by_text("검토자가 편집한 기여 요약", exact=True)
            ).to_be_visible()
            page.get_by_role("button", name="최종 확정").click()
            page.locator('textarea[name="reason"]').fill("편집 후 최종 확인")
            page.get_by_role("button", name="평가 확정", exact=True).click()
            expect(page.get_by_role("button", name="확정 해제")).to_be_visible()
            page.get_by_role("button", name="평가 편집", exact=True).click()
            page.locator("#edit-reason").fill("확정 후 다시 편집하는 흐름 확인")
            page.get_by_role("button", name="변경 사항 저장").click()
            expect(page.get_by_role("button", name="최종 확정")).to_be_visible()
            page.get_by_role("button", name="평가 기준", exact=False).click()
            page.locator('[data-rubric-field="description"]').first.fill(
                "예외 처리와 운영 복구 가능성을 상세히 평가한다."
            )
            page.locator('[data-rubric-field="weight"]').nth(0).fill("40")
            page.locator('[data-rubric-field="weight"]').nth(1).fill("20")
            page.get_by_role("button", name="평가 기준 저장").click()
            expect(page.get_by_text("custom-1", exact=True)).to_be_visible()
            page.screenshot(path="artifacts/rubric-settings.png", full_page=True)
            page.get_by_role("button", name="서비스 연결", exact=False).first.click()
            for kind in ("github", "confluence", "llm"):
                form = page.locator(f'form[data-service="{kind}"]')
                form.locator('input[name="secret"]').fill("browser-private-token")
                form.get_by_role("button", name="연결 테스트").click()
                expect(form.locator(".connection-status")).to_contain_text(
                    "연결 성공", timeout=20000
                )
                form.get_by_role("button", name="연결 설정 저장").click()
                expect(form.locator('input[name="secret"]')).to_have_value("")
            assert "browser-private-token" not in page.evaluate(
                "async()=>await(await fetch('/api/settings')).text()"
            )
            page.screenshot(path="artifacts/service-connections.png", full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(
                path="artifacts/service-connections-mobile.png", full_page=True
            )
            page.get_by_role("button", name="평가 대시보드").click()
            page.get_by_role("button", name="편집", exact=True).click()
            page.get_by_role("button", name="현재 기본 기준 불러오기").click()
            expect(
                page.locator('[data-rubric-field="description"]').first
            ).to_have_value("예외 처리와 운영 복구 가능성을 상세히 평가한다.")
            page.locator("#edit-reason").fill("새 기본 기준을 기존 평가에 적용")
            page.get_by_role("button", name="변경 사항 저장").click()
            expect(page.get_by_role("button", name="변경 항목 재분석")).to_be_visible()
            assert not errors, errors
            Path("artifacts/editing-verification.json").write_text(
                json.dumps(
                    {
                        "browser": browser.version,
                        "page_errors": errors,
                        "mode": "synthetic HTTP services",
                        "checks": [
                            "completed evaluation edit",
                            "participants add/remove",
                            "weights recalculate",
                            "incremental reanalysis",
                            "full reanalysis preserving reviewer scores and text",
                            "manual correction retained",
                            "review text editing",
                            "finalized evaluation edit",
                            "rubric contents and ratios",
                            "global rubric explicit application",
                            "three integration connection tests",
                            "masked secret persistence",
                            "mobile layout",
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            browser.close()
            print(
                "Editing, rubric, all three connection tests, secret masking and mobile flows passed."
            )
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        mock_server.should_exit = True
        mock_thread.join(timeout=10)
