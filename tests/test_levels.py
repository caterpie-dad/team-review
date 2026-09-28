import copy
import json
import pytest
from pydantic import ValidationError
from app.db import Database
from app.domain import Member, DIMS, aggregate, career_at, career_level, level_policy
from app.demo import fixture_members
from conftest import seed, run_demo


@pytest.mark.parametrize(
    "years, expected",
    [(1, "CL2"), (8, "CL2"), (9, "CL3"), (16, "CL3"), (17, "CL4"), (35, "CL4")],
)
def test_level_boundaries(years, expected):
    assert career_level(years) == expected


def sample_evaluation(score=80):
    members = [
        dict(m, career_years=years)
        for m, years in zip(fixture_members()[:3], [8, 9, 17])
    ]
    return {
        "year": 2026,
        "members_snapshot": members,
        "level_policy": level_policy({}),
        "projects": [
            {"id": "p", "weight": 100, "member_ids": [m["id"] for m in members]}
        ],
        "results": [
            {
                "member_id": m["id"],
                "project_id": "p",
                "dimensions": {key: {"score": score} for key in DIMS},
            }
            for m in members
        ],
    }


@pytest.mark.parametrize(
    "raw, expected",
    [
        (80, [80, 72, 64]),
        (100, [100, 90, 80]),
        (None, [None, None, None]),
        (0, [0, 0, 0]),
    ],
)
def test_same_output_different_expectations(raw, expected):
    rows = aggregate(sample_evaluation(raw))
    assert [r["score"] for r in rows] == expected
    assert all(r["raw_score"] == raw for r in rows)
    assert all(r["projects"][0]["score"] == raw for r in rows)


def test_adjustment_and_year_rollover():
    ev = sample_evaluation()
    ev["results"][1]["dimensions"]["quality"]["adjusted_score"] = 100
    rows = aggregate(ev)
    assert rows[1]["raw_score"] == 87 and rows[1]["score"] == 78.3
    ev["year"] = 2027
    assert aggregate(ev)[0]["level"] == "CL3"
    assert aggregate(ev)[0]["score"] == 72


def test_legacy_scores_do_not_change():
    ev = sample_evaluation()
    del ev["level_policy"]
    for member in ev["members_snapshot"]:
        member.pop("career_years")
        member.pop("career_reference_year")
    assert all(r["score"] == 80 and not r["level_applied"] for r in aggregate(ev))


@pytest.mark.parametrize(
    "weights",
    [
        {"CL2": 1, "CL3": 1, "CL4": 0.8},
        {"CL2": 1.2, "CL3": 1, "CL4": 0.9},
        {"CL2": 1, "CL3": 0.8, "CL4": 0},
        {"CL2": float("nan"), "CL3": 0.9, "CL4": 0.8},
        {"CL2": 1},
    ],
)
def test_invalid_policy(weights):
    with pytest.raises(ValueError):
        level_policy({"evaluation": {"level_weights": weights}})


def test_member_validation_and_retired_email():
    member = fixture_members()[0]
    for change in [
        {"career_years": 0},
        {"career_years": 8.5},
        {"github_ids": ["a,b"]},
        {"git_emails": ["a@example.test"]},
    ]:
        with pytest.raises(ValidationError):
            Member(**(member | change))
    with pytest.raises(ValueError):
        career_at(member, 2025)


def test_policy_snapshot_and_export(client, config):
    ev = run_demo(client)
    assert ev["level_policy"]["weights"] == {"CL2": 1, "CL3": 0.9, "CL4": 0.8}
    before = copy.deepcopy(ev["aggregate"])
    config["evaluation"]["level_weights"]["CL3"] = 0.85
    member = client.get("/api/members").json()[0]
    member["career_years"] = 25
    assert client.put("/api/members/" + member["id"], json=member).status_code == 200
    base = "/api/evaluations/" + ev["id"]
    assert client.get(base).json()["aggregate"] == before
    csv = client.get(base + "/export?format=csv").text
    assert "raw_score,career_years,level,level_weight" in csv
    assert "CL3" in csv and "CL4" in csv
    assert "git_emails" not in client.get(base + "/export").text


def test_existing_member_missing_career_blocks_start(client):
    ev = seed(client)
    db = client.app.state.db
    with db.connect() as conn:
        member = db.members(conn)[0]
        member.pop("career_years")
        conn.execute(
            "UPDATE members SET data=? WHERE id=?", (json.dumps(member), member["id"])
        )
    response = client.post(
        "/api/evaluations/" + ev["id"] + "/start", json={"revision": ev["revision"]}
    )
    assert response.status_code == 422 and "연차" in response.text


def test_email_retirement_migration_preserves_scores(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    db = Database(path)
    ev = sample_evaluation()
    ev.update(id="old", status="completed", revision=1)
    del ev["level_policy"]
    for m in ev["members_snapshot"]:
        m["git_emails"] = ["old@example.test"]
    with db.connect() as conn:
        conn.execute("PRAGMA user_version=1")
        conn.execute(
            "INSERT INTO members VALUES (?,?)",
            ("T001", json.dumps(ev["members_snapshot"][0])),
        )
        db.save(conn, ev)
        db.audit(conn, "old", "previous_run", ev)
    db = Database(path)
    assert "git_emails" not in json.dumps(db.members())
    assert "git_emails" not in json.dumps(db.get("old"))
    assert all(r["score"] == 80 for r in aggregate(db.get("old")))
    with db.connect() as conn:
        assert "git_emails" not in conn.execute("SELECT data FROM audit").fetchone()[0]
