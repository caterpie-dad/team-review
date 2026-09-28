"""Evaluation corrections preserve compatible judgments and explicitly invalidate the rest."""

import copy
from .domain import DIMS, RUBRIC, career_at

CONFIG_FIELDS = ("title", "year", "projects")


def rubric_meaning(rubric):
    return (
        rubric.get("anchors"),
        sorted((d["id"], d["description"]) for d in rubric["dimensions"]),
    )


def source_scope(project):
    return (
        project["start"],
        project["end"],
        sorted(project["github_urls"]),
        sorted(project["confluence_urls"]),
    )


def identity(member):
    return (
        sorted(member.get("github_ids", [])),
        sorted(member.get("confluence_ids", [])),
    )


def pending_result(pid, mid):
    return {
        "project_id": pid,
        "member_id": mid,
        "analysis_pending": True,
        "dimensions": {
            key: {
                "score": None,
                "reason": "설정 변경으로 재분석이 필요합니다.",
                "confidence": 0,
                "citations": [],
            }
            for key in DIMS
        },
        "summary": "재분석 필요",
        "limitations": "수정된 설정에 맞는 새 판정이 아직 없습니다.",
    }


def revise_evaluation(ev, body, members, rubric, policy):
    old = copy.deepcopy(ev)
    projects = [p.model_dump(mode="json") for p in body.projects]
    mids = {m for p in projects for m in p["member_ids"]}
    old_members = {m["id"]: m for m in ev.get("members_snapshot", [])}
    current_members = {m["id"]: m for m in members}
    snapshot = []
    for mid in sorted(mids):
        member = (
            current_members[mid]
            if body.refresh_members or mid not in old_members
            else old_members[mid]
        )
        if policy:
            career_at(member, body.year)
        snapshot.append(copy.deepcopy(member))
    for key in ("github_ids", "confluence_ids"):
        assigned = [account for member in snapshot for account in member[key]]
        if len(assigned) != len(set(assigned)):
            raise ValueError(
                "과거/현재 팀원 계정이 충돌합니다. 최신 팀원 정보 반영을 선택하세요."
            )
    old_projects = {p["id"]: p for p in ev["projects"]}
    old_results = {(r["project_id"], r["member_id"]): r for r in ev.get("results", [])}
    changed_members = {
        m["id"]
        for m in snapshot
        if m["id"] not in old_members or identity(m) != identity(old_members[m["id"]])
    }
    changed_scope = {
        p["id"]
        for p in projects
        if p["id"] not in old_projects
        or source_scope(p) != source_scope(old_projects[p["id"]])
    }
    meaning_changed = rubric_meaning(rubric) != rubric_meaning(ev.get("rubric", RUBRIC))
    results = []
    for project in projects:
        for mid in project["member_ids"]:
            previous = old_results.get((project["id"], mid))
            valid = (
                previous
                and not previous.get("analysis_pending")
                and project["id"] not in changed_scope
                and mid not in changed_members
                and not meaning_changed
            )
            results.append(
                copy.deepcopy(previous) if valid else pending_result(project["id"], mid)
            )
    ev.update(
        title=body.title,
        year=body.year,
        projects=projects,
        rubric=copy.deepcopy(rubric),
        members_snapshot=snapshot,
        results=results,
        status="completed",
        needs_analysis=any(r.get("analysis_pending") for r in results),
        finalized_at=None,
        finalization_reason=None,
    )
    if policy:
        ev["level_policy"] = copy.deepcopy(policy)
    active_pids = {p["id"] for p in projects}
    # Retain only evidence in the current declared scope. Prior copies live in the audit version.
    for key in ("evidence", "scopes"):
        ev[key] = {
            pid: data
            for pid, data in ev.get(key, {}).items()
            if pid in active_pids and pid not in changed_scope
        }
    ev["message"] = (
        "설정 변경 · 재분석이 필요한 항목이 있습니다."
        if ev["needs_analysis"]
        else "편집 반영 · 점수를 다시 계산했습니다."
    )
    return old
