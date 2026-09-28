import copy
import json
import pytest

from app.analysis import reverse_patch, options, select_evidence, blob_hash
from app.connectors import Collector, Judge, Cancelled, SourceError
from app.db import Database
from app.demo import fixture_evaluation, fixture_members
from app.demo_efficiency import history_fixture
from conftest import seed, wait_done


def efficiency_project(config):
    project = fixture_evaluation(config)["projects"][0]
    project.update(
        weight=100,
        member_ids=["T001", "T002"],
        confluence_urls=[],
        github_urls=[config["github"]["web_url"] + "/demo/efficiency"],
    )
    return project


def test_adaptive_collect_retains_authorship_transient_work_and_raw_evidence(config):
    collector = Collector(config, fixture_members())
    try:
        items, warnings, scope = collector.collect(efficiency_project(config))
        raw = [item for item in items if not item["metadata"].get("kind")]
        selected = select_evidence(items)
        assert len(raw) == 48 and len(selected) == 2
        assert not warnings and scope["repos"] == ["demo/efficiency"]
        assert (
            sum(len(item["content"]) for item in selected)
            < sum(len(item["content"]) for item in raw) * 0.2
        )
        for packet in selected:
            sources = [e for e in raw if e["id"] in packet["metadata"]["source_hashes"]]
            assert len(sources) == 24
            assert {e["member_id"] for e in sources} == {packet["member_id"]}
            assert packet["metadata"]["head_commit"] in {
                e["metadata"]["sha"] for e in sources
            }
        owner_one = next(e for e in selected if e["member_id"] == "T001")
        assert "test_failure_recovery_removed_later" in owner_one["content"]
        assert "기존 코드는 문맥일 뿐" in owner_one["content"]
        owner_two = next(e for e in selected if e["member_id"] == "T002")
        net = (
            owner_two["content"]
            .split("순변경 (이 구간의 기여):", 1)[1]
            .split("최종 파일", 1)[0]
        )
        assert "+def normalize_event" not in net
        # Missing or changed source records invalidate a packet; no stale suppression.
        source_id = next(iter(owner_one["metadata"]["source_hashes"]))
        damaged = [e for e in items if e["id"] != source_id]
        result = select_evidence(damaged)
        assert owner_one["id"] not in {e["id"] for e in result}
        assert len(result) == 24
    finally:
        collector.close()


@pytest.mark.parametrize(
    "change",
    [
        "alternating",
        "coauthor",
        "merge",
        "missing_parent",
        "hash",
        "patch",
        "rename",
        "oversize",
        "file_limit",
        "network",
    ],
)
def test_unsafe_compaction_falls_back(config, change):
    history = history_fixture(8, authors=1)
    if change == "file_limit":
        config["analysis"]["max_snapshot_files"] = 1
    for index, commit in enumerate(history["commits"]):
        if change == "alternating":
            commit["author"]["login"] = f"dev{index % 2 + 1:02}"
        elif change == "coauthor":
            commit["commit"]["message"] += (
                "\nCo-authored-by: Other <other@example.test>"
            )
        elif change == "merge":
            commit["parents"].append({"sha": "b" * 40})
        elif change == "missing_parent":
            commit["parents"] = []
        elif change == "patch":
            commit["files"][0]["patch"] = "@@ -1,1 +1,1 @@\n-old\n+wrong content"
        elif change == "rename":
            commit["files"][0]["status"] = "renamed"
        elif change == "file_limit":
            commit["files"].append({**commit["files"][0], "filename": "src/second.py"})
    if change in ("hash", "oversize"):
        for blob in history["blobs"].values():
            if change == "hash":
                blob["content"] = "dGFtcGVyZWQ="
            else:
                blob["size"] = 10000000
    collector = Collector(config, fixture_members())
    calls = []

    def get(service, path, params=None):
        calls.append(path)
        assert service == "github" and path.startswith("repos/demo/efficiency/")
        if path.endswith("/commits"):
            return history["commits"]
        if "/git/blobs/" in path:
            if change == "network":
                raise SourceError("GitHub snapshot unavailable")
            return history["blobs"][path.rsplit("/", 1)[1]]
        return next(c for c in history["commits"] if c["sha"] == path.rsplit("/", 1)[1])

    collector.get = get
    try:
        items = collector.github(efficiency_project(config))
        assert not any(e["metadata"].get("kind") for e in items)
        assert len(items) == (0 if change == "merge" else 8)
        if change in (
            "alternating",
            "coauthor",
            "merge",
            "missing_parent",
            "file_limit",
        ):
            assert not any("/git/blobs/" in path for path in calls)
    finally:
        collector.close()


def test_reverse_patch_no_newline_add_delete_and_context():
    assert (
        reverse_patch(
            "after",
            "@@ -1 +1 @@\n-before\n\\ No newline at end of file\n+after\n\\ No newline at end of file",
        )
        == "before"
    )
    assert reverse_patch("new\n", "@@ -0,0 +1 @@\n+new") == ""
    assert reverse_patch("", "@@ -1 +0,0 @@\n-old") == "old\n"
    assert reverse_patch("a\nc\n", "@@ -2 +1,0 @@\n-b") == "a\nb\nc\n"
    with pytest.raises(ValueError):
        reverse_patch("wrong\n", "@@ -1 +1 @@\n-old\n+new")
    assert blob_hash("test\n") == "9daeafb9864cf43055ae93beb0afd6c7d144bfa4"


def sample_evidence():
    return [
        {
            "id": "sample",
            "source": "github",
            "title": "code",
            "content": "+def sample_function_with_valid_evidence():\n+    return True\n",
        }
    ]


@pytest.mark.parametrize("change", ["rubric", "scope", "model", "key", "content"])
def test_cache_revalidates_and_invalidates(config, tmp_path, change):
    db = Database(tmp_path / "cache.sqlite3")
    judge = Judge(config, cache=db)
    try:
        evidence = sample_evidence()
        first = judge.judge(evidence, {"member_id": "T001"})
        second = judge.judge(evidence, {"member_id": "T001"})
        assert first["analysis"]["requests"] == 1
        assert (
            second["analysis"]["requests"] == 0
            and second["analysis"]["cache_hits"] == 1
        )
        assert second["dimensions"] == first["dimensions"]
        context = {"member_id": "T001"}
        if change == "rubric":
            judge.rubric = copy.deepcopy(judge.rubric)
            judge.rubric["dimensions"][0]["description"] += " 추가된 기준"
        elif change == "scope":
            context["member_id"] = "T002"
        elif change == "model":
            config["llm"]["model"] = "different-model"
        elif change == "key":
            config["llm"]["key"] = "different-key"
        elif change == "content":
            evidence[0]["content"] += "+# changed content\n"
        assert judge.judge(evidence, context)["analysis"]["requests"] == 1
    finally:
        judge.close()


def test_corrupt_cache_and_explicit_bypass(config, tmp_path):
    db = Database(tmp_path / "cache.sqlite3")
    with db.connect() as conn:
        db.cache_judgment("one", {}, 2)
        db.cache_judgment("two", {}, 2)
        db.cache_judgment("three", {}, 2)
        assert conn.execute("SELECT COUNT(*) FROM judgment_cache").fetchone()[0] == 2
    judge = Judge(config, cache=db)
    try:
        judge.judge(sample_evidence())
        with db.connect() as conn:
            conn.execute(
                "UPDATE judgment_cache SET data=?", (json.dumps({"dimensions": {}}),)
            )
        assert judge.judge(sample_evidence())["analysis"]["requests"] == 1
        bypass = Judge(config, cache=db, bypass_cache=True)
        try:
            assert bypass.judge(sample_evidence())["analysis"]["requests"] == 1
        finally:
            bypass.close()
    finally:
        judge.close()


def test_cancellation_not_swallowed(config):
    collector = Collector(
        config, fixture_members(), check=lambda: (_ for _ in ()).throw(Cancelled())
    )
    try:
        with pytest.raises(Cancelled):
            collector.github(efficiency_project(config))
    finally:
        collector.close()


def test_worker_reuses_identical_analysis_and_force_bypasses_cache(client, config):
    seed(client)
    ev = client.post(
        "/api/evaluations",
        json={
            "title": "효율 검증",
            "year": 2026,
            "projects": [efficiency_project(config)],
        },
    ).json()

    def run(ev, force=False):
        r = client.post(
            f"/api/evaluations/{ev['id']}/start",
            json={"revision": ev["revision"], "force": force, "reason": "전체 재검토"},
        )
        assert r.status_code == 200, r.text
        done = wait_done(client, ev["id"])
        assert done["status"] == "completed", done.get("error")
        return done

    first = run(ev)
    assert len(first["evidence"]["project-1"]) == 50
    assert sum(r["analysis"]["requests"] for r in first["results"]) == 2
    cloned = client.post(f"/api/evaluations/{ev['id']}/clone", json={}).json()
    reused = run(cloned)
    assert sum(r["analysis"]["requests"] for r in reused["results"]) == 0
    assert sum(r["analysis"]["cache_hits"] for r in reused["results"]) == 2
    forced = run(reused, force=True)
    assert sum(r["analysis"]["requests"] for r in forced["results"]) == 2


def test_analysis_settings_validate():
    for changed in (
        {"github_strategy": "unsafe"},
        {"min_savings_ratio": float("nan")},
        {"cache_enabled": "yes"},
        {"max_consecutive_commits": 2},
    ):
        with pytest.raises(ValueError):
            options({"analysis": changed})


def test_provider_usage_is_recorded_as_tokens(config):
    import httpx
    from app.domain import DIMS

    judge = Judge(config)
    judge.client.close()
    evidence = sample_evidence()
    result = {
        "dimensions": {
            key: {
                "score": 75,
                "confidence": 0.8,
                "reason": "원문 근거",
                "citations": [
                    {
                        "evidence_id": "sample",
                        "quote": "sample_function_with_valid_evidence",
                    }
                ],
            }
            for key in DIMS
        },
        "summary": "검증",
        "limitations": "테스트",
    }
    judge.client = httpx.Client(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": json.dumps(result)}}],
                    "usage": {"prompt_tokens": 1234, "completion_tokens": 321},
                },
            )
        )
    )
    try:
        analysis = judge.judge(evidence)["analysis"]
        assert analysis["input_tokens"] == 1234 and analysis["output_tokens"] == 321
        assert analysis["requests"] == 1 and analysis["request_chars"] > 0
    finally:
        judge.close()
