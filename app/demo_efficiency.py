"""Synthetic repeated-edit history for efficiency regression and benchmarking."""

import base64
import difflib
import hashlib
from datetime import date, timedelta
from .analysis import blob_hash


def history_fixture(count=48, authors=2):
    baseline = "".join(
        f"def existing_helper_{i:02}(value):\n    return value  # Existing utility owned before this period.\n\n"
        for i in range(10)
    )
    previous, parent = baseline, "a" * 40
    commits, blobs = [], {}
    for index in range(count):
        author = 1 + min(authors - 1, index * authors // count)
        current = baseline + "def normalize_event(event):\n    checks = []\n"
        current += "".join(
            f'    checks.append(event.get("field_{i:02}", 0) >= {index % 2 + 1})\n'
            for i in range(30)
        )
        current += """    if not all(checks):
        raise ValueError("invalid event")
    return event

def test_normalize_event():
    assert normalize_event(valid_event())
"""
        if 8 <= index <= 12:
            current += "\ndef test_failure_recovery_removed_later():\n    assert recover_failure() == 'recovered'\n"
        sha = hashlib.sha1(f"synthetic-efficiency-commit-{index}".encode()).hexdigest()
        blob = blob_hash(current)
        blobs[blob] = {
            "sha": blob,
            "size": len(current.encode()),
            "encoding": "base64",
            "content": base64.b64encode(current.encode()).decode(),
        }
        patch = "\n".join(
            list(
                difflib.unified_diff(
                    previous.splitlines(), current.splitlines(), n=3, lineterm=""
                )
            )[2:]
        )
        commits.append(
            {
                "sha": sha,
                "parents": [{"sha": parent}],
                "author": {"login": f"dev{author:02}"},
                "commit": {
                    "message": f"입력 검증 정책과 테스트 조정 {index + 1}",
                    "committer": {
                        "date": (date(2026, 1, 1) + timedelta(days=index)).isoformat()
                        + "T10:00:00Z"
                    },
                },
                "files": [
                    {
                        "filename": "src/service.py",
                        "status": "modified",
                        "sha": blob,
                        "patch": patch,
                    }
                ],
            }
        )
        previous, parent = current, sha
    return {"commits": list(reversed(commits)), "blobs": blobs, "baseline": baseline}
