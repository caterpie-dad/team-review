import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS members (id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS evaluations (id TEXT PRIMARY KEY, status TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0, data TEXT NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS one_running ON evaluations(status) WHERE status='running';
                CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY AUTOINCREMENT, evaluation_id TEXT, at TEXT NOT NULL, action TEXT NOT NULL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS login_attempts (ip TEXT PRIMARY KEY, count INTEGER NOT NULL, until REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS judgment_cache (key TEXT PRIMARY KEY, data TEXT NOT NULL, created_at TEXT NOT NULL);

            """)
            if db.execute("PRAGMA user_version").fetchone()[0] < 2:

                def retire_email(value):
                    if isinstance(value, dict):
                        return {
                            k: retire_email(v)
                            for k, v in value.items()
                            if k != "git_emails"
                        }
                    if isinstance(value, list):
                        return [retire_email(v) for v in value]
                    return value

                for table in ("members", "evaluations", "audit"):
                    for record in db.execute(
                        f"SELECT id, data FROM {table}"
                    ).fetchall():
                        data = retire_email(json.loads(record["data"]))
                        db.execute(
                            f"UPDATE {table} SET data=? WHERE id=?",
                            (json.dumps(data, ensure_ascii=False), record["id"]),
                        )
                db.execute("PRAGMA user_version=2")
            for row in db.execute(
                "SELECT * FROM evaluations WHERE status='running'"
            ).fetchall():
                ev = json.loads(row["data"])
                ev.update(
                    status="failed",
                    error="서버 재시작으로 실행이 중단되었습니다. 재시도할 수 있습니다.",
                    updated_at=now(),
                )
                self.save(db, ev)
                self.audit(db, ev["id"], "interrupted", {})

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def get(self, eid, db=None):
        if db is None:
            with self.connect() as conn:
                return self.get(eid, conn)
        row = db.execute("SELECT data FROM evaluations WHERE id=?", (eid,)).fetchone()
        return json.loads(row["data"]) if row else None

    def members(self, db=None):
        if db is None:
            with self.connect() as conn:
                return self.members(conn)
        members = [
            json.loads(r["data"])
            for r in db.execute("SELECT data FROM members ORDER BY id")
        ]
        # Older records may omit optional account lists. Normalize reads without
        # rewriting original records or historical evaluation snapshots.
        for member in members:
            for key in ("github_ids", "confluence_ids"):
                if member.get(key) is None:
                    member[key] = []
        return members

    def save(self, db, ev):
        ev["revision"] = ev.get("revision", 0) + 1
        ev["updated_at"] = now()
        db.execute(
            "INSERT INTO evaluations(id,status,revision,data) VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,revision=excluded.revision,data=excluded.data",
            (
                ev["id"],
                ev["status"],
                ev["revision"],
                json.dumps(ev, ensure_ascii=False),
            ),
        )

    def audit(self, db, eid, action, data):
        db.execute(
            "INSERT INTO audit(evaluation_id,at,action,data) VALUES(?,?,?,?)",
            (eid, now(), action, json.dumps(data, ensure_ascii=False)),
        )

    def cached_judgment(self, key):
        with self.connect() as conn:
            row = conn.execute(
                "SELECT data FROM judgment_cache WHERE key=?", (key,)
            ).fetchone()
        try:
            return json.loads(row[0]) if row else None
        except (ValueError, TypeError):
            return None

    def cache_judgment(self, key, judgment, limit):
        with self.connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO judgment_cache VALUES(?,?,?)",
                (key, json.dumps(judgment, ensure_ascii=False), now()),
            )
            conn.execute(
                "DELETE FROM judgment_cache WHERE key IN (SELECT key FROM judgment_cache ORDER BY created_at DESC, key LIMIT -1 OFFSET ?)",
                (limit,),
            )
