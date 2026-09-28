import pytest
from pydantic import ValidationError
from app.domain import EvaluationInput, parse_source, aggregate, validate_judgment, DIMS
from app.demo import fixture_evaluation


@pytest.mark.parametrize(
    "change", ["weight", "dates", "year", "duplicate", "no_sources", "no_members"]
)
def test_invalid_evaluation(config, change):
    ev = fixture_evaluation(config)
    p = ev["projects"][0]
    if change == "weight":
        p["weight"] = 24
    if change == "dates":
        p["start"] = "2026-12-31"
        p["end"] = "2026-01-01"
    if change == "year":
        p["start"] = "2025-12-31"
    if change == "duplicate":
        ev["projects"][1]["id"] = p["id"]
    if change == "no_sources":
        p["github_urls"] = []
        p["confluence_urls"] = []
    if change == "no_members":
        p["member_ids"] = []
    with pytest.raises(ValidationError):
        EvaluationInput(**ev)


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.test/org/repo",
        "https://github.demo.test.evil.test/org/repo",
        "https://github.demo.test@evil.test/org/repo",
        "https://github.demo.test/org/repo/tree/main",
        "https://github.demo.test/org/%2e%2e",
        "file:///etc/passwd",
        "http://github.demo.test/org/repo",
    ],
)
def test_github_scope_rejects(config, url):
    with pytest.raises(ValueError):
        parse_source(url, config, "github")


@pytest.mark.parametrize(
    "url",
    [
        "https://confluence.demo.test/other/pages/12",
        "https://confluence.demo.test/wiki2/pages/12",
        "https://evil.test/wiki/pages/12",
        "https://confluence.demo.test/wiki/random",
    ],
)
def test_confluence_scope_rejects(config, url):
    with pytest.raises(ValueError):
        parse_source(url, config, "confluence")


def test_parse_allowed(config):
    assert parse_source("https://github.demo.test/org/repo.git", config, "github") == {
        "kind": "repo",
        "name": "org/repo",
    }
    assert parse_source("https://github.demo.test/orgs/demo", config, "github") == {
        "kind": "org",
        "name": "demo",
    }
    assert (
        parse_source(
            "https://confluence.demo.test/wiki/pages/viewpage.action?pageId=123",
            config,
            "confluence",
        )["name"]
        == "123"
    )


def judgment():
    return {
        "dimensions": {
            k: {
                "score": 80,
                "reason": "근거가 확인됨",
                "confidence": 0.8,
                "citations": [{"evidence_id": "e1", "quote": "actual evidence text"}],
            }
            for k in DIMS
        },
        "summary": "summary",
        "limitations": "limited",
    }


@pytest.mark.parametrize(
    "change",
    [
        "id",
        "quote",
        "nan",
        "inf",
        "bounds",
        "boolean",
        "missing_dimension",
        "no_citation",
        "confidence",
        "reason",
    ],
)
def test_llm_validation_rejects(change):
    j = judgment()
    d = j["dimensions"]["quality"]
    if change == "id":
        d["citations"][0]["evidence_id"] = "outside"
    if change == "quote":
        d["citations"][0]["quote"] = "invented text"
    if change == "nan":
        d["score"] = float("nan")
    if change == "inf":
        d["score"] = float("inf")
    if change == "bounds":
        d["score"] = 101
    if change == "boolean":
        d["score"] = True
    if change == "missing_dimension":
        del j["dimensions"]["validation"]
    if change == "no_citation":
        d["citations"] = []
    if change == "confidence":
        d["confidence"] = 2
    if change == "reason":
        d["reason"] = ""
    with pytest.raises(ValueError):
        validate_judgment(j, [{"id": "e1", "content": "actual evidence text here"}])


def test_valid_judgment_and_null():
    j = judgment()
    j["dimensions"]["quality"]["score"] = None
    assert (
        validate_judgment(j, [{"id": "e1", "content": "actual evidence text here"}])[
            "dimensions"
        ]["quality"]["score"]
        is None
    )


def test_weighted_aggregate_and_missing():
    ev = {
        "members_snapshot": [{"id": "a", "name": "A"}],
        "projects": [
            {"id": "p1", "weight": 25, "member_ids": ["a"]},
            {"id": "p2", "weight": 75, "member_ids": ["a"]},
        ],
        "results": [
            {
                "member_id": "a",
                "project_id": "p1",
                "dimensions": {k: {"score": 80} for k in DIMS},
            },
            {
                "member_id": "a",
                "project_id": "p2",
                "dimensions": {k: {"score": None} for k in DIMS},
            },
        ],
    }
    row = aggregate(ev)[0]
    assert row["score"] == 80 and row["coverage"] == 25
    for d in ev["results"][1]["dimensions"].values():
        d["score"] = 60
    assert aggregate(ev)[0]["score"] == 65
    ev["results"][0]["dimensions"]["quality"]["adjusted_score"] = 100
    assert aggregate(ev)[0]["score"] == 66.75
    ev["projects"][1]["member_ids"] = []
    ev["results"] = ev["results"][:1]
    assert aggregate(ev)[0]["score"] == 87 and aggregate(ev)[0]["coverage"] == 100


def test_historical_rubric_snapshot_is_used():
    rubric = {
        "dimensions": [
            {"id": key, "weight": 100 if key == "quality" else 0} for key in DIMS
        ]
    }
    ev = {
        "rubric": rubric,
        "members_snapshot": [{"id": "a", "name": "A"}],
        "projects": [{"id": "p", "weight": 100, "member_ids": ["a"]}],
        "results": [
            {
                "member_id": "a",
                "project_id": "p",
                "dimensions": {
                    key: {"score": 90 if key == "quality" else 10} for key in DIMS
                },
            }
        ],
    }
    assert aggregate(ev)[0]["score"] == 90


@pytest.mark.parametrize("bad", [None, [], "invalid", {"quality": []}])
def test_malformed_llm_schema(bad):
    with pytest.raises(ValueError):
        validate_judgment({"dimensions": bad}, [])
