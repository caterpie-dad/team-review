"""Run the full Docker scenario in disposable projects; never touch existing volumes."""

import json
import os
import secrets
import socket
import sqlite3
import subprocess
import sys
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
ARTIFACTS = ROOT / "artifacts"
ARTIFACTS.mkdir(exist_ok=True)
PYTHON = sys.executable


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def run(command, env, **kwargs):
    subprocess.run(command, env=env, check=True, **kwargs)


@contextmanager
def login(url, password):
    with httpx.Client(
        base_url=url, headers={"X-Review-Request": "1"}, timeout=30
    ) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/api/members").status_code == 401
        client.post("/api/login", json={"password": password}).raise_for_status()
        yield client


with tempfile.TemporaryDirectory(prefix="trace-verification-") as directory:
    temp = Path(directory)
    # Mounts must be readable by the image's unprivileged UID.
    temp.chmod(0o755)
    suffix = uuid.uuid4().hex[:10]
    demo_project, prod_project = "trace-check-" + suffix, "trace-restore-" + suffix
    image = "trace-team-review:verify-" + suffix
    password = secrets.token_urlsafe(32)
    env = {
        **os.environ,
        "DASHBOARD_PASSWORD": password,
        "PORT": str(port()),
        "BIND_HOST": "127.0.0.1",
        "GITHUB_TOKEN": "",
        "CONFLUENCE_USERNAME": "",
        "CONFLUENCE_TOKEN": "",
        "LLM_API_KEY": "",
    }
    override = temp / "demo.json"
    override.write_text(
        json.dumps(
            {"services": {"dashboard": {"image": image}, "fixtures": {"image": image}}}
        )
    )
    demo = [
        "docker",
        "compose",
        "-p",
        demo_project,
        "-f",
        "compose.demo.yaml",
        "-f",
        str(override),
    ]
    prod = None
    prod_env = None
    try:
        run([*demo, "up", "-d", "--build", "--wait"], env)
        url = "http://127.0.0.1:" + env["PORT"]
        run([PYTHON, "scripts/verify_demo.py", "--url", url], env)
        run([PYTHON, "scripts/browser_check.py"], {**env, "REVIEW_URL": url})
        with login(url, password) as client:
            settings = client.get("/api/settings").json()
            body = {
                key: value
                for key, value in settings["services"]["github"].items()
                if key != "secret_set"
            }
            body.update(
                revision=settings["revision"], secret="isolated-persistence-token"
            )
            saved = client.put("/api/settings/services/github", json=body)
            saved.raise_for_status()
            settings = saved.json()
            assert "isolated-persistence-token" not in saved.text
            evs = client.get("/api/evaluations").json()
            before = {
                e["id"]: client.get("/api/evaluations/" + e["id"]).json() for e in evs
            }
        run([*demo, "up", "-d", "--force-recreate", "--wait"], env)
        with login(url, password) as client:
            assert client.get("/api/settings").json() == settings
            for eid, data in before.items():
                assert client.get("/api/evaluations/" + eid).json() == data
        run(
            [
                *demo,
                "exec",
                "-T",
                "dashboard",
                "python",
                "scripts/backup.py",
                "/data/verification.sqlite3",
            ],
            env,
        )
        run(
            [
                *demo,
                "cp",
                "dashboard:/data/verification.sqlite3",
                str(temp / "review.sqlite3"),
            ],
            env,
        )
        run(
            [
                *demo,
                "cp",
                "dashboard:/data/review-settings.json",
                str(temp / "review-settings.json"),
            ],
            env,
        )
        with sqlite3.connect(temp / "review.sqlite3") as db:
            assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert db.execute("SELECT COUNT(*) FROM judgment_cache").fetchone()[0] > 0
            assert db.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0] == len(
                before
            )
        # Exercise the operating Compose file with restored demo data and mock services.
        prod_cfg = temp / "settings.toml"
        prod_cfg.write_text(
            (ROOT / "config/demo.toml")
            .read_text()
            .replace("demo = true", "demo = false", 1)
        )
        prod_override = temp / "production.json"
        prod_override.write_text(
            json.dumps(
                {
                    "services": {
                        "dashboard": {
                            "image": image,
                            "volumes": [str(prod_cfg) + ":/config/settings.toml:ro"],
                        }
                    },
                    "networks": {
                        "default": {"external": True, "name": demo_project + "_default"}
                    },
                }
            )
        )
        prod = [
            "docker",
            "compose",
            "-p",
            prod_project,
            "-f",
            "compose.yaml",
            "-f",
            str(prod_override),
        ]
        prod_env = {**env, "PORT": str(port())}
        volume = prod_project + "_review-data"
        run(["docker", "volume", "create", volume], env)
        # Same restore procedure as README, applied only to this new test volume.
        restore = """set -eu
archive_dir="/data/before-restore-$(date +%s)"
mkdir "$archive_dir"
for name in review.sqlite3 review.sqlite3-wal review.sqlite3-shm review-settings.json; do
  if [ -f "/data/$name" ]; then mv "/data/$name" "$archive_dir/"; fi
done
cp /restore/review.sqlite3 /data/review.sqlite3
if [ -f /restore/review-settings.json ]; then cp /restore/review-settings.json /data/; fi
chown -R 10001:10001 /data
chmod 600 /data/review.sqlite3
if [ -f /data/review-settings.json ]; then chmod 600 /data/review-settings.json; fi
"""
        run(
            [
                "docker",
                "run",
                "--rm",
                "--user",
                "0",
                "--entrypoint",
                "sh",
                "-v",
                volume + ":/data",
                "-v",
                str(temp) + ":/restore:ro",
                image,
                "-c",
                restore,
            ],
            env,
        )
        run([*prod, "up", "-d", "--no-build", "--wait"], prod_env)
        prod_url = "http://127.0.0.1:" + prod_env["PORT"]
        with login(prod_url, password) as client:
            assert client.get("/api/meta").json()["demo"] is False
            assert client.post("/api/demo/seed", json={}).status_code == 403
            assert client.get("/api/settings").json() == settings
            for eid, data in before.items():
                assert client.get("/api/evaluations/" + eid).json() == data
            for kind, service in settings["services"].items():
                body = {k: v for k, v in service.items() if k != "secret_set"}
                body["revision"] = settings["revision"]
                response = client.post(
                    "/api/settings/services/" + kind + "/test", json=body
                )
                response.raise_for_status()
                assert response.json()["ok"], kind
        report = {
            "mode": "isolated Docker projects with synthetic data",
            "members": 10,
            "projects": 6,
            "checks": [
                "image build and health",
                "password and anonymous denial",
                "full evaluation and adjustment/export",
                "desktop/mobile end-to-end",
                "container recreation preserves evaluations and settings",
                "consistent SQLite backup and integrity",
                "judgment cache included in backup",
                "database and settings restore to a new volume",
                "production Compose starts without seed capability",
                "three connections work after restore",
            ],
            "existing_workspace_modified": False,
        }
        (ARTIFACTS / "docker-verification.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2)
        )
        print(
            "Docker evaluation, persistence, backup/restore and production-mode checks passed.",
            flush=True,
        )
    finally:
        if prod is not None:
            subprocess.run([*prod, "down", "-v"], env=prod_env or env, check=False)
        subprocess.run([*demo, "down", "-v"], env=env, check=False)
        subprocess.run(["docker", "image", "rm", image], env=env, check=False)
