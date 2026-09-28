"""Compare full-diff and adaptive evaluation using isolated synthetic HTTP services."""

import json
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn
from app.config import load_settings
from app.demo import create_demo_app, fixture_members
from app.connectors import Collector, Judge
from app.db import Database


def serve(app):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(
        target=server.run, kwargs={"sockets": [sock]}, daemon=True
    )
    thread.start()
    for _ in range(100):
        if server.started:
            return server, thread, f"http://127.0.0.1:{port}"
        time.sleep(0.05)
    raise RuntimeError("Test server startup failed")


os.environ["DASHBOARD_PASSWORD"] = "benchmark-local-password!"
reports = []
for authors in (1, 2):
    mock_server, mock_thread, mock_url = serve(
        create_demo_app(efficiency_authors=authors)
    )
    try:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = load_settings("config/demo.toml")
            for kind in ("github", "confluence", "llm"):
                cfg[kind]["api_url"] = mock_url + "/" + kind
            members = fixture_members()[:authors]
            project = {
                "id": "efficiency",
                "name": "반복 수정 검증",
                "weight": 100,
                "start": "2026-01-01",
                "end": "2026-12-31",
                "member_ids": [m["id"] for m in members],
                "github_urls": [cfg["github"]["web_url"] + "/demo/efficiency"],
                "confluence_urls": [],
            }
            scenarios = {}
            for strategy in ("diff", "adaptive"):
                cfg["analysis"]["github_strategy"] = strategy
                collector = Collector(cfg, members)
                started = time.monotonic()
                try:
                    evidence, warnings, scope = collector.collect(project)
                finally:
                    collector.close()
                db = Database(Path(tmp) / (strategy + ".sqlite3"))
                judge = Judge(cfg, cache=db)
                try:
                    results = [
                        judge.judge(
                            [e for e in evidence if e["member_id"] == m["id"]],
                            {"member_id": m["id"]},
                        )
                        for m in members
                    ]
                    elapsed = round(time.monotonic() - started, 3)
                    cached = [
                        judge.judge(
                            [e for e in evidence if e["member_id"] == m["id"]],
                            {"member_id": m["id"]},
                        )
                        for m in members
                    ]
                finally:
                    judge.close()
                metrics = [r["analysis"] for r in results]
                scenarios[strategy] = {
                    "retained_evidence": len(evidence),
                    "warnings": warnings,
                    "original_content_chars": sum(a["original_chars"] for a in metrics),
                    "selected_content_chars": sum(a["sent_chars"] for a in metrics),
                    "llm_requests": sum(a["requests"] for a in metrics),
                    "llm_request_chars_including_prompts": sum(
                        a["request_chars"] for a in metrics
                    ),
                    "consolidated_groups": sum(
                        a["consolidated_groups"] for a in metrics
                    ),
                    "repeat_llm_requests": sum(
                        r["analysis"]["requests"] for r in cached
                    ),
                    "repeat_cache_hits": sum(
                        r["analysis"]["cache_hits"] for r in cached
                    ),
                    "elapsed_seconds_mock_services": elapsed,
                    "actual_tokens": None,
                }
            assert (
                scenarios["adaptive"]["selected_content_chars"]
                < scenarios["diff"]["selected_content_chars"] * 0.2
            )
            assert scenarios["adaptive"]["repeat_llm_requests"] == 0
            reports.append(
                {
                    "commits": 48,
                    "authors": authors,
                    "scenarios": scenarios,
                    "quality_validation": "Not measured by the synthetic LLM; compare real-model judgments before operational scoring.",
                    "content_reduction_percent": round(
                        100
                        * (
                            1
                            - scenarios["adaptive"]["selected_content_chars"]
                            / scenarios["diff"]["selected_content_chars"]
                        ),
                        1,
                    ),
                }
            )
    finally:
        mock_server.should_exit = True
        mock_thread.join(timeout=10)
report = {
    "mode": "synthetic HTTP services; no real model quality or latency claim",
    "token_note": "Mock API does not report token usage. Counts are characters, not tokens.",
    "scenarios": reports,
}
Path("artifacts").mkdir(exist_ok=True)
Path("artifacts/analysis-benchmark.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2)
)
print(json.dumps(report, ensure_ascii=False, indent=2))
