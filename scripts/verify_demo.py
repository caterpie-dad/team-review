"""Evaluate the Docker demo, verify its result, and retain a reviewable export."""

import argparse
import json
import os
import time
from pathlib import Path
import httpx

parser = argparse.ArgumentParser()
parser.add_argument("--url", default="http://127.0.0.1:8080")
args = parser.parse_args()
password = os.getenv("DASHBOARD_PASSWORD")
if not password:
    for line in Path(".env").read_text().splitlines():
        if line.startswith("DASHBOARD_PASSWORD="):
            password = line.split("=", 1)[1]
client = httpx.Client(base_url=args.url, headers={"X-Review-Request": "1"}, timeout=30)
r = client.post("/api/login", json={"password": password})
r.raise_for_status()
meta = client.get("/api/meta").json()
assert meta["demo"], "Only run this verifier on a synthetic demo workspace."
evs = client.get("/api/evaluations").json()
if not evs:
    r = client.post("/api/demo/seed", json={})
    r.raise_for_status()
    ev = r.json()
else:
    ev = client.get("/api/evaluations/" + evs[-1]["id"]).json()
base = "/api/evaluations/" + ev["id"]
if ev["status"] in ("draft", "failed", "cancelled"):
    r = client.post(base + "/start", json={"revision": ev["revision"]})
    r.raise_for_status()
for _ in range(180):
    ev = client.get(base).json()
    if ev["status"] != "running":
        break
    time.sleep(1)
assert ev["status"] in ("completed", "finalized"), ev.get("error")
assert len(ev["members_snapshot"]) == 10
assert len(ev["projects"]) == 6
assert len(ev["results"]) == 30
assert sum(len(es) for es in ev["evidence"].values()) == 59
assert sum(p["weight"] for p in ev["projects"]) == 100
assert any(r["coverage"] < 100 for r in ev["aggregate"])
if ev["status"] == "finalized":
    r = client.post(
        base + "/reopen",
        json={"revision": ev["revision"], "reason": "자동 검증을 위한 조정 재현"},
    )
    r.raise_for_status()
    ev = r.json()
row = ev["aggregate"][0]
result = next(r for r in ev["results"] if r["member_id"] == row["member_id"])
original = result["dimensions"]["quality"]["score"]
if "adjusted_score" in result["dimensions"]["quality"]:
    reset = {
        "revision": ev["revision"],
        "member_id": result["member_id"],
        "project_id": result["project_id"],
        "dimension": "quality",
        "score": original,
        "reason": "자동 검증 시작 전 LLM 원점수로 복원",
        "reset": True,
    }
    r = client.patch(base + "/adjust", json=reset)
    r.raise_for_status()
    ev = r.json()
    row = next(r for r in ev["aggregate"] if r["member_id"] == result["member_id"])
adjustment = {
    "revision": ev["revision"],
    "member_id": result["member_id"],
    "project_id": result["project_id"],
    "dimension": "quality",
    "score": 94,
    "reason": "더미 평가 검증: 근거 재검토 후 품질 점수 조정",
    "comment": "프로젝트 점수와 종합점수 자동 재계산 확인",
}
r = client.patch(base + "/adjust", json=adjustment)
r.raise_for_status()
adjusted = r.json()
changed = next(
    r
    for r in adjusted["results"]
    if r["member_id"] == result["member_id"] and r["project_id"] == result["project_id"]
)
assert changed["dimensions"]["quality"]["score"] == original
assert changed["dimensions"]["quality"]["adjusted_score"] == 94
r = client.get(base + "/export")
r.raise_for_status()
Path("artifacts").mkdir(exist_ok=True)
Path("artifacts/demo-evaluation-2026.json").write_text(
    json.dumps(r.json(), ensure_ascii=False, indent=2)
)
r = client.get(base + "/export?format=csv")
r.raise_for_status()
Path("artifacts/demo-evaluation-2026.csv").write_text(r.text)
summary = {
    "evaluation_id": ev["id"],
    "year": ev["year"],
    "members": 10,
    "projects": 6,
    "results": 30,
    "evidence": 59,
    "warnings": len(ev["warnings"]),
    "mode": "synthetic LLM",
    "adjustment": {
        "original_quality": original,
        "adjusted_quality": 94,
        "before_overall": row["score"],
        "after_overall": next(
            r["score"]
            for r in adjusted["aggregate"]
            if r["member_id"] == row["member_id"]
        ),
    },
    "members_summary": adjusted["aggregate"],
}
Path("artifacts/demo-verification.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2)
)
print(
    json.dumps(
        {k: v for k, v in summary.items() if k != "members_summary"},
        ensure_ascii=False,
        indent=2,
    )
)
client.close()
