import json

import httpx

from conftest import seed, wait_done


def test_all_project_sources_reach_llm_without_duplicate_evidence(client, monkeypatch):
    ev = seed(client)
    project = ev["projects"][0]
    second = ev["projects"][1]
    project["github_urls"] += second["github_urls"] + project["github_urls"]
    project["confluence_urls"] += second["confluence_urls"] + [
        "https://confluence.demo.test/wiki/spaces/DEMO/pages/1003"
    ]
    project["member_ids"] = [m["id"] for m in client.get("/api/members").json()]
    project["weight"] = 100
    base = "/api/evaluations/" + ev["id"]
    saved = client.put(
        base,
        json={
            "title": ev["title"],
            "year": ev["year"],
            "projects": [project],
            "revision": ev["revision"],
        },
    )
    assert saved.status_code == 200, saved.text
    sent_ids = []
    original = httpx.Client.send

    def capture(self, request, *args, **kwargs):
        if request.url.path == "/llm/chat/completions":
            payload = json.loads(request.content)
            evidence = json.loads(payload["messages"][1]["content"])["evidence"]
            sent_ids.extend(e["id"] for e in evidence)
        return original(self, request, *args, **kwargs)

    monkeypatch.setattr(httpx.Client, "send", capture)
    assert (
        client.post(
            base + "/start", json={"revision": saved.json()["revision"]}
        ).status_code
        == 200
    )
    done = wait_done(client, ev["id"])
    assert done["status"] == "completed", done.get("error")
    scope = done["scopes"][project["id"]]
    assert scope["repos"] == ["demo/project-1", "demo/project-2"]
    assert {"1000", "1100", "1003"} <= set(scope["confluence_pages"])
    assert len(scope["confluence_pages"]) == 12
    evidence = done["evidence"][project["id"]]
    assert (
        len(evidence) == 21
    )  # 11 commits including one unmapped author, 10 documents.
    assert len({e["id"] for e in evidence}) == len(evidence)
    mapped = {e["id"] for e in evidence if e["member_id"]}
    assert set(sent_ids) == mapped
    assert len(sent_ids) == len(mapped)
