"""Validate the operation/demo documentation split and local documentation links."""

import re
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
readme = (root / "README.md").read_text()
for forbidden in (
    "./start.sh demo",
    "trace-demo",
    "compose.demo.yaml",
    "scripts/verify_demo.py",
    "scripts/benchmark_analysis.py",
    ".venv/",
    "2026년 데모 불러오기",
):
    assert forbidden not in readme, (
        f"Operational README contains demo content: {forbidden}"
    )
for required in (
    "./start.sh production",
    "trace-production",
    "config/settings.toml",
    "DASHBOARD_PASSWORD",
    "review-settings.json",
    "scripts/backup.py",
    "복원",
):
    assert required in readme, f"Missing operational instruction: {required}"
for path in [root / "README.md", *sorted((root / "docs").glob("*.md"))]:
    for target in re.findall(r"\]\(([^)]+)\)", path.read_text()):
        if re.match(r"https?://|mailto:|#", target):
            continue
        local = target.split("#", 1)[0]
        assert (path.parent / local).exists(), f"Broken link in {path.name}: {target}"
demo = (root / "docs/demo.md").read_text()
for required in (
    "./start.sh demo",
    "10명",
    "6개",
    "scripts/verify_all.py",
    "scripts/dev.py",
):
    assert required in demo, f"Missing demo guide: {required}"
print(
    "Operational README, separate demo guide and all local documentation links passed."
)
subprocess.run([sys.executable, str(root / "scripts/verify_startup.py")], check=True)
