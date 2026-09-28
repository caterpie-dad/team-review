import copy

from conftest import seed, wait_done


def test_incomplete_draft_round_trip_and_start_blocked(client, monkeypatch):
    launched = []
    monkeypatch.setattr(client.app.state.worker, "launch", launched.append)
    body = {
        "title": " ",
        "year": 2026,
        "projects": [
            {"id": "one", "name": "", "start": "", "end": "", "weight": 30},
            {"id": "two", "name": "", "weight": 20},
        ],
    }
    response = client.post("/api/evaluations", json=body)
    assert response.status_code == 201, response.text
    ev = response.json()
    base = "/api/evaluations/" + ev["id"]
    ev = client.get(base).json()
    assert ev["title"] == "" and len(ev["projects"]) == 2
    assert ev["projects"][0]["start"] is None
    assert any("프로젝트 · 1번 · 이름" in message for message in ev["draft_issues"])
    assert any("프로젝트 · 2번 · 이름" in message for message in ev["draft_issues"])
    response = client.post(base + "/start", json={"revision": ev["revision"]})
    assert response.status_code == 422
    assert "String should" not in response.text
    assert "평가 이름: 1자 이상 입력하세요." in response.text
    assert launched == []
    assert client.get(base).json()["status"] == "draft"
    body.update(title="작업 중인 평가", revision=ev["revision"])
    updated = client.put(base, json=body)
    assert updated.status_code == 200
    assert client.get(base).json()["projects"][0]["name"] == ""
    assert client.put(base, json=body).status_code == 409
    cloned = client.post(base + "/clone", json={})
    assert cloned.status_code == 201
    assert cloned.json()["projects"] == updated.json()["projects"]


def test_draft_can_be_completed_and_run(client):
    complete = seed(client)
    ev = client.post("/api/evaluations", json={"year": 2026}).json()
    base = "/api/evaluations/" + ev["id"]
    assert client.get(base).json()["draft_issues"]
    body = {key: complete[key] for key in ("title", "year", "projects")}
    body["revision"] = ev["revision"]
    updated = client.put(base, json=body)
    assert updated.status_code == 200, updated.text
    assert client.get(base).json()["draft_issues"] == []
    assert (
        client.post(
            base + "/start", json={"revision": updated.json()["revision"]}
        ).status_code
        == 200
    )
    done = wait_done(client, ev["id"])
    assert done["status"] == "completed" and len(done["results"]) == 30
    body.update(title="", revision=done["revision"], reason="빈 이름 변경 시도")
    assert client.put(base, json=body).status_code == 422
    assert client.get(base).json()["title"] == complete["title"]


def test_draft_defers_scope_and_weight_validation_until_start(client, monkeypatch):
    ev = seed(client)
    base = "/api/evaluations/" + ev["id"]
    body = {key: copy.deepcopy(ev[key]) for key in ("title", "year", "projects")}
    body["projects"][0]["weight"] = 26
    body["revision"] = ev["revision"]
    response = client.put(base, json=body)
    assert response.status_code == 200
    ev = response.json()
    rejected = client.post(base + "/start", json={"revision": ev["revision"]})
    assert rejected.status_code == 422 and "합은 100" in rejected.text
    body["projects"][0]["weight"] = 25
    body["projects"][0]["github_urls"] = ["https://outside.test/org/repo"]
    body["revision"] = ev["revision"]
    response = client.put(base, json=body)
    assert response.status_code == 200
    launched = []
    monkeypatch.setattr(client.app.state.worker, "launch", launched.append)
    rejected = client.post(
        base + "/start", json={"revision": response.json()["revision"]}
    )
    assert (
        rejected.status_code == 422
        and "프로젝트 1번 · github 근거 링크" in rejected.text
    )
    assert launched == []
    assert client.get(base).json()["status"] == "draft"
