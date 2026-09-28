import copy
import threading
from .db import now
from .connectors import Collector, Judge, Cancelled, SourceError


class Worker:
    def __init__(self, db, cfg):
        self.db, self.cfg = db, cfg
        self.threads = {}

    def launch(self, eid):
        thread = threading.Thread(
            target=self.run, args=(eid,), daemon=True, name="evaluation-" + eid
        )
        self.threads[eid] = thread
        thread.start()

    def check(self, eid):
        ev = self.db.get(eid)
        if not ev or ev.get("cancel_requested") or ev["status"] != "running":
            raise Cancelled()

    def update(self, eid, **changes):
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            ev = self.db.get(eid, conn)
            if ev["status"] != "running":
                raise Cancelled()
            ev.update(changes)
            self.db.save(conn, ev)

    def run(self, eid):
        ev = self.db.get(eid)

        def check():
            self.check(eid)

        cfg = self.cfg() if callable(self.cfg) else self.cfg
        collector = Collector(
            cfg,
            ev["members_snapshot"],
            check,
            lambda msg: self.update(eid, message=msg),
        )
        judge = Judge(
            cfg,
            check,
            rubric=ev.get("rubric"),
            cache=self.db,
            bypass_cache=ev.get("bypass_analysis_cache", False),
        )
        evidence = copy.deepcopy(ev.get("evidence", {}))
        scopes = copy.deepcopy(ev.get("scopes", {}))
        warnings = list(ev.get("warnings", []))
        results = copy.deepcopy(ev.get("results", []))
        completed = {
            (r["project_id"], r["member_id"])
            for r in results
            if not r.get("analysis_pending")
        }
        pending = [
            (p, mid)
            for p in ev["projects"]
            for mid in p["member_ids"]
            if (p["id"], mid) not in completed
        ]
        pending_pids = {p["id"] for p, _ in pending}
        try:
            total = len(pending_pids) + len(pending)
            done = 0
            evidence_projects = {}
            for project in ev["projects"]:
                if project["id"] not in pending_pids:
                    continue
                check()
                pid = project["id"]
                items, notes, scope = collector.collect(project)
                for item in items:
                    if item["id"] in evidence_projects:
                        notes.append(
                            f"동일 근거가 다른 프로젝트에도 포함됩니다: {item['external_id']}. 프로젝트 구분과 가중치를 검토하세요."
                        )
                    evidence_projects[item["id"]] = pid
                # Retained judgments still cite their original content snapshot.
                retained_mids = {
                    mid for project_id, mid in completed if project_id == pid
                }
                old_items = [
                    item
                    for item in evidence.get(pid, [])
                    if item["member_id"] in retained_mids
                ]
                merged = {item["id"]: item for item in items}
                merged.update({item["id"]: item for item in old_items})
                evidence[pid], scopes[pid] = list(merged.values()), scope
                warnings.extend(f"{project['name']}: {note}" for note in notes)
                warnings = list(dict.fromkeys(warnings))
                done += 1
                self.update(
                    eid,
                    evidence=evidence,
                    scopes=scopes,
                    warnings=warnings,
                    progress=round(done / max(total, 1) * 100),
                    message=f"{project['name']} · 수집 완료",
                )
            for project, mid in pending:
                check()
                self.update(eid, message=f"{project['name']} · {mid} 근거 기반 분석")
                own = [e for e in evidence[project["id"]] if e["member_id"] == mid]
                member = next(m for m in ev["members_snapshot"] if m["id"] == mid)
                result = judge.judge(
                    own,
                    cache_context={
                        "project_scope": {
                            k: project[k]
                            for k in ("start", "end", "github_urls", "confluence_urls")
                        },
                        "member_id": mid,
                        "github_ids": member.get("github_ids", []),
                        "confluence_ids": member.get("confluence_ids", []),
                    },
                )
                result.update(
                    model=cfg["llm"]["model"],
                    rubric_version=ev["rubric"]["version"],
                    analyzed_at=now(),
                )
                override = next(
                    (
                        r
                        for r in ev.get("reanalysis_overrides", [])
                        if r["project_id"] == project["id"] and r["member_id"] == mid
                    ),
                    None,
                )
                if override:
                    for key, dimension in result["dimensions"].items():
                        for field in (
                            "adjusted_score",
                            "adjustment_reason",
                            "reviewer_comment",
                        ):
                            if field in override["dimensions"][key]:
                                dimension[field] = override["dimensions"][key][field]
                    for field in ("adjusted_summary", "adjusted_limitations"):
                        if field in override:
                            result[field] = override[field]
                results.append(
                    {**result, "project_id": project["id"], "member_id": mid}
                )
                done += 1
                self.update(
                    eid,
                    results=results,
                    progress=min(99, round(done / max(total, 1) * 100)),
                )
            check()
            with self.db.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                current = self.db.get(eid, conn)
                if current.get("cancel_requested"):
                    raise Cancelled()
                current.update(
                    needs_analysis=False,
                    status="completed",
                    progress=100,
                    message="평가 완료 · 결과를 검토하고 확정하세요.",
                    finished_at=now(),
                )
                current.pop("reanalysis_overrides", None)
                self.db.save(conn, current)
                self.db.audit(
                    conn,
                    eid,
                    "completed",
                    {
                        "results": len(results),
                        "evidence_count": sum(map(len, evidence.values())),
                        "warnings": len(warnings),
                    },
                )
        except Exception as exc:
            with self.db.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                current = self.db.get(eid, conn)
                if current and current["status"] == "running":
                    cancelled = isinstance(exc, Cancelled)
                    error = (
                        "평가자가 실행을 취소했습니다."
                        if cancelled
                        else str(exc)
                        if isinstance(exc, SourceError)
                        else "처리 중 오류가 발생했습니다. 연계 데이터 형식과 설정을 확인하세요."
                    )
                    current.update(
                        status="cancelled" if cancelled else "failed",
                        error=error,
                        message=error,
                        finished_at=now(),
                    )
                    self.db.save(conn, current)
                    self.db.audit(conn, eid, current["status"], {"error": error})
        finally:
            collector.close()
            judge.close()
            self.threads.pop(eid, None)
