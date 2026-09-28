import httpx
import pytest
from app.connectors import Collector, Judge, SourceError
from app.demo import fixture_members, fixture_evaluation
from app.domain import hash_text


def test_author_mapping_and_truncation(config):
    c = Collector(config, fixture_members())
    assert c.owner("github", ["dev01-work"]) == "T001"
    assert c.owner("github", ["DEV01@EXAMPLE.TEST"]) is None
    assert c.owner("github", ["dev01", "dev02"]) is None
    assert c.owner("github", ["unknown"]) is None
    c.limits["max_chars"] = 10
    p = fixture_evaluation(config)["projects"][0]
    e = c.evidence(
        "github",
        "id",
        "https://github.demo.test/x",
        "title",
        "0123456789abcdef",
        "T001",
        "2026-01-01",
        p,
    )
    assert e["content"] == "0123456789" and e["truncated"]
    assert e["sha256"] == hash_text("0123456789abcdef")
    assert (
        c.evidence("github", "id", "url", "title", "same", "T001", "2026-01-01", p)
        is None
    )
    c.close()


def test_confluence_version_attribution_and_scope(config):
    c = Collector(config, fixture_members())
    calls = []

    def fake(service, path, params=None):
        calls.append((path, params))
        if path.endswith("child/page"):
            return {"results": [], "_links": {}}
        v = (params or {}).get("version", 3)
        return {
            "title": "doc",
            "version": {
                "number": v,
                "when": "2025-12-01" if v == 1 else "2026-05-01",
                "by": {"accountId": f"cf-{v:02}"},
            },
            "body": {
                "storage": {
                    "value": "<p>baseline</p>"
                    + ("<p>author TWO addition</p>" if v >= 2 else "")
                    + (
                        '<p>author THREE addition</p><a href="https://evil.test">outside</a>'
                        if v == 3
                        else ""
                    )
                }
            },
        }

    c.get = fake
    p = fixture_evaluation(config)["projects"][0]
    evidence = c.confluence(p)
    assert len(evidence) == 2
    assert evidence[0]["member_id"] == "T002" and evidence[1]["member_id"] == "T003"
    assert "+author TWO addition" in evidence[0]["content"]
    assert "+author TWO addition" not in evidence[1]["content"]
    assert all(path.startswith("content/1000") for path, _ in calls)
    c.close()


def test_pagination_ignores_external_next(config):
    c = Collector(config, fixture_members())
    calls = []

    def fake(service, path, params):
        calls.append((path, params["start"]))
        return {
            "results": [{"id": "1"}] if params["start"] == 0 else [],
            "_links": {"next": "https://evil.test/secret"}
            if params["start"] == 0
            else {},
        }

    c.get = fake
    assert list(c.cf_list("content/1000/child/page")) == [{"id": "1"}]
    assert calls == [("content/1000/child/page", 0), ("content/1000/child/page", 100)]
    c.close()


def test_org_boundary_and_freeze(config):
    c = Collector(config, fixture_members())
    requested = []

    def lists(path, params=None):
        requested.append(path)
        if path.startswith("orgs"):
            return iter(
                [
                    {"full_name": "demo/owned", "owner": {"login": "demo"}},
                    {"full_name": "evil/foreign", "owner": {"login": "evil"}},
                    {
                        "full_name": "demo/fork",
                        "owner": {"login": "demo"},
                        "fork": True,
                    },
                    {
                        "full_name": "demo/archived",
                        "owner": {"login": "demo"},
                        "archived": True,
                    },
                    {"full_name": "evil/spoof", "owner": {"login": "demo"}},
                ]
            )
        return iter([])

    c.gh_list = lists
    p = fixture_evaluation(config)["projects"][0]
    p["github_urls"] = ["https://github.demo.test/demo"]
    assert c.github(p) == []
    assert c.scope["repos"] == ["demo/owned"]
    assert requested == ["orgs/demo/repos", "repos/demo/owned/commits"]
    c.close()


def test_redirect_and_error_secret_redaction(config):
    c = Collector(config, [])
    c.client.close()
    c.client = httpx.Client(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                302, headers={"location": "https://evil.test/secret"}
            )
        )
    )
    with pytest.raises(SourceError, match="HTTP 302"):
        c.get("github", "repos/demo/x")
    c.client.close()
    c.client = httpx.Client(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(401, text="TOKEN=secret-test-value")
        )
    )
    with pytest.raises(SourceError) as err:
        c.get("github", "repos/demo/x")
    assert "secret-test-value" not in str(err.value)
    c.close()


def test_llm_invalid_retry_and_no_evidence(config):
    j = Judge(config)
    j.client.close()
    calls = []

    def response(req):
        calls.append(req)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": '{"dimensions":{}}'}}]}
        )

    j.client = httpx.Client(transport=httpx.MockTransport(response))
    assert all(d["score"] is None for d in j.judge([])["dimensions"].values())
    assert not calls
    with pytest.raises(SourceError):
        j.judge(
            [
                {
                    "id": "1",
                    "content": "sample evidence text",
                    "source": "github",
                    "title": "x",
                }
            ]
        )
    assert len(calls) == 3
    j.close()


def test_github_pagination_and_merge_filter(config):
    c = Collector(config, fixture_members())
    calls = []

    def fake(service, path, params):
        calls.append(params["page"])
        return (
            [{"sha": str(i)} for i in range(100)]
            if params["page"] == 1
            else [{"sha": "last"}]
        )

    c.get = fake
    assert len(list(c.gh_list("repos/demo/x/commits"))) == 101
    assert calls == [1, 2]
    c.close()


def test_version_mismatch_fails_closed(config):
    c = Collector(config, fixture_members())

    def fake(service, path, params):
        return {"version": {"number": 2}, "body": {"storage": {"value": "content"}}}

    c.get = fake
    with pytest.raises(SourceError, match="버전"):
        c.confluence(fixture_evaluation(config)["projects"][0])
    c.close()


def test_multi_batch_partial_dimensions_remain_pending(config):
    from app.domain import DIMS

    config["limits"]["llm_batch_chars"] = 20
    judge = Judge(config)

    def batch(evidence):
        return {
            "dimensions": {
                key: {
                    "score": None
                    if key == "validation" and evidence[0]["id"] == "2"
                    else 80,
                    "confidence": 0.8,
                    "reason": "검증",
                    "citations": [],
                }
                for key in DIMS
            },
            "summary": "요약",
            "limitations": "제한",
        }

    judge.batch = batch
    result = judge.judge(
        [{"id": "1", "content": "a" * 20}, {"id": "2", "content": "b" * 20}]
    )
    assert result["batches"] == 2
    assert result["dimensions"]["quality"]["score"] == 80
    assert result["dimensions"]["validation"]["score"] is None
    judge.close()
