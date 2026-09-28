import hashlib
import json
from pathlib import Path


def test_legacy_member_lists_are_optional_and_editable(client):
    record = {
        "id": "legacy",
        "name": "기존 팀원",
        "active": True,
        "career_years": 5,
        "career_reference_year": 2026,
        "confluence_ids": None,
    }
    db = client.app.state.db
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO members VALUES(?,?)", (record["id"], json.dumps(record))
        )
    members = client.get("/api/members").json()
    assert members == [{**record, "github_ids": [], "confluence_ids": []}]
    # Reading the old data must not rewrite the stored source record.
    with db.connect() as conn:
        assert (
            json.loads(conn.execute("SELECT data FROM members").fetchone()[0]) == record
        )
    response = client.post(
        "/api/members",
        json={
            "id": "new",
            "name": "새 팀원",
            "career_years": 1,
            "career_reference_year": 2026,
            "github_ids": ["new-account"],
        },
    )
    assert response.status_code == 201
    updated = {
        **members[0],
        "github_ids": ["old-account"],
        "confluence_ids": ["cf-old"],
    }
    response = client.put("/api/members/legacy", json=updated)
    assert response.status_code == 200, response.text
    assert response.json() == updated
    updated["github_ids"] = ["new-account"]
    assert client.put("/api/members/legacy", json=updated).status_code == 409


def test_dashboard_assets_are_versioned_and_revalidated(client):
    static = Path("app/static")
    version = hashlib.sha256(
        b"".join(
            (static / name).read_bytes()
            for name in ("app.js", "settings.js", "style.css")
        )
    ).hexdigest()[:16]
    response = client.get("/")
    assert response.headers["cache-control"] == "no-store"
    assert "__ASSET_VERSION__" not in response.text
    for name in ("app.js", "settings.js", "style.css"):
        url = f"/static/{name}?v={version}"
        assert url in response.text
        asset = client.get(url)
        assert asset.status_code == 200
        assert asset.headers["cache-control"] == "no-cache"
    assert client.get("/api/members").headers["cache-control"] == "no-store"
