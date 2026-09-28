import copy
import json

import httpx
import pytest

from app.connectors import Collector, Cancelled
from app.demo import fixture_evaluation, fixture_members
from app.source_errors import ResourceError, request_error
from conftest import seed, wait_done


def intercept(monkeypatch, handler):
    original = httpx.Client.send

    def send(self, request, *args, **kwargs):
        response = handler(request)
        return (
            response
            if response is not None
            else original(self, request, *args, **kwargs)
        )

    monkeypatch.setattr(httpx.Client, "send", send)


@pytest.mark.parametrize(
    "status,body,code",
    [
        (409, {"message": "Git Repository is empty."}, "empty_repository"),
        (409, {"message": "Other conflict TOKEN=private-value"}, "conflict"),
        (401, {"message": "Bad credentials TOKEN=private-value"}, "unauthorized"),
        (403, {"message": "API rate limit exceeded"}, "rate_limited"),
        (403, {"message": "Forbidden TOKEN=private-value"}, "forbidden"),
        (404, {"message": "Not found"}, "not_found"),
        (429, {}, "rate_limited"),
        (503, {}, "server_error"),
        (302, {}, "redirect"),
    ],
)
def test_safe_diagnostic_classification(config, status, body, code):
    config["github"]["token"] = "sensitive-token"
    config["github"]["api_url"] = (
        "https://user:password@api.test/base?secret=sensitive-token"
    )
    error = request_error(
        config,
        "github",
        "repos/org/repo/commits",
        {"page": 2, "token": "sensitive-token"},
        response=httpx.Response(status, json=body),
    )
    assert isinstance(error, ResourceError)
    assert error.detail["code"] == code
    assert error.detail["url"] == "https://api.test/base/repos/org/repo/commits?page=2"
    assert error.detail["reason"] and error.detail["hint"]
    rendered = str(error) + json.dumps(error.detail)
    assert all(
        secret not in rendered
        for secret in ("sensitive-token", "private-value", "password")
    )


def test_mixed_failures_finish_and_mark_partial_results(client, monkeypatch):
    ev = seed(client)
    project = copy.deepcopy(ev["projects"][0])
    project["weight"] = 100
    project["github_urls"] = [
        "https://github.demo.test/demo/empty",
        "https://github.demo.test/demo/private",
        *project["github_urls"],
    ]
    base = "/api/evaluations/" + ev["id"]
    saved = client.put(
        base,
        json={
            "title": ev["title"],
            "year": ev["year"],
            "projects": [project],
            "revision": ev["revision"],
        },
    ).json()
    captured = []

    def failure(request):
        path = request.url.path
        if path == "/github/repos/demo/empty/commits":
            return httpx.Response(409, json={"message": "Git Repository is empty."})
        if path == "/github/repos/demo/private/commits":
            return httpx.Response(403, json={"message": "TOKEN=private-value"})
        if path == "/confluence/content/1003":
            return httpx.Response(404, json={"message": "TOKEN=private-value"})
        if path == "/llm/chat/completions":
            captured.extend(
                json.loads(json.loads(request.content)["messages"][1]["content"])[
                    "evidence"
                ]
            )

    intercept(monkeypatch, failure)
    assert (
        client.post(base + "/start", json={"revision": saved["revision"]}).status_code
        == 200
    )
    done = wait_done(client, ev["id"])
    assert done["status"] == "completed", done.get("error")
    scope = done["scopes"][project["id"]]
    assert {i["status"] for i in scope["collection_issues"]} == {403, 404, 409}
    assert scope["collection_incomplete"]
    evidence = done["evidence"][project["id"]]
    assert any(e["source"] == "github" for e in evidence)
    assert any(e["source"] == "confluence" for e in evidence)
    assert not any(e["metadata"].get("page_id") == "1003" for e in evidence)
    assert {e["id"] for e in captured} == {e["id"] for e in evidence if e["member_id"]}
    assert all(
        row["provisional"] and row["collection_incomplete"] for row in done["aggregate"]
    )
    assert all(row["score"] is not None for row in done["aggregate"])
    assert all("잠정" in r["limitations"] for r in done["results"])
    exported = client.get(base + "/export?format=json")
    assert exported.status_code == 200 and "private-value" not in exported.text
    assert "collection_issues" in exported.text
    csv = client.get(base + "/export?format=csv").text
    assert "collection_incomplete" in csv and "True" in csv
    # Recovery replaces current-run diagnostics, preserving the previous run in audit.
    monkeypatch.undo()
    repaired = copy.deepcopy(project)
    repaired["github_urls"] = repaired["github_urls"][-1:]
    edited = client.put(
        base,
        json={
            "title": ev["title"],
            "year": ev["year"],
            "projects": [repaired],
            "revision": done["revision"],
            "reason": "접근 가능한 경로로 복구",
        },
    ).json()
    assert (
        client.post(
            base + "/start",
            json={
                "revision": edited["revision"],
                "force": True,
                "reason": "수집 오류 복구 확인",
            },
        ).status_code
        == 200
    )
    recovered = wait_done(client, ev["id"])
    assert recovered["status"] == "completed"
    assert not any(s.get("collection_issues") for s in recovered["scopes"].values())
    assert not any(r["collection_incomplete"] for r in recovered["aggregate"])
    assert not any("수집 제외" in w for w in recovered["warnings"])


def test_all_unavailable_finishes_without_llm_or_zero_scores(client, monkeypatch):
    ev = seed(client)
    llm_calls = []

    def unavailable(request):
        if request.url.path.startswith(("/github/", "/confluence/")):
            return httpx.Response(401, json={"message": "Bad credentials"})
        if request.url.path == "/llm/chat/completions":
            llm_calls.append(request)

    intercept(monkeypatch, unavailable)
    assert (
        client.post(
            f"/api/evaluations/{ev['id']}/start", json={"revision": ev["revision"]}
        ).status_code
        == 200
    )
    done = wait_done(client, ev["id"])
    assert done["status"] == "completed", done.get("error")
    assert not llm_calls
    assert len(done["results"]) == 30
    assert all(
        r["score"] is None and r["coverage"] == 0 and r["provisional"]
        for r in done["aggregate"]
    )


def test_skip_commit_and_late_listing_page_keeps_other_evidence(config, monkeypatch):
    collector = Collector(config, fixture_members())
    project = fixture_evaluation(config)["projects"][0]
    original = collector.get
    skipped = []

    def get(service, path, params=None):
        if service == "github" and path.endswith("/commits"):
            if params["page"] == 2:
                raise request_error(
                    config, service, path, params, response=httpx.Response(403)
                )
            commits = original(service, path, params)
            skipped.append(commits[0]["sha"])
            return commits + [commits[-1]] * (100 - len(commits))
        if service == "github" and path.endswith("/" + skipped[0]):
            raise request_error(
                config, service, path, params, response=httpx.Response(404)
            )
        return original(service, path, params)

    collector.get = get
    try:
        items, _, scope = collector.collect(project)
        assert any(e["source"] == "github" for e in items)
        assert not any(e["metadata"].get("sha") == skipped[0] for e in items)
        assert {i["status"] for i in scope["collection_issues"]} == {403, 404}
        assert any("page=2" in i["url"] for i in scope["collection_issues"])
    finally:
        collector.close()


def test_missing_document_version_does_not_misattribute_later_content(config):
    collector = Collector(config, fixture_members())
    project = fixture_evaluation(config)["projects"][0]
    project["github_urls"] = []

    def get(service, path, params=None):
        if path.endswith("child/page"):
            return {"results": []}
        version = (params or {}).get("version", 4)
        if version == 2:
            raise request_error(
                config, service, path, params, response=httpx.Response(404)
            )
        return {
            "version": {
                "number": version,
                "when": "2026-04-01",
                "by": {"accountId": "cf-01"},
            },
            "body": {
                "storage": {
                    "value": "<p>baseline</p>"
                    + ("<p>unobserved change</p>" if version >= 3 else "")
                    + ("<p>verified new change</p>" if version == 4 else "")
                }
            },
        }

    collector.get = get
    try:
        items, _, scope = collector.collect(project)
        assert [e["metadata"]["version"] for e in items] == [1, 4]
        assert "+unobserved change" not in items[-1]["content"]
        assert "+verified new change" in items[-1]["content"]
        assert scope["collection_incomplete"]
    finally:
        collector.close()


@pytest.mark.parametrize(
    "error", [httpx.ReadTimeout("secret"), httpx.ConnectError("secret")]
)
def test_network_retry_diagnostics_and_cancellation(config, monkeypatch, error):
    monkeypatch.setattr("app.connectors.time.sleep", lambda _: None)
    collector = Collector(config, fixture_members())
    collector.client.close()
    calls = []

    def fail(request):
        calls.append(request)
        raise error

    collector.client = httpx.Client(transport=httpx.MockTransport(fail))
    try:
        with pytest.raises(ResourceError) as exc:
            collector.get("github", "repos/demo/x/commits")
        assert exc.value.detail["attempts"] == 3 and len(calls) == 3
        assert "secret" not in str(exc.value)
        collector.check = lambda: (_ for _ in ()).throw(Cancelled())
        with pytest.raises(Cancelled):
            collector.collect(fixture_evaluation(config)["projects"][0])
    finally:
        collector.close()


@pytest.mark.parametrize(
    "status,body,incomplete",
    [
        (409, {"message": "Git Repository is empty."}, True),
        (409, {"message": "Conflict"}, True),
        (503, {}, True),
        (200, None, True),
    ],
)
def test_failed_org_does_not_hide_explicit_repo(
    config, monkeypatch, status, body, incomplete
):
    monkeypatch.setattr("app.connectors.time.sleep", lambda _: None)
    collector = Collector(config, fixture_members())
    project = fixture_evaluation(config)["projects"][0]
    project["github_urls"].insert(0, "https://github.demo.test/unavailable")
    calls = []

    def unavailable(request):
        if request.url.path == "/github/orgs/unavailable/repos":
            calls.append(request)
            return (
                httpx.Response(status, json=body)
                if body is not None
                else httpx.Response(200, text="private-html-response")
            )

    intercept(monkeypatch, unavailable)
    try:
        items, notes, scope = collector.collect(project)
        assert scope["collection_incomplete"] is incomplete
        assert any(e["source"] == "github" for e in items)
        assert any(e["source"] == "confluence" for e in items)
        assert len(calls) == (3 if status == 503 else 1)
        assert "private-html-response" not in json.dumps(scope) + str(notes)
    finally:
        collector.close()


def test_confirmed_empty_repo_is_not_a_missing_access_scope(config, monkeypatch):
    collector = Collector(config, fixture_members())
    project = fixture_evaluation(config)["projects"][0]
    project["github_urls"].insert(0, "https://github.demo.test/demo/empty")
    intercept(
        monkeypatch,
        lambda request: httpx.Response(
            409, json={"message": "Git Repository is empty."}
        )
        if request.url.path == "/github/repos/demo/empty/commits"
        else None,
    )
    try:
        items, _, scope = collector.collect(project)
        assert items and scope["collection_incomplete"] is False
        assert scope["collection_issues"][0]["code"] == "empty_repository"
    finally:
        collector.close()


def test_failed_child_listing_does_not_hide_other_roots(config, monkeypatch):
    collector = Collector(config, fixture_members())
    projects = fixture_evaluation(config)["projects"]
    project = projects[0]
    project["confluence_urls"] += projects[1]["confluence_urls"]
    project["member_ids"] = [m["id"] for m in fixture_members()]
    intercept(
        monkeypatch,
        lambda request: httpx.Response(403)
        if request.url.path == "/confluence/content/1000/child/page"
        else None,
    )
    try:
        items, _, scope = collector.collect(project)
        assert scope["collection_incomplete"]
        assert scope["collection_issues"][0]["stage"] == "Confluence 하위 페이지 목록"
        assert len([e for e in items if e["source"] == "confluence"]) == 5
        assert "1103" in scope["confluence_pages"]
    finally:
        collector.close()
