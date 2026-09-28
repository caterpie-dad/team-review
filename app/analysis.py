"""Conservative, deterministic evidence reduction. Raw evidence is always retained."""

import base64
import difflib
import hashlib
import re
from collections import defaultdict

DEFAULTS = {
    "github_strategy": "adaptive",
    "min_consecutive_commits": 4,
    "max_consecutive_commits": 32,
    "min_savings_ratio": 0.2,
    "max_snapshot_bytes": 128000,
    "max_snapshot_files": 20,
    "cache_enabled": True,
    "cache_max_entries": 2000,
}


def options(cfg):
    result = {**DEFAULTS, **cfg.get("analysis", {})}
    if result["github_strategy"] not in ("adaptive", "diff"):
        raise ValueError("analysis.github_strategy는 adaptive 또는 diff여야 합니다.")
    for key, low, high in (
        ("min_consecutive_commits", 2, 1000),
        ("max_consecutive_commits", 2, 1000),
        ("max_snapshot_bytes", 1024, 1000000),
        ("max_snapshot_files", 1, 100),
        ("cache_max_entries", 1, 100000),
    ):
        if type(result[key]) is not int or not low <= result[key] <= high:
            raise ValueError(f"analysis.{key}: {low}~{high} 정수여야 합니다.")
    if type(result["cache_enabled"]) is not bool:
        raise ValueError("analysis.cache_enabled는 boolean이어야 합니다.")
    if result["max_consecutive_commits"] < result["min_consecutive_commits"]:
        raise ValueError("연속 커밋 최대 개수는 최소 개수 이상이어야 합니다.")
    ratio = result["min_savings_ratio"]
    if type(ratio) not in (int, float) or not 0.05 <= ratio <= 0.9:
        raise ValueError("analysis.min_savings_ratio는 0.05~0.9여야 합니다.")
    return result


def blob_hash(content):
    data = content.encode("utf-8")
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def decode_blob(data, sha, limit):
    if (
        not isinstance(data, dict)
        or data.get("sha") != sha
        or data.get("encoding") != "base64"
        or type(data.get("size")) is not int
        or not 0 <= data["size"] <= limit
    ):
        raise ValueError("snapshot metadata mismatch")
    encoded = data.get("content", "")
    if not isinstance(encoded, str) or len(encoded) > limit * 2:
        raise ValueError("snapshot too large")
    raw = base64.b64decode("".join(encoded.split()), validate=True)
    content = raw.decode("utf-8")
    if len(raw) != data["size"] or "\0" in content or blob_hash(content) != sha:
        raise ValueError("snapshot hash mismatch")
    return content


def reverse_patch(content, patch):
    """Reverse every hunk exactly; reject missing context/counts instead of guessing."""
    lines = content.splitlines(keepends=True)
    hunks, current = [], None
    for line in patch.splitlines():
        match = re.match(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
        if match:
            current = {
                "old_count": int(match[2] or 1),
                "new_count": int(match[4] or 1),
                "position": max(0, int(match[3]) - 1),
                "tokens": [],
            }
            # A zero-length new range denotes an insertion *after* that line.
            if current["new_count"] == 0:
                current["position"] = int(match[3])
            hunks.append(current)
        elif line == "\\ No newline at end of file":
            if not current or not current["tokens"]:
                raise ValueError("invalid newline marker")
            prefix, text = current["tokens"][-1]
            current["tokens"][-1] = (prefix, text.removesuffix("\n"))
        elif current and line and line[0] in " +-":
            current["tokens"].append((line[0], line[1:] + "\n"))
        else:
            raise ValueError("unsupported patch")
    if not hunks:
        raise ValueError("missing hunks")
    rebuilt, cursor = [], 0
    for h in hunks:
        old = [text for prefix, text in h["tokens"] if prefix != "+"]
        new = [text for prefix, text in h["tokens"] if prefix != "-"]
        position = h["position"]
        if (
            len(old) != h["old_count"]
            or len(new) != h["new_count"]
            or position < cursor
            or position > len(lines)
            or lines[position : position + len(new)] != new
        ):
            raise ValueError("patch does not match snapshot")
        rebuilt.extend(lines[cursor:position])
        rebuilt.extend(old)
        cursor = position + len(new)
    rebuilt.extend(lines[cursor:])
    return "".join(rebuilt)


def compact_run(run, fetch_blob, settings):
    files = defaultdict(list)
    for record in run:
        for file in record["files"]:
            if file.get("status") not in (
                "added",
                "modified",
                "removed",
            ) or not file.get("patch"):
                raise ValueError("unsupported or incomplete file")
            files[file["filename"]].append(file)
    if len(files) > settings["max_snapshot_files"]:
        raise ValueError("too many snapshot files")
    sections = []
    for path, changes in files.items():
        last = changes[-1]
        if last["status"] == "removed":
            final = ""
        else:
            sha = last.get("sha", "")
            if not re.fullmatch(r"[a-f0-9]{40}", sha):
                raise ValueError("missing blob sha")
            final = decode_blob(fetch_blob(sha), sha, settings["max_snapshot_bytes"])
        before = final
        for change in reversed(changes):
            if change["status"] != "removed" and blob_hash(before) != change.get("sha"):
                raise ValueError("intermediate snapshot mismatch")
            if change["status"] == "removed" and before:
                raise ValueError("removed file exists")
            before = reverse_patch(before, change["patch"])
            if change["status"] == "added" and before:
                raise ValueError("added file has prior content")
        net = "\n".join(
            difflib.unified_diff(
                before.splitlines(),
                final.splitlines(),
                fromfile="before/" + path,
                tofile="after/" + path,
                lineterm="",
            )
        )
        # Preserve transient additions/deletions (including tests removed later).
        # They are not credited as part of the final artifact.
        represented = set(net.splitlines())
        transient = list(
            dict.fromkeys(
                line
                for change in changes
                for line in change["patch"].splitlines()
                if line.startswith(("+", "-")) and line not in represented
            )
        )
        sections.append(
            f"File: {path}\n순변경 (이 구간의 기여):\n{net or '(최종 순변경 없음)'}"
            f"\n최종 파일 (기존 코드는 문맥일 뿐 개인 기여가 아님):\n{final or '(삭제됨)'}"
            "\n중간 추가/삭제 (중복 제거; 최종 산출물로 간주하지 말 것):\n"
            + "\n".join(transient)
        )
    history = "\n".join(
        f"{r['sha'][:12]} {r['evidence']['date']} {r['evidence']['title']}" for r in run
    )
    return (
        "동일 작성자의 연속 커밋 구간. 수량은 평가 기준이 아니다.\n"
        "파일 해시와 각 역방향 diff를 검증했다. 중간 변경의 순서는 원본 근거에 보존된다.\n"
        "커밋 이력 (메시지는 작성자의 주장):\n"
        + history
        + "\n\n"
        + "\n\n".join(sections)
    )


def consecutive_runs(records, max_commits=32):
    run = []
    for record in reversed(records):
        if not record:
            if run:
                yield run
            run = []
            continue
        if run and (
            record["author"] != run[-1]["author"]
            or record["parents"] != [run[-1]["sha"]]
            or len(run) >= max_commits
        ):
            yield run
            run = []
        run.append(record)
    if run:
        yield run


def select_evidence(evidence):
    """Only suppress raw items when the entire matching packet is available."""
    by_id = {item["id"]: item for item in evidence}
    packets, superseded = [], set()
    for item in evidence:
        meta = item.get("metadata", {})
        if meta.get("kind") != "github_consolidated":
            continue
        sources = meta.get("source_hashes", {})
        if (
            sources
            and not superseded.intersection(sources)
            and all(
                key in by_id
                and by_id[key].get("content_sha256") == digest
                and by_id[key].get("member_id") == item.get("member_id")
                for key, digest in sources.items()
            )
        ):
            packets.append(item)
            superseded.update(sources)
    return [
        item
        for item in evidence
        if item["id"] not in superseded
        and item.get("metadata", {}).get("kind") != "github_consolidated"
    ] + packets
