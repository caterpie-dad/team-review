import copy
from concurrent.futures import ThreadPoolExecutor
from fastapi.testclient import TestClient
from app.main import create_app
from app.db import Database
from app.demo import fixture_evaluation
from conftest import seed, run_demo


def test_authentication_csrf_and_cookie(config):
    app = create_app(config)
    with TestClient(app) as c:
        assert c.get("/api/members").status_code == 401
        assert c.get("/health").status_code == 200
        assert c.get("/").status_code == 200
        assert (
            c.post(
                "/api/login", json={"password": config["auth"]["password"]}
            ).status_code
            == 403
        )
        headers = {"X-Review-Request": "1", "Origin": "https://evil.test"}
        assert (
            c.post(
                "/api/login",
                json={"password": config["auth"]["password"]},
                headers=headers,
            ).status_code
            == 403
        )
        r = c.post(
            "/api/login",
            json={"password": config["auth"]["password"]},
            headers={"X-Review-Request": "1"},
        )
        assert r.status_code == 200
        assert (
            "HttpOnly" in r.headers["set-cookie"]
            and "SameSite=strict" in r.headers["set-cookie"]
        )
        assert c.get("/api/meta").status_code == 200
        assert config["auth"]["password"] not in c.get("/api/meta").text
        assert (
            c.post("/api/logout", headers={"X-Review-Request": "1"}).status_code == 200
        )
        assert c.get("/api/members").status_code == 401


def test_login_rate_limit(config):
    with TestClient(create_app(config), headers={"X-Review-Request": "1"}) as c:
        for _ in range(10):
            assert c.post("/api/login", json={"password": "wrong"}).status_code == 401
        assert (
            c.post(
                "/api/login", json={"password": config["auth"]["password"]}
            ).status_code
            == 429
        )


def test_member_duplicate_and_snapshot(client):
    ev = seed(client)
    members = client.get("/api/members").json()
    duplicate = {**members[0], "id": "OTHER"}
    assert client.post("/api/members", json=duplicate).status_code == 409
    duplicate["github_ids"] = []
    duplicate["confluence_ids"] = []
    assert client.post("/api/members", json=duplicate).status_code == 201
    inactive = {**members[0], "active": False}
    assert (
        client.put("/api/members/" + inactive["id"], json=inactive).status_code == 200
    )
    assert (
        client.post(
            "/api/evaluations/" + ev["id"] + "/start", json={"revision": ev["revision"]}
        ).status_code
        == 422
    )


def test_validation_revision_and_delete(client, config):
    ev = seed(client)
    base = "/api/evaluations/" + ev["id"]
    body = fixture_evaluation(config)
    body["revision"] = ev["revision"]
    invalid = copy.deepcopy(body)
    invalid["projects"][0]["weight"] = -1
    assert client.put(base, json=invalid).status_code == 422
    body["revision"] = 999
    assert client.put(base, json=body).status_code == 409
    body["revision"] = ev["revision"]
    body["title"] = "Updated"
    updated = client.put(base, json=body)
    assert updated.status_code == 200
    assert (
        client.request("DELETE", base, json={"revision": ev["revision"]}).status_code
        == 409
    )
    assert (
        client.request(
            "DELETE", base, json={"revision": updated.json()["revision"]}
        ).status_code
        == 200
    )
    assert client.get(base).status_code == 404


def test_single_run_atomic_and_cancel(client, monkeypatch):
    ev = seed(client)
    other = client.post("/api/evaluations/" + ev["id"] + "/clone", json={}).json()
    monkeypatch.setattr(client.app.state.worker, "launch", lambda eid: None)

    def start(e):
        return client.post(
            "/api/evaluations/" + e["id"] + "/start", json={"revision": e["revision"]}
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(start, [ev, other]))
    assert sorted(r.status_code for r in results) == [200, 409]
    running = next(r.json() for r in results if r.status_code == 200)
    assert (
        client.post(
            "/api/evaluations/" + running["id"] + "/cancel", json={}
        ).status_code
        == 200
    )
    client.app.state.worker.run(running["id"])
    cancelled = client.get("/api/evaluations/" + running["id"]).json()
    assert cancelled["status"] == "cancelled"
    assert (
        client.post(
            "/api/evaluations/" + cancelled["id"] + "/start",
            json={"revision": cancelled["revision"]},
        ).status_code
        == 200
    )
    client.app.state.worker.run(cancelled["id"])
    assert (
        client.get("/api/evaluations/" + cancelled["id"]).json()["status"]
        == "completed"
    )


def test_full_demo_adjust_finalize_export(client, mock_services):
    ev = run_demo(client)
    base = "/api/evaluations/" + ev["id"]
    assert len(ev["projects"]) == 6 and len(ev["members_snapshot"]) == 10
    assert len(ev["results"]) == 30 and len(ev["aggregate"]) == 10
    evidence = [e for items in ev["evidence"].values() for e in items]
    assert len(evidence) == 59
    assert all(e["date"].startswith("2026") for e in evidence)
    assert all(len(e["sha256"]) == 64 for e in evidence)
    assert any("미매핑" in w for w in ev["warnings"])
    assert any(m["coverage"] < 100 for m in ev["aggregate"])
    assert any("Ignore instructions" in e["content"] for e in evidence)
    assert all(
        d["score"] != 100 for r in ev["results"] for d in r["dimensions"].values()
    )
    assert not any(
        "outside" in r["path"] or "secret" in r["path"]
        for r in mock_services[1].state.requests
    )
    project = ev["projects"][0]
    mid = project["member_ids"][0]
    original = next(
        r
        for r in ev["results"]
        if r["project_id"] == project["id"] and r["member_id"] == mid
    )["dimensions"]["quality"]["score"]
    before = next(r for r in ev["aggregate"] if r["member_id"] == mid)["score"]
    body = {
        "revision": ev["revision"],
        "member_id": mid,
        "project_id": project["id"],
        "dimension": "quality",
        "score": 96,
        "reason": "근거를 다시 검토하여 설계 품질을 보정함",
        "comment": "추가 검토 의견",
    }
    r = client.patch(base + "/adjust", json=body)
    assert r.status_code == 200, r.text
    adjusted = r.json()
    d = next(
        r
        for r in adjusted["results"]
        if r["project_id"] == project["id"] and r["member_id"] == mid
    )["dimensions"]["quality"]
    assert d["score"] == original and d["adjusted_score"] == 96
    assert (
        next(r for r in adjusted["aggregate"] if r["member_id"] == mid)["score"]
        != before
    )
    assert client.patch(base + "/adjust", json=body).status_code == 409
    assert (
        client.post(
            base + "/finalize", json={"revision": adjusted["revision"]}
        ).status_code
        == 422
    )
    r = client.post(
        base + "/finalize",
        json={
            "revision": adjusted["revision"],
            "reason": "합성 데이터의 미귀속과 판정 보류를 확인함",
        },
    )
    assert r.status_code == 200
    finalized = r.json()
    body["revision"] = finalized["revision"]
    corrected = client.patch(base + "/adjust", json=body)
    assert corrected.status_code == 200 and corrected.json()["status"] == "completed"
    finalized = client.post(
        base + "/finalize",
        json={"revision": corrected.json()["revision"], "reason": "수정 결과 재확정"},
    ).json()
    export = client.get(base + "/export").json()
    assert export["status"] == "finalized" and any(
        a["action"] == "adjustment" for a in export["audit"]
    )
    assert client.get(base + "/export?format=csv").status_code == 200
    assert client.get(base + "/export?format=xml").status_code == 422
    assert (
        client.post(
            base + "/reopen", json={"revision": finalized["revision"], "reason": ""}
        ).status_code
        == 422
    )
    reopened = client.post(
        base + "/reopen",
        json={"revision": finalized["revision"], "reason": "재검토 요청"},
    ).json()
    body.update(revision=reopened["revision"], reset=True)
    restored = client.patch(base + "/adjust", json=body).json()
    d = next(
        r
        for r in restored["results"]
        if r["project_id"] == project["id"] and r["member_id"] == mid
    )["dimensions"]["quality"]
    assert "adjusted_score" not in d
    m = client.get("/api/members").json()[0]
    m["name"] = "변경된 이름"
    client.put("/api/members/" + m["id"], json=m)
    assert client.get(base).json()["members_snapshot"][0]["name"] != "변경된 이름"
    assert client.post(base + "/clone", json={}).status_code == 201
    assert (
        client.request(
            "DELETE", base, json={"revision": restored["revision"]}
        ).status_code
        == 409
    )


def test_recovery_marks_interrupted_failed(config, client, monkeypatch):
    ev = seed(client)
    monkeypatch.setattr(client.app.state.worker, "launch", lambda _: None)
    client.post(
        "/api/evaluations/" + ev["id"] + "/start", json={"revision": ev["revision"]}
    )
    reopened = Database(config["storage"]["path"])
    assert reopened.get(ev["id"])["status"] == "failed"
    assert "재시작" in reopened.get(ev["id"])["error"]


def test_live_mode_blocks_seed(config):
    config["app"]["demo"] = False
    with TestClient(create_app(config), headers={"X-Review-Request": "1"}) as c:
        c.post("/api/login", json={"password": config["auth"]["password"]})
        assert c.post("/api/demo/seed", json={}).status_code == 403


def test_second_process_cannot_reset_running(config, client, monkeypatch):
    import pytest

    ev = seed(client)
    monkeypatch.setattr(client.app.state.worker, "launch", lambda _: None)
    client.post(
        "/api/evaluations/" + ev["id"] + "/start", json={"revision": ev["revision"]}
    )
    with pytest.raises(RuntimeError, match="이미 실행 중"):
        create_app(config)
    assert client.get("/api/evaluations/" + ev["id"]).json()["status"] == "running"


def test_expired_session(client):
    with client.app.state.db.connect() as conn:
        conn.execute("UPDATE sessions SET expires=0")
    assert client.get("/api/members").status_code == 401


def test_failed_run_can_retry_without_leaking_secrets(client, monkeypatch):
    from app.connectors import Collector, SourceError
    from conftest import wait_done

    ev = seed(client)
    original = Collector.collect

    def broken(self, project):
        raise SourceError("github HTTP 401. 접근 권한을 확인하세요.")

    monkeypatch.setattr(Collector, "collect", broken)
    base = "/api/evaluations/" + ev["id"]
    assert (
        client.post(base + "/start", json={"revision": ev["revision"]}).status_code
        == 200
    )
    failed = wait_done(client, ev["id"])
    assert failed["status"] == "failed" and not failed["results"]
    assert client.get(base + "/export").status_code == 409
    monkeypatch.setattr(Collector, "collect", original)
    assert (
        client.post(base + "/start", json={"revision": failed["revision"]}).status_code
        == 200
    )
    completed = wait_done(client, ev["id"])
    assert completed["status"] == "completed"
    assert any(
        a["action"] == "previous_run" for a in client.get(base + "/audit").json()
    )


def test_csv_formula_injection(client):
    ev = run_demo(client)
    db = client.app.state.db
    with db.connect() as conn:
        record = db.get(ev["id"], conn)
        record["members_snapshot"][0]["name"] = '=HYPERLINK("https://evil.test")'
        db.save(conn, record)
    csv = client.get("/api/evaluations/" + ev["id"] + "/export?format=csv").text
    assert "'=HYPERLINK" in csv
