"""Run every verification workflow and retain logs, JUnit and a source fingerprint."""

import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import playwright

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
ARTIFACTS = ROOT / "artifacts"
LOGS = ARTIFACTS / "verification-logs"
LOGS.mkdir(parents=True, exist_ok=True)
python = sys.executable
node = str(Path(playwright.__file__).parent / "driver" / "node")
steps = [
    ("tests", [python, "-m", "pytest", "-q", "--junitxml=artifacts/full-suite.xml"]),
    ("lint", [python, "-m", "ruff", "check", "app", "tests", "scripts"]),
    ("python-syntax", [python, "-m", "compileall", "-q", "app", "scripts", "tests"]),
    ("app-js", [node, "--check", "app/static/app.js"]),
    ("settings-js", [node, "--check", "app/static/settings.js"]),
    ("start-shell", ["bash", "-n", "start.sh"]),
    ("members-browser", [python, "scripts/browser_members_check.py"]),
    ("drafts-browser", [python, "scripts/browser_drafts_check.py"]),
    ("sources-browser", [python, "scripts/browser_sources_check.py"]),
    ("editing-browser", [python, "scripts/browser_editing_check.py"]),
    ("analysis-browser", [python, "scripts/browser_analysis_check.py"]),
    ("analysis-benchmark", [python, "scripts/benchmark_analysis.py"]),
    ("docker", [python, "scripts/verify_docker.py"]),
    ("documentation", [python, "scripts/verify_docs.py"]),
]
report = {
    "started_at": datetime.now(timezone.utc).isoformat(),
    "steps": [],
    "status": "running",
}
report_path = ARTIFACTS / "full-verification.json"
try:
    for name, command in steps:
        print(f"[{name}] running", flush=True)
        started = time.monotonic()
        with (LOGS / (name + ".log")).open("w") as log:
            result = subprocess.run(
                command,
                stdout=log,
                stderr=subprocess.STDOUT,
                env=os.environ.copy(),
                timeout=900,
            )
        entry = {
            "name": name,
            "command": command,
            "exit_code": result.returncode,
            "seconds": round(time.monotonic() - started, 2),
            "log": "verification-logs/" + name + ".log",
        }
        report["steps"].append(entry)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        if result.returncode:
            print((LOGS / (name + ".log")).read_text()[-12000:], flush=True)
            raise RuntimeError(f"Verification failed: {name}")
        print(f"[{name}] passed ({entry['seconds']} s)", flush=True)
    files = [
        p
        for folder in ("app", "tests", "scripts", "config")
        for p in (ROOT / folder).rglob("*")
        if p.is_file()
        and p.suffix in (".py", ".js", ".css", ".html", ".toml")
        and p.name != "settings.toml"
    ]
    files += [
        ROOT / p
        for p in (
            "Dockerfile",
            "compose.yaml",
            "compose.demo.yaml",
            "start.sh",
            "requirements.txt",
            "requirements-dev.txt",
        )
    ]
    report["source_hashes"] = {
        str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(files)
    }
    report["status"] = "passed"
except BaseException as exc:
    report["status"] = "failed"
    report["error"] = str(exc)
    raise
finally:
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
print(
    "All verification workflows passed. See artifacts/full-verification.json.",
    flush=True,
)
