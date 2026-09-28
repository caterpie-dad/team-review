import copy
import json
import stat

import httpx
import pytest
from app.connectors import Judge
from app.domain import RUBRIC, DIMS
from app.settings import SettingsStore, test_connection as connection_test
from conftest import seed, run_demo, wait_done


def edit_body(ev):
    return {
        k: copy.deepcopy(ev[k]) for k in ("title", "year", "projects", "revision")
    } | {"reason": "사후 검토에 따른 변경"}


def service_body(settings, kind):
    service = settings["services"][kind]
    return {
        k: service[k]
        for k in ("api_url", "web_url", "auth", "username", "model", "json_mode")
    } | {"revision": settings["revision"]}


def test_completed_project_and_participant_edits_preserve_corrections(client):
    ev = run_demo(client)
    base = "/api/evaluations/" + ev["id"]
    corrected = client.patch(
        base + "/adjust",
        json={
            "revision": ev["revision"],
            "project_id": "project-2",
            "member_id": "T003",
            "dimension": "quality",
            "score": 99,
            "reason": "기존 보정 유지 확인",
        },
    ).json()
    old_result = next(
        r
        for r in corrected["results"]
        if r["project_id"] == "project-2" and r["member_id"] == "T003"
    )
    body = edit_body(corrected)
    body["projects"][0]["member_ids"].remove("T001")
    body["projects"][0]["member_ids"].append("T010")
    body["projects"][0]["weight"] = 30
    body["projects"][1]["weight"] = 15
    edited = client.put(base, json=body)
    assert edited.status_code == 200, edited.text
    edited = edited.json()
    assert edited["needs_analysis"]
    assert not any(
        r["project_id"] == "project-1" and r["member_id"] == "T001"
        for r in edited["results"]
    )
    assert sum(bool(r.get("analysis_pending")) for r in edited["results"]) == 1
    assert (
        next(
            r
            for r in edited["results"]
            if r["project_id"] == "project-2" and r["member_id"] == "T003"
        )
        == old_result
    )
    assert (
        client.post(
            base + "/finalize",
            json={"revision": edited["revision"], "reason": "아직 재분석 전"},
        ).status_code
        == 409
    )
    assert (
        client.post(base + "/start", json={"revision": edited["revision"]}).status_code
        == 200
    )
    done = wait_done(client, ev["id"])
    assert done["status"] == "completed"
    assert not done["needs_analysis"]
    assert len(done["results"]) == 30
    assert (
        next(
            r
            for r in done["results"]
            if r["project_id"] == "project-2" and r["member_id"] == "T003"
        )
        == old_result
    )
    assert any(
        a["action"] == "configuration_edited" and a["data"]["before"]["results"]
        for a in client.get(base + "/audit").json()
    )
    # Removing a whole project never leaves it in the current aggregate/evidence scope.
    body = edit_body(done)
    body["projects"].pop()
    body["projects"][0]["weight"] += 10
    deleted = client.put(base, json=body).json()
    assert not deleted["needs_analysis"]
    current = client.get(base).json()
    assert "project-6" not in current["evidence"]
    assert all(r["project_id"] != "project-6" for r in current["results"])
    # A new project is editable and has its own pending pairs.
    body = edit_body(current)
    new = copy.deepcopy(body["projects"][0])
    new.update(id="new-project", name="추가 프로젝트", weight=10)
    body["projects"][0]["weight"] -= 10
    body["projects"].append(new)
    added = client.put(base, json=body).json()
    assert sum(bool(r.get("analysis_pending")) for r in added["results"]) == len(
        new["member_ids"]
    )


def test_weight_only_recalculates_and_finalized_can_edit(client):
    ev = run_demo(client)
    base = "/api/evaluations/" + ev["id"]
    finalized = client.post(
        base + "/finalize",
        json={"revision": ev["revision"], "reason": "원래 결과 확정"},
    ).json()
    body = edit_body(finalized)
    body["title"] = "수정된 평가"
    body["projects"][0]["weight"] = 30
    body["projects"][1]["weight"] = 15
    edited = client.put(base, json=body).json()
    assert edited["status"] == "completed" and not edited["needs_analysis"]
    assert edited["results"] == ev["results"]
    assert client.get(base).json()["aggregate"] != ev["aggregate"]
    assert client.put(base, json=body).status_code == 409
    assert (
        client.patch(
            base + "/review-text",
            json={
                "revision": edited["revision"],
                "project_id": "project-1",
                "member_id": "T001",
                "summary": "검토 후 수정한 평가 의견",
                "limitations": "운영 검증 범위 참고",
                "reason": "평가 의견 보완",
            },
        ).status_code
        == 200
    )
    updated = client.get(base).json()
    r = next(
        r
        for r in updated["results"]
        if r["project_id"] == "project-1" and r["member_id"] == "T001"
    )
    assert (
        r["adjusted_summary"] == "검토 후 수정한 평가 의견"
        and r["summary"] != r["adjusted_summary"]
    )


def test_rubric_weights_reaggregate_descriptions_require_analysis(client):
    ev = run_demo(client)
    base = "/api/evaluations/" + ev["id"]
    body = edit_body(ev)
    body["rubric"] = copy.deepcopy(ev["rubric"])
    body["rubric"]["dimensions"][0]["weight"] = 40
    body["rubric"]["dimensions"][1]["weight"] = 20
    updated = client.put(base, json=body).json()
    assert not updated["needs_analysis"]
    assert client.get(base).json()["aggregate"] != ev["aggregate"]
    body = edit_body(updated)
    body["rubric"] = updated["rubric"]
    body["rubric"]["dimensions"][0]["description"] = (
        "오류 처리와 설계 근거를 우선 검토한다."
    )
    changed = client.put(base, json=body).json()
    assert changed["needs_analysis"] and all(
        r.get("analysis_pending") for r in changed["results"]
    )
    assert all(r["score"] is None for r in client.get(base).json()["aggregate"])


def test_scope_and_identity_changes_invalidate_only_affected(client):
    ev = run_demo(client)
    base = "/api/evaluations/" + ev["id"]
    body = edit_body(ev)
    body["projects"][0]["start"] = "2026-02-01"
    changed = client.put(base, json=body).json()
    assert sum(bool(r.get("analysis_pending")) for r in changed["results"]) == 5
    assert "project-1" not in changed["evidence"]
    member = client.get("/api/members").json()[0]
    member["github_ids"].append("new-github-alias")
    client.put("/api/members/" + member["id"], json=member)
    body = edit_body(changed)
    body["refresh_members"] = True
    refreshed = client.put(base, json=body).json()
    assert all(
        r.get("analysis_pending")
        for r in refreshed["results"]
        if r["member_id"] == "T001"
    )


def test_settings_persistence_masking_connections_and_conflicts(client, config):
    settings = client.get("/api/settings").json()
    for kind in ("github", "confluence", "llm"):
        body = service_body(settings, kind)
        body["secret"] = "never-return-this-token"
        result = client.post("/api/settings/services/" + kind + "/test", json=body)
        assert result.status_code == 200 and result.json()["ok"], result.text
        saved = client.put("/api/settings/services/" + kind, json=body)
        assert saved.status_code == 200 and "never-return-this-token" not in saved.text
        settings = saved.json()
        assert settings["services"][kind]["secret_set"]
    store = client.app.state.settings
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    reopened = SettingsStore(config, store.path)
    assert reopened.effective()["llm"]["key"] == "never-return-this-token"
    body = service_body(settings, "llm")
    body["secret"] = ""
    settings = client.put("/api/settings/services/llm", json=body).json()
    assert store.effective()["llm"]["key"] == "never-return-this-token"
    assert client.put("/api/settings/services/llm", json=body).status_code == 409
    body = service_body(settings, "llm")
    body["clear_secret"] = True
    assert not client.put("/api/settings/services/llm", json=body).json()["services"][
        "llm"
    ]["secret_set"]
    assert store.effective()["llm"]["key"] == ""
    assert "never-return-this-token" not in client.get("/api/meta").text
    with client.app.state.db.connect() as conn:
        assert all(
            "never-return-this-token" not in r["data"]
            for r in conn.execute("SELECT data FROM audit")
        )


def test_global_rubric_explicit_application_and_validation(client):
    ev = run_demo(client)
    base = "/api/evaluations/" + ev["id"]
    settings = client.get("/api/settings").json()
    rubric = copy.deepcopy(settings["rubric"])
    rubric["dimensions"][0]["description"] = "새로운 품질 평가 내용입니다."
    body = {
        "revision": settings["revision"],
        "rubric": rubric,
        "level_weights": {"CL2": 1, "CL3": 0.85, "CL4": 0.7},
    }
    invalid = copy.deepcopy(body)
    invalid["rubric"]["dimensions"][0]["weight"] = 45
    assert client.put("/api/settings/rubric", json=invalid).status_code == 422
    saved = client.put("/api/settings/rubric", json=body)
    assert saved.status_code == 200
    assert client.get(base).json()["rubric"] == ev["rubric"]
    edit = edit_body(ev)
    edit["use_current_rubric"] = True
    updated = client.put(base, json=edit).json()
    assert updated["needs_analysis"]
    assert (
        updated["rubric"]["dimensions"][0]["description"]
        == rubric["dimensions"][0]["description"]
    )
    assert updated["level_policy"]["weights"]["CL3"] == 0.85


def test_running_settings_locked_and_invalid_service(client, monkeypatch):
    settings = client.get("/api/settings").json()
    body = service_body(settings, "github")
    invalid = {**body, "api_url": "file:///etc/passwd", "secret": "do-not-echo-secret"}
    response = client.put("/api/settings/services/github", json=invalid)
    assert response.status_code == 422 and "do-not-echo-secret" not in response.text
    assert (
        client.post("/api/settings/services/unknown/test", json=body).status_code == 404
    )
    ev = seed(client)
    monkeypatch.setattr(client.app.state.worker, "launch", lambda eid: None)
    client.post(
        "/api/evaluations/" + ev["id"] + "/start", json={"revision": ev["revision"]}
    )
    assert client.put("/api/settings/services/github", json=body).status_code == 409
    assert (
        client.put(
            "/api/evaluations/" + ev["id"],
            json=edit_body(client.get("/api/evaluations/" + ev["id"]).json()),
        ).status_code
        == 409
    )


@pytest.mark.parametrize(
    "status,payload",
    [
        (401, {"error": "SENSITIVE"}),
        (302, {}),
        (200, {"unexpected": "SENSITIVE"}),
        (200, {"type": "anonymous"}),
    ],
)
def test_connection_errors_are_sanitized(monkeypatch, status, payload):
    original = httpx.Client
    transport = httpx.MockTransport(
        lambda req: httpx.Response(
            status, json=payload, headers={"location": "https://outside.test"}
        )
    )
    monkeypatch.setattr(
        "app.settings.httpx.Client",
        lambda **kwargs: original(transport=transport, **kwargs),
    )
    result = connection_test(
        "confluence", {"api_url": "https://configured.test", "token": "SENSITIVE"}
    )
    assert result["ok"] is False
    assert "SENSITIVE" not in json.dumps(result)


def test_judge_sends_selected_rubric(config):
    rubric = copy.deepcopy(RUBRIC)
    rubric["dimensions"][0]["description"] = "변경된 실제 평가 기준"
    calls = []

    def handle(request):
        calls.append(json.loads(request.content))
        judgment = {
            "dimensions": {
                key: {
                    "score": 80,
                    "confidence": 0.8,
                    "reason": "검증 내용",
                    "citations": [
                        {"evidence_id": "e1", "quote": "actual evidence content"}
                    ],
                }
                for key in DIMS
            }
        }
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(judgment)}}]}
        )

    judge = Judge(config, rubric=rubric)
    judge.client.close()
    judge.client = httpx.Client(transport=httpx.MockTransport(handle))
    judge.judge(
        [
            {
                "id": "e1",
                "source": "github",
                "title": "code",
                "content": "actual evidence content",
            }
        ]
    )
    assert json.loads(calls[0]["messages"][1]["content"])["rubric"] == rubric
    judge.close()


def test_full_reanalysis_preserves_reviewer_overrides(client):
    ev = run_demo(client)
    base = "/api/evaluations/" + ev["id"]
    corrected = client.patch(
        base + "/adjust",
        json={
            "revision": ev["revision"],
            "project_id": "project-1",
            "member_id": "T001",
            "dimension": "quality",
            "score": 97,
            "reason": "전체 재분석에서도 유지할 평가자 보정",
        },
    ).json()
    assert (
        client.post(
            base + "/start", json={"revision": corrected["revision"], "force": True}
        ).status_code
        == 422
    )
    assert (
        client.post(
            base + "/start",
            json={
                "revision": corrected["revision"],
                "force": True,
                "reason": "LLM 전체 재분석 검증",
            },
        ).status_code
        == 200
    )
    done = wait_done(client, ev["id"])
    assert done["status"] == "completed" and not done["needs_analysis"]
    assert len(done["results"]) == 30
    result = next(
        r
        for r in done["results"]
        if r["project_id"] == "project-1" and r["member_id"] == "T001"
    )
    assert result["dimensions"]["quality"]["adjusted_score"] == 97
    assert result["model"] == client.app.state.settings.effective()["llm"]["model"]
    assert "reanalysis_overrides" not in done
    assert any(
        a["action"] == "full_reanalysis_requested"
        for a in client.get(base + "/audit").json()
    )
