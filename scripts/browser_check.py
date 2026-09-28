"""Browser smoke test of an existing Docker demo workspace. Mutations are synthetic only."""

import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

password = os.getenv("DASHBOARD_PASSWORD")
if not password:
    password = next(
        line.split("=", 1)[1]
        for line in Path(".env").read_text().splitlines()
        if line.startswith("DASHBOARD_PASSWORD=")
    )
base = os.getenv("REVIEW_URL", "http://127.0.0.1:8080")
Path("artifacts").mkdir(exist_ok=True)
with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(
        viewport={"width": 1440, "height": 1100}, device_scale_factor=1
    )
    errors = []
    page.on("pageerror", lambda err: errors.append(str(err)))
    page.goto(base)
    page.get_by_label("대시보드 비밀번호").fill(password)
    page.get_by_role("button", name="워크스페이스 접속").click()
    expect(
        page.get_by_role("heading", name="기여를 이해하는 새로운 기준")
    ).to_be_visible()
    expect(page.get_by_text("DEMO WORKSPACE")).to_be_visible()
    page.screenshot(path="artifacts/dashboard.png", full_page=True)
    page.get_by_role("button", name="팀원 관리").click()
    expect(page.get_by_role("heading", name="팀원 관리")).to_be_visible()
    assert page.locator("tbody tr").count() == 10
    page.locator('[data-action="edit-member"]').first.click()
    expect(page.get_by_role("heading", name="팀원 정보 편집")).to_be_visible()
    expect(
        page.locator('[data-identity-list="github_ids"] .identity-tag')
    ).to_have_count(2)
    page.get_by_role("button", name="취소", exact=True).click()
    page.get_by_role("button", name="평가 기준", exact=False).click()
    expect(page.get_by_role("heading", name="평가 기준", exact=True)).to_be_visible()
    page.get_by_role("button", name="평가 대시보드").click()
    page.get_by_role("button", name="새 평가 만들기").click()
    page.locator("#eval-title").fill("브라우저 흐름 검증 · 2026")
    page.locator('[data-field="name"]').fill("브라우저 검증 프로젝트")
    page.locator('[data-source-entry="github_urls"]').fill(
        "https://github.demo.test/demo/project-1"
    )
    page.locator('[data-action="add-source"][data-key="github_urls"]').click()
    page.locator('[data-source-entry="confluence_urls"]').fill(
        "https://confluence.demo.test/wiki/spaces/DEMO/pages/1000"
    )
    page.locator('[data-action="add-source"][data-key="confluence_urls"]').click()
    page.locator('[data-action="toggle-member"]').last.click()
    assert page.locator(".chip.selected").count() == 9
    page.get_by_role("button", name="프로젝트 추가").click()
    page.locator('[data-field="name"]').last.fill("보조 프로젝트")
    page.locator('[data-source-entry="github_urls"]').last.fill(
        "https://github.demo.test/demo/project-2"
    )
    page.locator('[data-action="add-source"][data-key="github_urls"]').last.click()
    page.get_by_role("button", name="가중치 균등 분배").click()
    expect(page.locator("#weight-total")).to_have_text("100.00 / 100")
    slider = page.locator("[data-weight-range]").first
    slider.focus()
    slider.press("ArrowRight")
    expect(page.locator('[data-field="weight"]').first).to_have_value("51")
    expect(page.locator("#weight-total")).to_have_text("101.00 / 100")
    slider.press("ArrowLeft")
    expect(page.locator("#weight-total")).to_have_text("100.00 / 100")
    page.screenshot(path="artifacts/evaluation-settings.png", full_page=True)
    page.get_by_role("button", name="초안 저장").click()
    expect(page.get_by_role("heading", name="설정이 준비되었습니다")).to_be_visible()
    page.get_by_role("button", name="평가 시작").click()
    expect(page.get_by_role("heading", name="근거를 분석하고 있습니다")).to_be_visible(
        timeout=10000
    )
    page.screenshot(path="artifacts/progress.png", full_page=True)
    expect(page.get_by_role("button", name="최종 확정")).to_be_visible(timeout=60000)
    page.screenshot(path="artifacts/browser-evaluation-result.png", full_page=True)
    page.get_by_role("button", name="목록", exact=False).click()
    # Original 2026 demo has 6 projects and is retained as the primary result.
    page.locator('[data-action="open"]').last.click()
    expect(page.get_by_role("button", name="최종 확정")).to_be_visible()
    assert page.locator(".member-select").count() == 10
    page.locator('[data-action="adjust"]').first.click()
    page.get_by_role("spinbutton", name="평가자 점수", exact=True).fill("92")
    page.locator('textarea[name="reason"]').fill(
        "브라우저 검증: 근거와 설계를 재검토함"
    )
    page.locator('textarea[name="comment"]').fill("입력된 의견과 재집계를 확인함")
    page.get_by_role("button", name="조정 반영").click()
    expect(page.get_by_text("평가자 조정", exact=True)).to_be_visible()
    page.locator('[data-action="evidence"]').first.click()
    expect(page.get_by_role("heading", name="근거 원문")).to_be_visible()
    assert len(page.locator("dialog pre").inner_text()) > 20
    page.get_by_role("button", name="닫기", exact=True).click()
    page.get_by_role("button", name="변경 이력").click()
    expect(page.get_by_role("heading", name="변경 이력")).to_be_visible()
    expect(
        page.locator(".audit-entry").filter(has_text="브라우저 검증").first
    ).to_be_visible()
    page.get_by_role("button", name="닫기", exact=True).click()
    page.get_by_role("button", name="내보내기").click()
    with page.expect_download() as download:
        page.get_by_role("link", name="CSV 다운로드").click()
    assert download.value.suggested_filename.endswith(".csv")
    page.get_by_role("button", name="닫기", exact=True).click()
    page.get_by_role("button", name="최종 확정").click()
    page.locator('textarea[name="reason"]').fill(
        "브라우저 검증: 미귀속과 판정 보류 항목 확인"
    )
    page.get_by_role("button", name="평가 확정", exact=True).click()
    expect(page.get_by_role("button", name="확정 해제")).to_be_visible()
    assert page.locator('[data-action="adjust"]').count() == 4
    page.screenshot(path="artifacts/results-finalized.png", full_page=True)
    page.get_by_role("button", name="확정 해제").click()
    page.locator('textarea[name="reason"]').fill(
        "데모 사용자가 조정을 체험할 수 있도록 검토 상태로 복귀"
    )
    page.locator('#decision-form button[type="submit"]').click()
    expect(page.get_by_role("button", name="최종 확정")).to_be_visible()
    page.screenshot(path="artifacts/results.png", full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    page.screenshot(path="artifacts/results-mobile.png", full_page=True)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.set_viewport_size({"width": 1440, "height": 1100})
    page.get_by_role("button", name="로그아웃").click()
    expect(page.get_by_role("heading", name="평가 워크스페이스")).to_be_visible()
    assert not errors, errors
    Path("artifacts/browser-verification.json").write_text(
        json.dumps(
            {
                "browser": browser.version,
                "page_errors": errors,
                "desktop": [1440, 1100],
                "mobile": [390, 844],
                "checks": [
                    "login",
                    "10 members",
                    "multiple identities",
                    "rubric",
                    "create evaluation",
                    "select participants",
                    "weights",
                    "native weight slider updates numeric input and total",
                    "start",
                    "progress",
                    "results",
                    "adjustment",
                    "evidence",
                    "audit",
                    "download",
                    "finalize",
                    "reopen",
                    "responsive",
                    "logout",
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    browser.close()
    print("Browser workflow passed; screenshots and report saved to artifacts/.")
